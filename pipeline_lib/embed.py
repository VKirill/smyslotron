"""Эмбеддинги: провайдеры (OpenAI / Gemini / совместимые), ретраи, RateLimiter,
файловый инкрементальный кэш EmbStore."""

import asyncio
import os
import time

import httpx
import numpy as np

from . import ctx
from .config import COMPAT_API, DERIVED, GEMINI_TASK, INTENT_PREFIX, PRICE, TITLES
from .ctx import set_status


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
        path = ctx.PDIR / f"emb_{key}.npy"
        texts = self._texts(key)
        m = None
        if path.exists():
            m = np.load(path)
            if m.shape[0] == len(texts):
                pass  # кэш актуален
            elif m.shape[0] < len(texts):
                # проект пополнился: доэмбеддить только хвост новых фраз
                set_status(f"Эмбеддинги (+{len(texts) - m.shape[0]}): {TITLES.get(key, key)}",
                           ctx._status.get("pct", 5))
                tail = await self._embed(key, texts[m.shape[0]:])
                m = np.vstack([m, tail])
                np.save(path, m)
            else:
                m = None  # кэш длиннее набора — пересчитать целиком
        if m is None:
            set_status(f"Эмбеддинги: {TITLES.get(key, key)}", ctx._status.get("pct", 5))
            m = await self._embed(key, texts)
            np.save(path, m)
        self.mem[key] = m
        return m
