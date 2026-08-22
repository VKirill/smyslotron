"""
Semantika Web — мини-SaaS вокруг кластеризатора по эмбеддингам.

Авторизация по почте/паролю (без подтверждения), проекты = загруженные файлы,
очередь на кластеризацию (pipeline.py как subprocess), выдача данных просмотрщику.

Запуск: uv run uvicorn app:app --host 127.0.0.1 --port 8090
Проксируется angie: /semantika/api/ -> :8090/semantika/api/
"""

import asyncio
import csv
import hashlib
import hmac
import io
import json
import re
import secrets
import shutil
import sqlite3
import time
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse

BASE = Path(__file__).parent
DB_PATH = BASE / "semantika.db"
PROJECTS = BASE / "projects"
UPLOADS = BASE / "uploads"
ENV_FILE = Path("/home/ubuntu/apps/seo-cluster/.env")  # источник API-ключей для pipeline

MAX_ROWS = 100_000
MAX_PROJECTS = 5
MAX_UPLOAD_MB = 50
COOKIE = "sem_sess"

app = FastAPI(docs_url=None, redoc_url=None)
P = "/semantika/api"

# ---------------------------------------------------------------- db

def db() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_db() -> None:
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL,
            pw TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(
            token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS projects(
            id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL,
            status TEXT NOT NULL, task TEXT DEFAULT '',
            rows INTEGER DEFAULT 0, uniq INTEGER DEFAULT 0, clusters INTEGER DEFAULT 0,
            cost_rub REAL DEFAULT 0, labeled INTEGER DEFAULT 0,
            error TEXT DEFAULT '', created TEXT NOT NULL);
        """)
        for ddl in ("ALTER TABLE projects ADD COLUMN costs TEXT DEFAULT ''",
                    "ALTER TABLE projects ADD COLUMN variants TEXT DEFAULT 'openai,gemini'",
                    "ALTER TABLE projects ADD COLUMN retries INTEGER DEFAULT 0",
                    "ALTER TABLE projects ADD COLUMN history TEXT DEFAULT ''",
                    "ALTER TABLE projects ADD COLUMN target_geo TEXT DEFAULT ''"):
            try:
                c.execute(ddl)
            except sqlite3.OperationalError:
                pass

# ---------------------------------------------------------------- auth

def hash_pw(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1)
    return salt.hex() + ":" + h.hex()


def check_pw(pw: str, stored: str) -> bool:
    try:
        salt_hex, h = stored.split(":")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt_hex), n=2 ** 14, r=8, p=1)
        return hmac.compare_digest(calc.hex(), h)
    except ValueError:
        return False


_rate: dict[str, list[float]] = {}  # ponytail: in-memory rate limit, до Redis далеко


def rate_limit(request: Request, limit: int = 10, window: int = 60) -> None:
    ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "?")
    now = time.time()
    hits = [t for t in _rate.get(ip, []) if now - t < window]
    if len(hits) >= limit:
        raise HTTPException(429, "Слишком много попыток, подожди минуту")
    hits.append(now)
    _rate[ip] = hits


def current_user(request: Request) -> sqlite3.Row:
    tok = request.cookies.get(COOKIE, "")
    if tok:
        with db() as c:
            row = c.execute(
                "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
                (tok,)).fetchone()
        if row:
            return row
    raise HTTPException(401, "Нужна авторизация")


def set_session(resp: Response, user_id: int) -> None:
    tok = secrets.token_urlsafe(32)
    with db() as c:
        c.execute("INSERT INTO sessions(token,user_id,created) VALUES(?,?,?)",
                  (tok, user_id, time.strftime("%F %T")))
    resp.set_cookie(COOKIE, tok, httponly=True, secure=True, samesite="lax",
                    max_age=30 * 86400, path="/")


@app.post(P + "/auth/register")
async def register(request: Request, resp: Response):
    rate_limit(request)
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    pw = body.get("password") or ""
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(400, "Некорректная почта")
    if len(pw) < 6:
        raise HTTPException(400, "Пароль — минимум 6 символов")
    with db() as c:
        if c.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise HTTPException(409, "Такая почта уже зарегистрирована — войди")
        cur = c.execute("INSERT INTO users(email,pw,created) VALUES(?,?,?)",
                        (email, hash_pw(pw), time.strftime("%F %T")))
        uid = cur.lastrowid
    set_session(resp, uid)
    return {"email": email}


@app.post(P + "/auth/login")
async def login(request: Request, resp: Response):
    rate_limit(request)
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    with db() as c:
        row = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not row or not check_pw(body.get("password") or "", row["pw"]):
        raise HTTPException(401, "Неверная почта или пароль")
    set_session(resp, row["id"])
    return {"email": email}


@app.post(P + "/auth/logout")
async def logout(request: Request, resp: Response):
    tok = request.cookies.get(COOKIE, "")
    if tok:
        with db() as c:
            c.execute("DELETE FROM sessions WHERE token=?", (tok,))
    resp.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get(P + "/auth/me")
async def me(user: sqlite3.Row = Depends(current_user)):
    return {"email": user["email"]}

# ---------------------------------------------------------------- upload + mapper

DELIMS = [";", "\t", ",", "|"]
Q_ALIASES = {"запрос", "ключевая фраза", "фраза", "ключ", "keyword", "ключевое слово",
             "key", "запросы", "keys"}
BASE_ALIASES = {"базовая частотность", "частотность", "частота", "ws", "базовая",
                "частота ws", "wordstat", "базовая частота"}
EXACT_ALIASES = {"точная частотность", "!частота", '"!частота"', 'частота "!"',
                 'ws "!"', "точная", "точная частота", '"!ws"',
                 "!wordstat", '"!wordstat"', "!ws"}
VEXACT_ALIASES = {"[!wordstat]", "[!ws]", "[!частота]", "очень точная частотность",
                  "очень точная", "очень точная частота", 'ws "[!]"'}
KNOWN_VARIANTS = {"openai", "gemini", "gem_sim", "gem_query", "lemma", "intent",
                  "ensemble", "openai1536", "gemini768", "voyage", "qwen"}
TOPO_ALIASES = {"является топонимом", "топоним", "toponym", "гео", "geo"}
QUES_ALIASES = {"является вопросом", "вопрос", "question", "вопросительный"}


def decode_upload(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise HTTPException(400, "Не удалось определить кодировку файла (жду UTF-8 или Windows-1251)")


def guess_mapping(text: str) -> dict:
    """Автоопределение разделителя, заголовка и колонок + превью для модалки-маппера."""
    lines = [l for l in text.splitlines() if l.strip()][:16]
    if not lines:
        raise HTTPException(400, "Файл пустой")
    # разделитель: максимум стабильных колонок на первых строках
    best, best_cols = ";", 1
    for d in DELIMS:
        counts = [len(next(csv.reader([l], delimiter=d))) for l in lines]
        cols = min(counts)
        if cols > best_cols:
            best, best_cols = d, cols
    previews = {}
    for d in DELIMS:
        previews[d] = [next(csv.reader([l], delimiter=d))[:12] for l in lines[:12]]
    rows = previews[best]
    header = [h.strip().lower().strip('"“”«»') for h in rows[0]]
    has_header = any(h in Q_ALIASES | BASE_ALIASES | EXACT_ALIASES | VEXACT_ALIASES | TOPO_ALIASES | QUES_ALIASES for h in header)
    qcol = bcol = ecol = vcol = tcol = qscol = -1
    if has_header:
        for i, h in enumerate(header):
            if h in Q_ALIASES and qcol < 0: qcol = i
            elif h in VEXACT_ALIASES and vcol < 0: vcol = i
            elif h in EXACT_ALIASES and ecol < 0: ecol = i
            elif h in BASE_ALIASES and bcol < 0: bcol = i
            elif h in TOPO_ALIASES and tcol < 0: tcol = i
            elif h in QUES_ALIASES and qscol < 0: qscol = i
    if qcol < 0:
        # колонка запроса: первая, где значения нечисловые
        body = rows[1:] if has_header else rows
        for i in range(best_cols):
            vals = [r[i] for r in body if len(r) > i]
            if vals and sum(not v.strip().replace(" ", "").isdigit() for v in vals) > len(vals) / 2:
                qcol = i
                break
        qcol = max(qcol, 0)
    return {"delimiter": best, "has_header": has_header,
            "query_col": qcol, "base_col": bcol, "exact_col": ecol, "vexact_col": vcol,
            "topo_col": tcol, "ques_col": qscol,
            "previews": previews}


@app.post(P + "/uploads")
async def upload(request: Request, file: UploadFile = File(...),
                 user: sqlite3.Row = Depends(current_user)):
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(400, f"Файл больше {MAX_UPLOAD_MB} МБ")
    text = decode_upload(raw)
    guess = guess_mapping(text)
    UPLOADS.mkdir(exist_ok=True)
    for old in UPLOADS.glob("*.txt"):  # чистим брошенные загрузки старше часа
        if time.time() - old.stat().st_mtime > 3600:
            old.unlink(missing_ok=True)
    token = secrets.token_hex(12)
    (UPLOADS / f"{token}.txt").write_text(text, encoding="utf-8")
    name = re.sub(r"\.(csv|txt|tsv)$", "", file.filename or "проект", flags=re.I)[:80]
    return {"upload_id": token, "name": name, **guess}


def parse_upload_rows(body: dict) -> list[tuple]:
    """Разбор загруженного файла по маппингу колонок -> [(q, b, e, v, t, w)]."""
    up = UPLOADS / f"{re.sub(r'[^a-f0-9]', '', body.get('upload_id', ''))}.txt"
    if not up.exists():
        raise HTTPException(400, "Загрузка не найдена — загрузи файл заново")
    delim = body.get("delimiter") or ";"
    if delim not in DELIMS:
        raise HTTPException(400, "Неизвестный разделитель")
    has_header = bool(body.get("has_header"))
    qcol = int(body.get("query_col", 0))
    bcol = int(body.get("base_col", -1))
    ecol = int(body.get("exact_col", -1))
    vcol = int(body.get("vexact_col", -1))
    tcol = int(body.get("topo_col", -1))
    qscol = int(body.get("ques_col", -1))

    def to_int(v: str) -> int:
        v = v.strip().strip('"').replace(" ", "").replace(" ", "")
        return int(v) if v.isdigit() else 0

    rows_out, seen = [], set()
    reader = csv.reader(io.StringIO(up.read_text(encoding="utf-8")), delimiter=delim)
    for i, row in enumerate(reader):
        if i == 0 and has_header:
            continue
        if len(row) <= qcol:
            continue
        q = row[qcol].strip().strip('"')
        if not q or q.lower() in seen:
            continue
        seen.add(q.lower())
        b = to_int(row[bcol]) if 0 <= bcol < len(row) else 0
        e = to_int(row[ecol]) if 0 <= ecol < len(row) else 0
        v = to_int(row[vcol]) if 0 <= vcol < len(row) else 0

        def flag(col):
            if not (0 <= col < len(row)):
                return ""
            return "1" if row[col].strip().strip('"').lower() in ("1", "да", "true", "yes", "+") else "0"

        rows_out.append((q, b, e, v, flag(tcol), flag(qscol)))
        if len(rows_out) > MAX_ROWS:
            raise HTTPException(400, f"Больше {MAX_ROWS:,} фраз — сократи файл")
    up.unlink(missing_ok=True)
    return rows_out


@app.post(P + "/projects")
async def create_project(request: Request, user: sqlite3.Row = Depends(current_user)):
    body = await request.json()
    name = (body.get("name") or "Проект").strip()[:80]
    variants = [v for v in (body.get("variants") or ["openai", "gemini"])
                if v in KNOWN_VARIANTS] or ["openai", "gemini"]
    with db() as c:
        n_active = c.execute("SELECT COUNT(*) FROM projects WHERE user_id=?",
                             (user["id"],)).fetchone()[0]
    if n_active >= MAX_PROJECTS:
        raise HTTPException(400, f"Лимит {MAX_PROJECTS} проектов — удали старый")
    rows_out = parse_upload_rows(body)
    if len(rows_out) < 10:
        raise HTTPException(400, "После разбора осталось меньше 10 фраз — проверь колонки и разделитель")

    pid = secrets.token_hex(8)
    pdir = PROJECTS / str(user["id"]) / pid
    pdir.mkdir(parents=True)
    with open(pdir / "keys.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Запрос", "Базовая частотность", "Точная частотность",
                    "Очень точная частотность", "Топоним", "Вопрос"])
        w.writerows(rows_out)
    with db() as c:
        c.execute("INSERT INTO projects(id,user_id,name,status,task,rows,created,variants) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  (pid, user["id"], name, "queued", "cluster", len(rows_out),
                   time.strftime("%F %T"), ",".join(variants)))
    return {"id": pid, "rows": len(rows_out)}

# ---------------------------------------------------------------- projects

def own_project(pid: str, user: sqlite3.Row) -> sqlite3.Row:
    with db() as c:
        row = c.execute("SELECT * FROM projects WHERE id=? AND user_id=?",
                        (pid, user["id"])).fetchone()
    if not row:
        raise HTTPException(404, "Проект не найден")
    return row


def project_dir(row: sqlite3.Row) -> Path:
    return PROJECTS / str(row["user_id"]) / row["id"]


def project_json(row: sqlite3.Row) -> dict:
    d = dict(row)
    d.pop("user_id", None)
    try:
        d["costs"] = json.loads(d.get("costs") or "{}")
    except json.JSONDecodeError:
        d["costs"] = {}
    d["variants"] = (d.get("variants") or "openai,gemini").split(",")
    st = project_dir(row) / "status.json"
    if row["status"] == "running" and st.exists():
        try:
            d["progress"] = json.loads(st.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return d


@app.get(P + "/projects")
async def list_projects(user: sqlite3.Row = Depends(current_user)):
    with db() as c:
        rows = c.execute("SELECT * FROM projects WHERE user_id=? ORDER BY created DESC",
                         (user["id"],)).fetchall()
    return [project_json(r) for r in rows]


@app.get(P + "/projects/{pid}")
async def get_project(pid: str, user: sqlite3.Row = Depends(current_user)):
    return project_json(own_project(pid, user))


@app.post(P + "/projects/{pid}/run")
async def run_project(pid: str, user: sqlite3.Row = Depends(current_user)):
    row = own_project(pid, user)
    if row["status"] == "running":
        raise HTTPException(400, "Уже выполняется")
    with db() as c:
        c.execute("UPDATE projects SET status='queued', task='cluster', error='' WHERE id=?", (pid,))
    return {"ok": True}


@app.post(P + "/projects/{pid}/label")
async def label_project(pid: str, user: sqlite3.Row = Depends(current_user)):
    row = own_project(pid, user)
    if row["status"] == "running":
        raise HTTPException(400, "Дождись окончания текущей обработки")
    if not (project_dir(row) / "data" / "meta.json").exists():
        raise HTTPException(400, "Сначала выполни кластеризацию")
    with db() as c:
        c.execute("UPDATE projects SET status='queued', task='label', error='' WHERE id=?", (pid,))
    return {"ok": True}


@app.post(P + "/projects/{pid}/target_geo")
async def set_target_geo(pid: str, request: Request,
                         user: sqlite3.Row = Depends(current_user)):
    """Целевой регион проекта: фразы этого гео сливаются с фразами без гео.
    Применяется в просмотрщике сразу; в серверный result.csv — при следующем пересчёте."""
    own_project(pid, user)
    body = await request.json()
    tg = str(body.get("target_geo") or "").strip().lower()[:200]
    with db() as c:
        c.execute("UPDATE projects SET target_geo=? WHERE id=?", (tg, pid))
    return {"target_geo": tg}


@app.post(P + "/projects/{pid}/append")
async def append_project(pid: str, request: Request,
                         user: sqlite3.Row = Depends(current_user)):
    """Докидывание фраз: точные дубли схлопываются (max частот, OR флагов),
    новые уникальные дописываются В КОНЕЦ — порядок старых строк неизменен,
    чтобы инкрементальный кэш эмбеддингов остался валидным."""
    row = own_project(pid, user)
    if row["status"] == "running":
        raise HTTPException(400, "Дождись окончания текущей обработки")
    body = await request.json()
    new_rows = parse_upload_rows(body)
    pdir = project_dir(row)

    existing: list[list] = []
    index: dict[str, list] = {}
    with open(pdir / "keys.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter=";"):
            q = (r.get("Запрос") or "").strip()
            if not q:
                continue
            item = [q, int(r.get("Базовая частотность") or 0),
                    int(r.get("Точная частотность") or 0),
                    int(r.get("Очень точная частотность") or 0),
                    r.get("Топоним") or "0", r.get("Вопрос") or "0"]
            existing.append(item)
            index[q.lower()] = item

    added = merged_cnt = 0
    for (q, b, e, v, t, w) in new_rows:
        k = q.lower()
        it = index.get(k)
        if it:
            it[1] = max(it[1], b)
            it[2] = max(it[2], e)
            it[3] = max(it[3], v)
            it[4] = "1" if it[4] == "1" or t == "1" else "0"
            it[5] = "1" if it[5] == "1" or w == "1" else "0"
            merged_cnt += 1
        else:
            item = [q, b, e, v, t, w]
            existing.append(item)
            index[k] = item
            added += 1
    if len(existing) > MAX_ROWS:
        raise HTTPException(400, f"Итого больше {MAX_ROWS:,} фраз — не влезает в лимит")

    with open(pdir / "keys.csv", "w", encoding="utf-8-sig", newline="") as f:
        w2 = csv.writer(f, delimiter=";")
        w2.writerow(["Запрос", "Базовая частотность", "Точная частотность",
                     "Очень точная частотность", "Топоним", "Вопрос"])
        w2.writerows(existing)

    # устаревшие деревья и meta — снести, чтобы «пропуск готовых» их не переиспользовал
    ddir = pdir / "data"
    if ddir.exists():
        for fb in ddir.glob("*.bin"):
            fb.unlink(missing_ok=True)
        (ddir / "meta.json").unlink(missing_ok=True)

    entry = f"{time.strftime('%F')} +{added}"
    with db() as c:
        c.execute("UPDATE projects SET rows=?, status='queued', task='cluster', "
                  "error='', retries=0, history=CASE WHEN history='' THEN ? "
                  "ELSE history || ' · ' || ? END WHERE id=?",
                  (len(existing), entry, entry, pid))
    return {"added": added, "merged": merged_cnt, "rows": len(existing)}


@app.delete(P + "/projects/{pid}")
async def delete_project(pid: str, user: sqlite3.Row = Depends(current_user)):
    row = own_project(pid, user)
    if row["status"] == "running":
        raise HTTPException(400, "Дождись окончания обработки")
    shutil.rmtree(project_dir(row), ignore_errors=True)
    with db() as c:
        c.execute("DELETE FROM projects WHERE id=?", (pid,))
    return {"ok": True}


DATA_FILES = re.compile(
    r"^(queries\.json|meta\.json|intents\.json|[a-z0-9_]{1,20}_(?:hard|soft|avg)\.bin|"
    r"result\.csv|report\.md)$")


@app.get(P + "/projects/{pid}/data/{fname}")
async def project_data(pid: str, fname: str, user: sqlite3.Row = Depends(current_user)):
    row = own_project(pid, user)
    if not DATA_FILES.fullmatch(fname):
        raise HTTPException(404)
    pdir = project_dir(row)
    path = pdir / "data" / fname
    if not path.exists():
        path = pdir / fname  # result.csv / report.md лежат в корне проекта
    if not path.exists():
        raise HTTPException(404)
    media = ("application/octet-stream" if fname.endswith(".bin")
             else "text/csv; charset=utf-8" if fname.endswith(".csv")
             else "application/json" if fname.endswith(".json") else "text/markdown")
    if fname == "result.csv":
        from urllib.parse import quote
        headers = {"Content-Disposition":
                   f"attachment; filename=result.csv; filename*=UTF-8''{quote(row['name'])}.csv"}
    else:
        headers = {}
    return FileResponse(path, media_type=media, headers=headers)

# ---------------------------------------------------------------- worker

def pipeline_env() -> dict:
    import os
    env = dict(os.environ)
    for f in (ENV_FILE, BASE / ".env"):  # локальный .env приложения поверх общего
        if f.exists():
            for line in f.read_text().splitlines():
                m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
                if m:
                    env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return env


async def worker_loop() -> None:
    while True:
        row = None
        try:
            with db() as c:
                if not c.execute("SELECT 1 FROM projects WHERE status='running'").fetchone():
                    row = c.execute("SELECT * FROM projects WHERE status='queued' "
                                    "ORDER BY created LIMIT 1").fetchone()
                    if row:
                        c.execute("UPDATE projects SET status='running' WHERE id=?", (row["id"],))
        except Exception:
            row = None
        if row:
            try:
                await run_pipeline(row)
            except Exception as e:
                with db() as c:
                    c.execute("UPDATE projects SET status='failed', error=? WHERE id=?",
                              (str(e)[:500], row["id"]))
        await asyncio.sleep(3)


async def run_pipeline(row: sqlite3.Row) -> None:
    pdir = project_dir(row)
    args = ["uv", "run", str(BASE / "pipeline.py"), str(pdir),
            "--variants", row["variants"] or "openai,gemini",
            "--target-geo", row["target_geo"] or ""]
    if row["task"] == "label":
        args.append("--label-only")
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=BASE, env=pipeline_env(),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    st = {}
    try:
        st = json.loads((pdir / "status.json").read_text())
    except (OSError, json.JSONDecodeError):
        pass
    with db() as c:
        skipped = st.get("skipped") or []
        if (proc.returncode == 0 and st.get("done") and skipped
                and row["task"] == "cluster" and (row["retries"] or 0) < 3):
            # часть вариантов не собралась (квоты API) — дожимаем повтором,
            # готовые эмбеддинги и деревья возьмутся из кэша проекта
            c.execute("UPDATE projects SET status='queued', retries=retries+1, "
                      "error=? WHERE id=?",
                      ("повтор: досбор вариантов " + ",".join(skipped), row["id"]))
        elif proc.returncode == 0 and st.get("done"):
            c.execute(
                "UPDATE projects SET status='ready', uniq=?, clusters=?, "
                "cost_rub=cost_rub+?, labeled=labeled|?, costs=? WHERE id=?",
                (st.get("uniq", row["uniq"]), st.get("clusters", row["clusters"]),
                 st.get("cost_rub", 0), 1 if row["task"] == "label" else 0,
                 json.dumps(st.get("costs", {}), ensure_ascii=False), row["id"]))
        else:
            msg = (st.get("error") or (err or b"").decode()[-500:] or "неизвестная ошибка")
            c.execute("UPDATE projects SET status='failed', error=? WHERE id=?",
                      (msg[:500], row["id"]))


@app.on_event("startup")
async def startup() -> None:
    init_db()
    PROJECTS.mkdir(exist_ok=True)
    with db() as c:  # зависшие после рестарта running -> failed c возможностью перезапуска
        c.execute("UPDATE projects SET status='failed', error='Прервано перезапуском сервера' "
                  "WHERE status='running'")
    asyncio.get_event_loop().create_task(worker_loop())
