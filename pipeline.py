# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27", "numpy>=1.26", "scipy>=1.11", "pymorphy3>=2.0"]
# ///
"""
Пайплайн кластеризации одного проекта Semantika Web (мультивариантный).

Вход:  <project_dir>/keys.csv (Запрос;Базовая;Точная;Очень точная;Топоним;Вопрос)
Выход: <project_dir>/data/{queries.json, meta.json, {variant}_{hard|soft|avg}.bin}
       <project_dir>/{result.csv, report.md, emb_*.npy, costs.json, status.json}

Варианты эмбеддингов (--variants openai,gemini,...):
  openai      OpenAI text-embedding-3-large 3072d           (базовый)
  gemini      Gemini-2, taskType=CLUSTERING 3072d           (базовый)
  gem_sim     Gemini-2, taskType=SEMANTIC_SIMILARITY
  gem_query   Gemini-2, taskType=RETRIEVAL_QUERY
  lemma       OpenAI по лемматизированному тексту
  intent      OpenAI по фразе с интент-префиксом
  ensemble    склейка нормированных openai+gemini            (бесплатно)
  openai1536  обрезка openai до 1536d (матрёшка)             (бесплатно)
  gemini768   обрезка gemini до 768d                         (бесплатно)

Режимы: полный и --label-only (DeepSeek-разметка интентов, дописывает result.csv).
"""

import asyncio
import csv
import json
import re
import os
import struct
import sys
import time
from functools import lru_cache
from pathlib import Path

import httpx
import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

PDIR = Path(sys.argv[1])
LABEL_ONLY = "--label-only" in sys.argv


def cli_target_geo() -> str:
    if "--target-geo" in sys.argv:
        i = sys.argv.index("--target-geo")
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1].strip().lower()
    return ""


def cli_variants() -> list[str]:
    if "--variants" in sys.argv:
        raw = sys.argv[sys.argv.index("--variants") + 1]
        return [v for v in raw.split(",") if v]
    return ["openai", "gemini"]


PRICE = {"openai": 0.13, "gemini": 0.15, "voyage": 0.18, "qwen": 0.07,
         "ds_in": 0.28, "ds_out": 0.42}  # $/1M токенов
USD_RUB = 80.0
P_FINE, P_COARSE = 15, 75
DEDUP_SIM_SURE, DEDUP_SIM_FLOOR = 0.97, 0.85
METHODS = {"hard": "complete", "soft": "single", "avg": "average"}

INTENT_PREFIX = "намерение пользователя, который ищет: "

TITLES = {
    "openai": "OpenAI · 3-large",
    "gemini": "Gemini · CLUSTERING",
    "gem_sim": "Gemini · SEM_SIMILARITY",
    "gem_query": "Gemini · RETRIEVAL_QUERY",
    "ensemble": "Ансамбль OpenAI+Gemini",
    "openai1536": "OpenAI · 1536d",
    "gemini768": "Gemini · 768d",
    "lemma": "OpenAI · леммы",
    "intent": "OpenAI · интент-префикс",
    "voyage": "Voyage · 3-large",
    "qwen": "Qwen3 · text-embedding-v4",
}
# производные: (вид, параметры)
DERIVED = {
    "ensemble": ("concat", ["openai", "gemini"]),
    "openai1536": ("trunc", "openai", 1536),
    "gemini768": ("trunc", "gemini", 768),
}
GEMINI_TASK = {"gemini": "CLUSTERING", "gem_sim": "SEMANTIC_SIMILARITY",
               "gem_query": "RETRIEVAL_QUERY"}
# OpenAI-совместимые сторонние провайдеры: (url, env-ключ, модель, батч, доп. параметры, вендор)
COMPAT_API = {
    "voyage": ("https://api.voyageai.com/v1/embeddings", "VOYAGE_API_KEY",
               "voyage-3-large", 400, {"output_dimension": 2048}, "voyage"),
    "qwen": ("https://dashscope-intl.aliyuncs.com/compatible-mode/v1/embeddings",
             "DASHSCOPE_API_KEY", "text-embedding-v4", 10, {"dimensions": 2048}, "qwen"),
}

_status: dict = {"stage": "", "pct": 0, "done": False}


def set_status(stage: str, pct: int, **kw) -> None:
    _status.update({"stage": stage, "pct": pct})
    _status.update(kw)
    (PDIR / "status.json").write_text(json.dumps(_status, ensure_ascii=False))


async def retry_post(client, url, sem, attempts=6, **kw):
    async with sem:
        for i in range(attempts):
            try:
                r = await client.post(url, **kw)
                if r.status_code == 429 or r.status_code >= 500:
                    raise httpx.HTTPStatusError("retry", request=r.request, response=r)
                r.raise_for_status()
                return r
            except (httpx.HTTPStatusError, httpx.TransportError) as e:
                if i == attempts - 1:
                    raise
                delay = min(60, 2 ** i)
                resp = getattr(e, "response", None)
                if resp is not None and resp.headers.get("retry-after"):
                    try:
                        delay = max(delay, float(resp.headers["retry-after"]) + 1)
                    except ValueError:
                        pass
                await asyncio.sleep(delay)

# ---------------- эмбеддинги ----------------

async def embed_openai(texts, key):
    # лимиты аккаунта: 10M TPM / 10K RPM — упираемся не в них, а в размер батча
    sem = asyncio.Semaphore(32)
    tokens = 0
    out = [None] * ((len(texts) + 2047) // 2048)

    async def one(bi, batch, client):
        nonlocal tokens
        r = await retry_post(client, "https://api.openai.com/v1/embeddings", sem,
                             headers={"Authorization": f"Bearer {key}"},
                             json={"model": "text-embedding-3-large", "input": batch})
        d = r.json()
        tokens += d["usage"]["prompt_tokens"]
        out[bi] = np.array([e["embedding"] for e in d["data"]], dtype=np.float32)

    async with httpx.AsyncClient(timeout=180) as client:
        await asyncio.gather(*(one(bi, texts[i:i + 2048], client)
                               for bi, i in enumerate(range(0, len(texts), 2048))))
    return np.vstack(out), tokens


async def embed_gemini(texts, key, task):
    url = ("https://generativelanguage.googleapis.com/v1beta/"
           f"models/gemini-embedding-2:batchEmbedContents?key={key}")
    sem = asyncio.Semaphore(16)
    out = [None] * ((len(texts) + 99) // 100)

    async def one(bi, batch, client):
        body = {"requests": [{"model": "models/gemini-embedding-2",
                              "content": {"parts": [{"text": q}]},
                              "taskType": task} for q in batch]}
        # ponytail: acquire один раз на вызов; редкие 429-ретраи внутри не учитываются
        await _gem_rl.acquire(len(batch))
        r = await retry_post(client, url, sem, attempts=8, json=body)
        out[bi] = np.array([e["values"] for e in r.json()["embeddings"]], dtype=np.float32)

    async with httpx.AsyncClient(timeout=180) as client:
        await asyncio.gather(*(one(bi, texts[i:i + 100], client)
                               for bi, i in enumerate(range(0, len(texts), 100))))
    return np.vstack(out), sum(len(q) for q in texts) // 4


class RateLimiter:
    """Скользящее минутное окно: не даёт пробить RPM-квоту, вместо ловли 429."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.used = 0
        self.win = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self, n: int) -> None:
        while True:
            async with self.lock:
                now = time.monotonic()
                if now - self.win >= 60:
                    self.win, self.used = now, 0
                if self.used + n <= self.per_minute:
                    self.used += n
                    return
                wait = 60 - (now - self.win)
            await asyncio.sleep(wait + 0.2)


# лимит аккаунта 20K RPM; берём 75%, поднять — env GEMINI_RPM
_gem_rl = RateLimiter(int(os.environ.get("GEMINI_RPM", "15000")))


async def embed_compat(texts, url, key, model, batch, extra):
    """OpenAI-совместимый /embeddings (Voyage, DashScope/Qwen и т.п.)."""
    sem = asyncio.Semaphore(32)
    tokens = 0
    out = [None] * ((len(texts) + batch - 1) // batch)

    async def one(bi, chunk, client):
        nonlocal tokens
        r = await retry_post(client, url, sem, attempts=8,
                             headers={"Authorization": f"Bearer {key}"},
                             json={"model": model, "input": chunk, **extra})
        d = r.json()
        u = d.get("usage") or {}
        tokens += u.get("total_tokens") or u.get("prompt_tokens") or 0
        out[bi] = np.array([e["embedding"] for e in d["data"]], dtype=np.float32)

    async with httpx.AsyncClient(timeout=180) as client:
        await asyncio.gather(*(one(bi, texts[i:i + batch], client)
                               for bi, i in enumerate(range(0, len(texts), batch))))
    return np.vstack(out), tokens


def _norm(m):
    return m / np.linalg.norm(m, axis=1, keepdims=True)


class EmbStore:
    """Ленивая выдача эмбеддингов по ключу варианта: файловый кэш + производные."""

    def __init__(self, qs, lemma_texts, usd):
        self.qs, self.lemma_texts, self.usd = qs, lemma_texts, usd
        self.mem: dict[str, np.ndarray] = {}

    def _texts(self, key: str) -> list[str]:
        if key == "lemma":
            return self.lemma_texts
        if key == "intent":
            return [INTENT_PREFIX + q for q in self.qs]
        return self.qs

    async def _embed(self, key: str, texts: list[str]) -> np.ndarray:
        if key in COMPAT_API:
            url, envk, model, batch, extra, vendor = COMPAT_API[key]
            m, tok = await embed_compat(texts, url, os.environ[envk], model, batch, extra)
            self.usd[vendor] = self.usd.get(vendor, 0) + tok / 1e6 * PRICE[vendor]
            return m
        if key in GEMINI_TASK:
            m, tok = await embed_gemini(texts, os.environ["GEMINI_API_KEY"],
                                        GEMINI_TASK[key])
            self.usd["gemini"] += tok / 1e6 * PRICE["gemini"]
        else:
            m, tok = await embed_openai(texts, os.environ["OPENAI_API_KEY"])
            self.usd["openai"] += tok / 1e6 * PRICE["openai"]
        return m

    async def get(self, key: str) -> np.ndarray:
        if key in self.mem:
            return self.mem[key]
        if key in DERIVED:
            kind = DERIVED[key]
            if kind[0] == "concat":
                parts = [_norm(await self.get(b)) for b in kind[1]]
                m = np.hstack(parts).astype(np.float32)
            else:  # trunc: матрёшечная обрезка + перенормировка
                base = await self.get(kind[1])
                m = _norm(base[:, :kind[2]].copy()).astype(np.float32)
            self.mem[key] = m
            return m
        path = PDIR / f"emb_{key}.npy"
        texts = self._texts(key)
        m = None
        if path.exists():
            m = np.load(path)
            if m.shape[0] == len(texts):
                pass  # кэш актуален
            elif m.shape[0] < len(texts):
                # проект пополнился: доэмбеддить только хвост новых фраз
                set_status(f"Эмбеддинги (+{len(texts) - m.shape[0]}): {TITLES.get(key, key)}",
                           _status.get("pct", 5))
                tail = await self._embed(key, texts[m.shape[0]:])
                m = np.vstack([m, tail])
                np.save(path, m)
            else:
                m = None  # кэш длиннее набора — пересчитать целиком
        if m is None:
            set_status(f"Эмбеддинги: {TITLES.get(key, key)}", _status.get("pct", 5))
            m = await self._embed(key, texts)
            np.save(path, m)
        self.mem[key] = m
        return m

# ---------------- морфология: дедуп, гео, вопросы ----------------

_PREPS = {"в", "во", "на", "с", "со", "для", "за", "по", "из", "изо", "у", "к",
          "ко", "от", "ото", "про", "под", "подо", "над", "о", "об", "обо", "при",
          "без", "безо", "через", "до", "возле", "около", "перед", "передо",
          "между", "сквозь", "вместо", "кроме", "ради", "вдоль", "среди"}
_QWORDS = {"как", "что", "почему", "сколько", "где", "когда", "зачем", "какой",
           "какая", "какие", "каков", "чем", "кто", "куда", "откуда", "ли", "можно"}


def analyze(queries):
    """(лемма-группы, chunk-ключи, гео-ключ, вопрос 0/1, лемма-текст) на фразу."""
    import pymorphy3
    morph = pymorphy3.MorphAnalyzer()

    @lru_cache(maxsize=None)
    def parse(w):
        pr = morph.parse(w)[0]
        return pr.normal_form, "Geox" in str(pr.tag)

    groups, chunk_keys, geo_keys, ques, lemma_texts = {}, [], [], [], []
    for i, q in enumerate(queries):
        words = re.findall(r"[а-яёa-z0-9]+", q.lower())
        parsed = [parse(w) for w in words]
        lemmas = [l for l, _ in parsed]
        lemma_texts.append(" ".join(lemmas))
        groups.setdefault(" ".join(sorted(lemmas)), []).append(i)
        geo_keys.append(" ".join(sorted({l for l, g in parsed if g})))
        ques.append(1 if any(l in _QWORDS for l in lemmas) else 0)
        chunks, cur = [], []
        for w in lemmas:
            if w in _PREPS:
                if cur:
                    chunks.append(" ".join(sorted(cur)))
                cur = [w]
            else:
                cur.append(w)
        if cur:
            chunks.append(" ".join(sorted(cur)))
        chunk_keys.append(" | ".join(sorted(chunks)))
    return groups, chunk_keys, geo_keys, ques, lemma_texts


def pick_reps(freqs, emb, cand, chunks):
    """Дубль: sim >= SURE, или совпали чанки предлогов и sim >= FLOOR."""
    x = _norm(emb)
    out = {}
    for members in cand.values():
        rest = sorted(members, key=lambda i: -freqs[i])
        while rest:
            rep, tail = rest[0], rest[1:]
            mine, other = [rep], []
            for m in tail:
                sim = float(x[rep] @ x[m])
                ok = sim >= DEDUP_SIM_SURE or (chunks[rep] == chunks[m] and sim >= DEDUP_SIM_FLOOR)
                (mine if ok else other).append(m)
            out[rep] = mine
            rest = other
    return sorted(out), out


def split_by_geo(labels, geo, target=""):
    """Кластер с разными гео-ключами режется по гео. target — список синонимов
    целевого региона через запятую («москва, мо, подмосковье»): целевые слова
    вычитаются из гео-ключа фразы, пустой остаток = «наш регион» = без гео.
    «дома под ключ в москве и мо» при таком target склеится с «дома под ключ»,
    а «в казани и москве» останется отдельно (остаток «казань»)."""
    from collections import defaultdict
    tset = {t.strip() for t in target.split(",") if t.strip()}
    if tset:
        geo = [" ".join(sorted(set(g.split()) - tset)) for g in geo]
    seen = defaultdict(set)
    for k, lab in enumerate(labels):
        seen[int(lab)].add(geo[k])
    new_lab = np.asarray(labels).copy()
    remap, nxt = {}, int(np.max(labels)) + 1
    for k, lab in enumerate(labels):
        lab = int(lab)
        if len(seen[lab]) > 1:
            key = (lab, geo[k])
            if key not in remap:
                remap[key] = nxt
                nxt += 1
            new_lab[k] = remap[key]
    return new_lab

# ---------------- HAC ----------------

def build_all(emb, provider, data_dir, pct_from, pct_to):
    x = _norm(emb)
    dist = 1.0 - x @ x.T
    np.clip(dist, 0, 2, out=dist)
    np.fill_diagonal(dist, 0.0)
    rng = np.random.default_rng(42)
    sample = rng.choice(x.shape[0], size=min(2000, x.shape[0]), replace=False)
    sub = dist[np.ix_(sample, sample)]
    t_fine, t_coarse = np.percentile(sub[np.triu_indices(len(sample), 1)], [P_FINE, P_COARSE])
    cond = squareform(dist, checks=False)
    del dist
    z_avg = None
    for j, (name, method) in enumerate(METHODS.items()):
        set_status(f"Кластеризация: {TITLES.get(provider, provider)} ({name})",
                   pct_from + (pct_to - pct_from) * j // 3)
        z = linkage(cond, method=method)
        buf = bytearray()
        for a, b, d, _ in z:
            buf += struct.pack("<iif", int(a), int(b), float(d))
        (data_dir / f"{provider}_{name}.bin").write_bytes(buf)
        if method == "average":
            z_avg = z
    del cond
    fine = fcluster(z_avg, t=t_fine, criterion="distance")
    coarse = fcluster(z_avg, t=t_coarse, criterion="distance")
    return fine, coarse

# ---------------- DeepSeek ----------------

PHRASE_PROMPT = """Ты — аналитик поисковых интентов (методология DrMax Search Intent Classifier v3).
Определи dominant-интент каждой поисковой фразы. Классифицируй НАМЕРЕНИЕ, а не слова:
различай информационный интерес, коммерческое исследование (сравнение, выбор исполнителя,
«лучший», «цена», «отзывы») и транзакционную готовность («заказать», «купить», «записаться»).

intent — строго одно из: информационный | коммерческое исследование | транзакционный | навигационный

Ответ строго JSON: {"items": [{"id": <id>, "intent": "..."}]}"""


async def deepseek_label_phrases(phrases, key):
    """Пофразовая разметка: phrases = [(id, текст)]. Возвращает (id->intent, tin, tout)."""
    batches = [phrases[i:i + 40] for i in range(0, len(phrases), 40)]
    sem = asyncio.Semaphore(1500)  # лимит аккаунта: до 2500 соединений, берём 1500
    result, tin, tout, done = {}, 0, 0, 0

    limits = httpx.Limits(max_connections=1600, max_keepalive_connections=200)
    async with httpx.AsyncClient(timeout=240, limits=limits) as client:
        async def one(batch):
            nonlocal tin, tout, done
            lines = "\n".join(f"{pid}: {txt}" for pid, txt in batch)
            r = await retry_post(client, "https://api.deepseek.com/chat/completions", sem,
                                 headers={"Authorization": f"Bearer {key}"}, json={
                                     "model": "deepseek-v4-flash",
                                     "messages": [{"role": "system", "content": PHRASE_PROMPT},
                                                  {"role": "user", "content": lines}],
                                     "response_format": {"type": "json_object"},
                                     "temperature": 0.1})
            d = r.json()
            tin += d["usage"]["prompt_tokens"]
            tout += d["usage"]["completion_tokens"]
            try:
                for it in json.loads(d["choices"][0]["message"]["content"]).get("items", []):
                    result[int(it["id"])] = str(it.get("intent", ""))[:40]
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                pass
            done += 1
            if done % 25 == 0 or done == len(batches):
                set_status("Разметка интентов фраз (DeepSeek)",
                           20 + int(75 * done / max(1, len(batches))))

        await asyncio.gather(*(one(b) for b in batches), return_exceptions=True)
    return result, tin, tout


INTENT_PROMPT = """Ты — аналитик поисковых интентов (методология DrMax Search Intent Classifier v3).
Классифицируй пользовательские НАМЕРЕНИЯ, а не слова: различай лексическую близость и intent
equivalence, информационный интерес и коммерческое исследование, сравнение и готовность к покупке.

Для каждого кластера запросов определи:
- intent: dominant интент — одно из: информационный | коммерческое исследование | транзакционный | навигационный | смешанный
- secondary: вторичный intent-layer одним коротким словосочетанием или "-"
- mixed_risk: true если внутри кластера скрыты РАЗНЫЕ сценарии пользователя, иначе false
- page_type: подходящий тип страницы (статья | коммерческая страница услуги | категория | карточка | лендинг | FAQ | подборка/сравнение)
- name: чистое короткое название кластера на русском (2-5 слов, без кавычек)

Ответ строго JSON: {"clusters": [{"id": <id>, "intent": "...", "secondary": "...",
"mixed_risk": bool, "page_type": "...", "name": "..."}]}"""


async def deepseek_label(groups, key):
    multi = sorted((cid for cid, qs in groups.items() if len(qs) >= 2),
                   key=lambda c: -sum(f for _, f in groups[c]))[:25000]
    batches = [multi[i:i + 10] for i in range(0, len(multi), 10)]
    sem = asyncio.Semaphore(1500)
    result, tin, tout = {}, 0, 0

    limits = httpx.Limits(max_connections=1600, max_keepalive_connections=200)
    async with httpx.AsyncClient(timeout=240, limits=limits) as client:
        async def one(batch):
            nonlocal tin, tout
            lines = []
            for cid in batch:
                qs = sorted(groups[cid], key=lambda x: -x[1])[:12]
                lines.append(f"Кластер id={cid}: " + "; ".join(q for q, _ in qs))
            r = await retry_post(client, "https://api.deepseek.com/chat/completions", sem,
                                 headers={"Authorization": f"Bearer {key}"}, json={
                                     "model": "deepseek-v4-flash",
                                     "messages": [{"role": "system", "content": INTENT_PROMPT},
                                                  {"role": "user", "content": "\n".join(lines)}],
                                     "response_format": {"type": "json_object"},
                                     "temperature": 0.1})
            d = r.json()
            tin += d["usage"]["prompt_tokens"]
            tout += d["usage"]["completion_tokens"]
            try:
                for cobj in json.loads(d["choices"][0]["message"]["content"]).get("clusters", []):
                    result[int(cobj["id"])] = cobj
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                pass

        done = 0
        for chunk in (batches[i:i + 100] for i in range(0, len(batches), 100)):
            await asyncio.gather(*(one(b) for b in chunk), return_exceptions=True)
            done += len(chunk)
            set_status("Разметка интентов (DeepSeek)", 20 + int(75 * done / max(1, len(batches))))
    return result, tin, tout

# ---------------- вспомогательное ----------------

def load_keys():
    qs, fb, fe, fv, ft, fq = [], [], [], [], [], []
    with open(PDIR / "keys.csv", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f, delimiter=";"):
            q = (row.get("Запрос") or "").strip()
            if q:
                qs.append(q)
                fb.append(int(row.get("Базовая частотность") or 0))
                fe.append(int(row.get("Точная частотность") or 0))
                fv.append(int(row.get("Очень точная частотность") or 0))
                ft.append(1 if (row.get("Топоним") or "") == "1" else 0)
                fq.append(1 if (row.get("Вопрос") or "") == "1" else 0)
    primary = fv if any(fv) else (fe if any(fe) else fb)
    return qs, fb, fe, fv, ft, fq, primary


def name_of(labels, r_queries, r_freqs):
    best = {}
    for lab, q, f in zip(labels, r_queries, r_freqs):
        if lab not in best or f > best[lab][0]:
            best[int(lab)] = (f, q)
    return {lab: q for lab, (_, q) in best.items()}


def write_result(rows, intents=None):
    intents = intents or {}
    with open(PDIR / "result.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Запрос", "Базовая частотность", "Точная частотность",
                    "Очень точная частотность", "Топоним", "Вопрос", "Дубль от",
                    "Кластер", "Имя кластера", "Группа", "Имя группы",
                    "Интент", "Вторичный интент", "Риск смешения", "Тип страницы",
                    "Название кластера (LLM)"])
        for r in rows:
            it = intents.get(r[7], {})
            w.writerow(list(r) + [
                it.get("intent", ""), it.get("secondary", ""),
                {True: "да", False: "нет"}.get(it.get("mixed_risk"), ""),
                it.get("page_type", ""), it.get("name", "")])


def save_costs(usd, n, labeled_clusters=0):
    path = PDIR / "costs.json"
    prev = {}
    try:
        prev = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        pass
    for k in ("openai_usd", "gemini_usd", "deepseek_usd", "voyage_usd", "qwen_usd"):
        prev[k] = round(prev.get(k, 0) + usd.get(k.split("_")[0], 0), 4)
    prev["total_usd"] = round(sum(prev.get(k, 0) for k in
        ("openai_usd", "gemini_usd", "deepseek_usd", "voyage_usd", "qwen_usd")), 4)
    prev["per_query_usd"] = round(prev["total_usd"] / max(1, n), 6)
    if labeled_clusters:
        prev["labeled_clusters"] = labeled_clusters
    if prev.get("deepseek_usd") and prev.get("labeled_clusters"):
        prev["per_cluster_usd"] = round(prev["deepseek_usd"] / prev["labeled_clusters"], 5)
    path.write_text(json.dumps(prev, ensure_ascii=False))
    return prev

# ---------------- main ----------------

async def main():
    usd = {"openai": 0.0, "gemini": 0.0, "deepseek": 0.0, "voyage": 0.0, "qwen": 0.0}
    variants = [v for v in cli_variants() if v in TITLES] or ["openai"]
    target_geo = cli_target_geo()
    qs, fb, fe, fv, ft, fq, fp = load_keys()
    n = len(qs)

    set_status("Морфология (дедуп, гео, вопросы)", 3)
    cand, chunks, geo_all, ques_auto, lemma_texts = analyze(qs)
    ques_all = [max(a, b) for a, b in zip(fq, ques_auto)]

    store = EmbStore(qs, lemma_texts, usd)

    # базовый вектор для дедупа и CSV: openai, если выбран, иначе первый базовый
    def first_base():
        for v in variants:
            if v not in DERIVED:
                return v
        kind = DERIVED[variants[0]]
        return kind[1][0] if kind[0] == "concat" else kind[1]

    base_key = "openai" if "openai" in variants else first_base()
    set_status(f"Эмбеддинги: {TITLES[base_key]}", 5)
    emb_base = await store.get(base_key)

    set_status("Дедуп смысловых дублей", 30)
    reps, groups = pick_reps(fp, emb_base, cand, chunks)
    rep_of = {m: rep for rep, ms in groups.items() for m in ms}
    pos = {r: k for k, r in enumerate(reps)}
    rep_idx = np.array(reps)
    r_qs = [qs[i] for i in reps]
    r_fp = [sum(fp[m] for m in groups[i]) for i in reps]
    r_geo = [geo_all[i] for i in reps]

    data_dir = PDIR / "data"
    data_dir.mkdir(exist_ok=True)

    if LABEL_ONLY:
        # пофразовая разметка: интент присваивается уникальному смыслу один раз
        # и валиден при ЛЮБЫХ настройках среза; вопросы — информационные бесплатно
        r_ques = [max(ques_all[m] for m in groups[i]) for i in reps]
        pmap: dict[str, str] = {}
        try:
            pmap = json.loads((PDIR / "phrase_intents.json").read_text())
        except (OSError, json.JSONDecodeError):
            pass
        for k, qflag in enumerate(r_ques):
            if qflag and r_qs[k] not in pmap:
                pmap[r_qs[k]] = "информационный"
        todo = [(k, r_qs[k]) for k in range(len(reps)) if r_qs[k] not in pmap]
        set_status("Разметка интентов фраз (DeepSeek)", 15)
        got, tin, tout = await deepseek_label_phrases(todo, os.environ["DEEPSEEK_API_KEY"])
        for k, intent in got.items():
            pmap[r_qs[k]] = intent
        usd["deepseek"] = (tin * PRICE["ds_in"] + tout * PRICE["ds_out"]) / 1e6
        (PDIR / "phrase_intents.json").write_text(
            json.dumps(pmap, ensure_ascii=False), encoding="utf-8")
        phrase_int = {k: pmap[r_qs[k]] for k in range(len(reps)) if r_qs[k] in pmap}
        (data_dir / "intents.json").write_text(json.dumps(
            {"i": [phrase_int.get(k, "") for k in range(len(reps))]},
            ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

        # кластерные колонки CSV — по фиксированному срезу базового варианта
        set_status("Экспорт CSV", 96)
        fine, coarse = build_all(emb_base[rep_idx], base_key, data_dir, 96, 98)
        fine = split_by_geo(fine, r_geo, target_geo)
        names_f, names_c = name_of(fine, r_qs, r_fp), name_of(coarse, r_qs, r_fp)
        # агрегат по кластеру: dominant / вторичный / риск смешения (>=25% чужого интента)
        agg: dict[int, dict] = {}
        cl_freq: dict[int, dict] = {}
        for k in range(len(reps)):
            lab = int(fine[k])
            d2 = cl_freq.setdefault(lab, {})
            it = phrase_int.get(k, "")
            if it:
                d2[it] = d2.get(it, 0) + max(1, r_fp[k])
        for lab, d2 in cl_freq.items():
            tot = sum(d2.values())
            top = sorted(d2.items(), key=lambda x: -x[1])
            second = top[1] if len(top) > 1 and top[1][1] >= 0.25 * tot else None
            agg[lab] = {"dom": top[0][0], "sec": second[0] if second else "-",
                        "mixed": "да" if second else "нет"}
        with open(PDIR / "result.csv", "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["Запрос", "Базовая частотность", "Точная частотность",
                        "Очень точная частотность", "Топоним", "Вопрос", "Дубль от",
                        "Кластер", "Имя кластера", "Группа", "Имя группы",
                        "Интент фразы", "Интент кластера", "Вторичный интент кластера",
                        "Риск смешения"])
            for i in range(n):
                k = pos[rep_of[i]]
                lab = int(fine[k])
                a = agg.get(lab, {})
                w.writerow([qs[i], fb[i], fe[i], fv[i],
                            geo_all[i] or ("да" if ft[i] else ""),
                            "да" if ques_all[i] else "",
                            qs[rep_of[i]] if rep_of[i] != i else "",
                            lab, names_f[lab], int(coarse[k]), names_c[int(coarse[k])],
                            phrase_int.get(k, ""), a.get("dom", ""), a.get("sec", ""),
                            a.get("mixed", "")])
        costs = save_costs(usd, n, labeled_clusters=len(got))
        set_status("Готово", 100, done=True,
                   cost_rub=round(sum(usd.values()) * USD_RUB, 2), costs=costs,
                   uniq=len(reps), clusters=len(set(fine)))
        return

    # queries.json (общий для всех вариантов)
    dups = [[qs[m] for m in groups[i] if m != i] for i in reps]
    r_fb = [sum(fb[m] for m in groups[i]) for i in reps]
    (data_dir / "queries.json").write_text(
        json.dumps({"q": r_qs, "f": r_fp, "b": r_fb, "d": dups, "total": n,
                    "g": r_geo,
                    "w": [max(ques_all[m] for m in groups[i]) for i in reps]},
                   ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # интенты из словаря (если размечали) — перевыровнять на новый список репов
    try:
        pmap = json.loads((PDIR / "phrase_intents.json").read_text())
        (data_dir / "intents.json").write_text(json.dumps(
            {"i": [pmap.get(q, "") for q in r_qs]},
            ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    except (OSError, json.JSONDecodeError):
        pass

    # деревья по каждому варианту; упавший вариант пропускаем, не валя прогон
    fine = coarse = None
    built, skipped = [], []
    span = 55 / max(1, len(variants))
    for vi, vkey in enumerate(variants):
        try:
            if (vkey != base_key
                    and all((data_dir / f"{vkey}_{m}.bin").exists() for m in METHODS)):
                built.append(vkey)  # деревья уже есть с прошлого запуска (дедуп детерминирован)
                continue
            emb = await store.get(vkey)
            f_, c_ = build_all(emb[rep_idx], vkey, data_dir,
                               int(38 + vi * span), int(38 + (vi + 1) * span))
            built.append(vkey)
            if vkey == base_key:
                fine, coarse = f_, c_
        except Exception as e:
            skipped.append(vkey)
            set_status(f"Вариант {TITLES.get(vkey, vkey)} пропущен: {type(e).__name__}",
                       int(38 + (vi + 1) * span), skipped=skipped)
    if fine is None:  # база не построилась в цикле — считаем отдельно
        fine, coarse = build_all(emb_base[rep_idx], base_key, data_dir, 93, 95)
    fine = split_by_geo(fine, r_geo, target_geo)

    (data_dir / "meta.json").write_text(json.dumps(
        {"n": len(reps), "generated": time.strftime("%F %H:%M"),
         "providers": {v: {"methods": list(METHODS), "title": TITLES[v]} for v in built}},
        ensure_ascii=False))

    set_status("Экспорт CSV", 96)
    names_f, names_c = name_of(fine, r_qs, r_fp), name_of(coarse, r_qs, r_fp)
    rows = []
    for i in range(n):
        k = pos[rep_of[i]]
        rows.append([qs[i], fb[i], fe[i], fv[i],
                     geo_all[i] or ("да" if ft[i] else ""),
                     "да" if ques_all[i] else "",
                     qs[rep_of[i]] if rep_of[i] != i else "",
                     int(fine[k]), names_f[int(fine[k])],
                     int(coarse[k]), names_c[int(coarse[k])]])
    write_result(rows)
    costs = save_costs(usd, n)
    (PDIR / "report.md").write_text(
        f"# {PDIR.name}\n\nФраз: {n} · уникальных смыслов: {len(reps)} · "
        f"кластеров: {len(set(fine))}\n\nВарианты: "
        + ", ".join(TITLES[v] for v in variants)
        + "\n\n## Себестоимость\n"
        f"- Эмбеддинги OpenAI: ${costs['openai_usd']}\n"
        f"- Эмбеддинги Gemini: ${costs['gemini_usd']}\n"
        f"- Разметка DeepSeek: ${costs['deepseek_usd']}\n"
        f"- Итого: ${costs['total_usd']} (~{costs['total_usd'] * USD_RUB:.2f} ₽)\n"
        f"- Цена 1 запроса: ${costs['per_query_usd']}\n", encoding="utf-8")
    set_status("Готово", 100, done=True, skipped=skipped,
               cost_rub=round(sum(usd.values()) * USD_RUB, 2), costs=costs,
               uniq=len(reps), clusters=len(set(fine)))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        set_status("Ошибка", 0, error=f"{type(e).__name__}: {e}"[:300])
        sys.exit(1)
