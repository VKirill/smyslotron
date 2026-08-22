"""Загрузка файлов, автомаппинг колонок (вордстат-нотация) и умные шаблоны форматов."""

import csv
import hashlib
import io
import json
import re
import secrets
import sqlite3
import time

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from .auth import current_user
from .config import MAX_ROWS, MAX_UPLOAD_MB, P, UPLOADS
from .db import db

router = APIRouter(prefix=P)

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
TOPO_ALIASES = {"является топонимом", "топоним", "toponym", "гео", "geo"}
QUES_ALIASES = {"является вопросом", "вопрос", "question", "вопросительный"}


def decode_upload(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise HTTPException(400, "Не удалось определить кодировку файла (жду UTF-8 или Windows-1251)")


def normalize_kc(text: str) -> str:
    """Экспорт Key Collector «Путь к группе и ее название»: убираем строку-шапку
    формата и строки-пути групп («/Нераспределенные» и т.п.), оставляя обычный
    CSV с заголовком «Запрос;Wordstat;…»."""
    lines = text.splitlines()
    first = next((l for l in lines if l.strip()), "")
    if not first.strip().lower().startswith("путь к группе"):
        return text
    out, skipped_first = [], False
    for l in lines:
        s = l.strip()
        if not skipped_first and s:
            skipped_first = True  # сама строка «Путь к группе…»
            continue
        if s.startswith("/"):
            continue  # маркер группы, не данные
        out.append(l)
    return "\n".join(out)


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


def header_sig(text: str) -> str:
    """Подпись формата файла: sha1 первой непустой строки (заголовка)."""
    for l in text.splitlines():
        if l.strip():
            return hashlib.sha1(l.strip().lower().encode()).hexdigest()
    return ""


_TPL_KEYS = ("delimiter", "has_header", "query_col", "base_col", "exact_col",
             "vexact_col", "topo_col", "ques_col")


def save_template(body: dict) -> None:
    """Запоминаем подтверждённый пользователем маппинг под подписью заголовка —
    следующий файл того же формата размапится автоматически (для всех пользователей)."""
    if not body.get("has_header"):
        return
    up = UPLOADS / f"{re.sub(r'[^a-f0-9]', '', body.get('upload_id', ''))}.txt"
    if not up.exists():
        return
    sig = header_sig(up.read_text(encoding="utf-8"))
    if not sig:
        return
    tpl = {k: body.get(k) for k in _TPL_KEYS}
    with db() as c:
        c.execute("INSERT INTO mapping_templates(sig,mapping,uses,updated) VALUES(?,?,1,?) "
                  "ON CONFLICT(sig) DO UPDATE SET mapping=excluded.mapping, "
                  "uses=uses+1, updated=excluded.updated",
                  (sig, json.dumps(tpl), time.strftime("%F %T")))


@router.post("/uploads")
async def upload(request: Request, file: UploadFile = File(...),
                 user: sqlite3.Row = Depends(current_user)):
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(400, f"Файл больше {MAX_UPLOAD_MB} МБ")
    text = normalize_kc(decode_upload(raw))
    guess = guess_mapping(text)
    with db() as c:
        row = c.execute("SELECT mapping FROM mapping_templates WHERE sig=?",
                        (header_sig(text),)).fetchone()
    if row:
        tpl = json.loads(row["mapping"])
        ncols = max((len(r) for r in guess["previews"].get(tpl.get("delimiter", ";"), [[]])),
                    default=0)
        # применяем шаблон, только если колонки влезают в реальную ширину файла
        if 0 <= int(tpl.get("query_col", -1)) < ncols:
            guess.update({k: tpl[k] for k in _TPL_KEYS if k in tpl})
            guess["template"] = True
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
        v = v.strip().strip('"').replace(" ", "").replace(" ", "")
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
