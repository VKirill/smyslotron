"""Проекты: создание, очередь, пополнение, срез, выдача данных просмотрщику."""

import csv
import json
import re
import secrets
import shutil
import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from .auth import current_user
from .config import KNOWN_VARIANTS, P, PROJECTS
from .db import db
from .uploads import parse_upload_rows, save_template

router = APIRouter(prefix=P)


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


@router.post("/projects")
async def create_project(request: Request, user: sqlite3.Row = Depends(current_user)):
    body = await request.json()
    name = (body.get("name") or "Проект").strip()[:80]
    variants = [v for v in (body.get("variants") or ["openai", "gemini"])
                if v in KNOWN_VARIANTS] or ["openai", "gemini"]
    save_template(body)
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


@router.get("/projects")
async def list_projects(user: sqlite3.Row = Depends(current_user)):
    with db() as c:
        rows = c.execute("SELECT * FROM projects WHERE user_id=? ORDER BY created DESC",
                         (user["id"],)).fetchall()
    return [project_json(r) for r in rows]


@router.get("/projects/{pid}")
async def get_project(pid: str, user: sqlite3.Row = Depends(current_user)):
    return project_json(own_project(pid, user))


@router.post("/projects/{pid}/run")
async def run_project(pid: str, user: sqlite3.Row = Depends(current_user)):
    row = own_project(pid, user)
    if row["status"] == "running":
        raise HTTPException(400, "Уже выполняется")
    with db() as c:
        c.execute("UPDATE projects SET status='queued', task='cluster', error='' WHERE id=?", (pid,))
    return {"ok": True}


@router.post("/projects/{pid}/label")
async def label_project(pid: str, user: sqlite3.Row = Depends(current_user)):
    row = own_project(pid, user)
    if row["status"] == "running":
        raise HTTPException(400, "Дождись окончания текущей обработки")
    if not (project_dir(row) / "data" / "meta.json").exists():
        raise HTTPException(400, "Сначала выполни кластеризацию")
    with db() as c:
        c.execute("UPDATE projects SET status='queued', task='label', error='' WHERE id=?", (pid,))
    return {"ok": True}


@router.post("/projects/{pid}/slice")
async def save_slice(pid: str, request: Request, user: sqlite3.Row = Depends(current_user)):
    """Зафиксировать текущий срез просмотрщика: мгновенная запись slice.json,
    без пересборки. Пайплайн подхватит срез при следующем естественном прогоне
    (интенты/пополнение); пользовательский экспорт — клиентский Excel."""
    row = own_project(pid, user)
    body = await request.json()
    variant = str(body.get("variant") or "")
    mode = str(body.get("mode") or "")
    if variant not in KNOWN_VARIANTS or mode not in ("hard", "avg", "soft"):
        raise HTTPException(400, "Некорректный вариант или режим")
    sl = {"variant": variant, "mode": mode,
          "slider": max(0, min(10000, int(body.get("slider", 0)))),
          "min_size": max(1, min(1000, int(body.get("min_size", 1))))}
    (project_dir(row) / "slice.json").write_text(json.dumps(sl), encoding="utf-8")
    return {"ok": True, **sl}


@router.post("/projects/{pid}/target_geo")
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


@router.post("/projects/{pid}/append")
async def append_project(pid: str, request: Request,
                         user: sqlite3.Row = Depends(current_user)):
    """Докидывание фраз: точные дубли схлопываются (max частот, OR флагов),
    новые уникальные дописываются В КОНЕЦ — порядок старых строк неизменен,
    чтобы инкрементальный кэш эмбеддингов остался валидным."""
    row = own_project(pid, user)
    if row["status"] == "running":
        raise HTTPException(400, "Дождись окончания текущей обработки")
    body = await request.json()
    save_template(body)
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

    # freq_only: обновить ТОЛЬКО частотности существующих фраз (перезапись значений),
    # новые фразы не добавлять — состав неизменен, эмбеддинги и деревья остаются в силе
    freq_only = bool(body.get("freq_only"))
    added = merged_cnt = 0
    changed = False  # менялось ли хоть что-то фактически
    for (q, b, e, v, t, w) in new_rows:
        k = q.lower()
        it = index.get(k)
        if it:
            before = list(it)
            if freq_only:
                it[1], it[2], it[3] = b, e, v
            else:
                it[1] = max(it[1], b)
                it[2] = max(it[2], e)
                it[3] = max(it[3], v)
                it[4] = "1" if it[4] == "1" or t == "1" else "0"
                it[5] = "1" if it[5] == "1" or w == "1" else "0"
            if it != before:
                changed = True
            merged_cnt += 1
        elif not freq_only:
            item = [q, b, e, v, t, w]
            existing.append(item)
            index[k] = item
            added += 1
            changed = True

    if not changed:
        # тот же файл, те же цифры — ничего не пересчитываем и не трогаем
        return {"added": 0, "merged": merged_cnt, "rows": len(existing),
                "freq_only": freq_only, "ignored_new": 0, "no_change": True}

    with open(pdir / "keys.csv", "w", encoding="utf-8-sig", newline="") as f:
        w2 = csv.writer(f, delimiter=";")
        w2.writerow(["Запрос", "Базовая частотность", "Точная частотность",
                     "Очень точная частотность", "Топоним", "Вопрос"])
        w2.writerows(existing)

    # деревья сносим ТОЛЬКО если реально добавились новые фразы: изменение одних
    # частот не ломает ни эмбеддинги, ни деревья — прогон лишь пере-генерит
    # queries.json с новыми суммами (бесплатно, всё из кэшей)
    if added > 0:
        ddir = pdir / "data"
        if ddir.exists():
            for fb in ddir.glob("*.bin"):
                fb.unlink(missing_ok=True)
            (ddir / "meta.json").unlink(missing_ok=True)

    entry = f"{time.strftime('%F')} " + (f"+{added}" if added else f"частоты×{merged_cnt}")
    # defer_run: при мультизагрузке очередь ставится одним /run после последнего файла,
    # иначе воркер может стартовать между файлами и второй append упрётся в running
    status_sql = "" if body.get("defer_run") else "status='queued', task='cluster', "
    with db() as c:
        c.execute(f"UPDATE projects SET rows=?, {status_sql}"
                  "error='', retries=0, history=CASE WHEN history='' THEN ? "
                  "ELSE history || ' · ' || ? END WHERE id=?",
                  (len(existing), entry, entry, pid))
    return {"added": added, "merged": merged_cnt, "rows": len(existing),
            "freq_only": freq_only,
            "ignored_new": len(new_rows) - merged_cnt - added if freq_only else 0}


@router.post("/projects/{pid}/clone")
async def clone_project(pid: str, user: sqlite3.Row = Depends(current_user)):
    """Полная копия проекта: файлы (ядро, кэши, деревья, интенты) + настройки
    просмотрщика (prefs/корзина/правила/оценки). Оригинал остаётся нетронутым."""
    row = own_project(pid, user)
    if row["status"] == "running":
        raise HTTPException(400, "Дождись окончания обработки")
    new_pid = secrets.token_hex(8)
    src, dst = project_dir(row), PROJECTS / str(user["id"]) / new_pid
    shutil.copytree(src, dst)
    with db() as c:
        c.execute("INSERT INTO projects(id,user_id,name,status,task,rows,uniq,clusters,"
                  "cost_rub,labeled,error,created,costs,variants,retries,history,target_geo) "
                  "SELECT ?,user_id,?,status,task,rows,uniq,clusters,cost_rub,labeled,'',"
                  "?,costs,variants,0,history,target_geo FROM projects WHERE id=?",
                  (new_pid, f"{row['name']} — копия", time.strftime("%F %T"), pid))
        # настройки просмотрщика: sem_prefs / sem_trash / sem_rules / sem_pres
        for key in ("sem_prefs", "sem_trash", "sem_rules", "sem_pres"):
            c.execute("INSERT OR REPLACE INTO user_prefs(user_id,key,value,updated) "
                      "SELECT user_id, ?, value, ? FROM user_prefs "
                      "WHERE user_id=? AND key=?",
                      (f"{key}:{new_pid}", time.strftime("%F %T"),
                       user["id"], f"{key}:{pid}"))
    return {"id": new_pid}


@router.post("/projects/{pid}/rename")
async def rename_project(pid: str, request: Request,
                         user: sqlite3.Row = Depends(current_user)):
    own_project(pid, user)
    body = await request.json()
    name = str(body.get("name") or "").strip()[:80]
    if not name:
        raise HTTPException(400, "Пустое название")
    with db() as c:
        c.execute("UPDATE projects SET name=? WHERE id=?", (name, pid))
    return {"name": name}


@router.delete("/projects/{pid}")
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


@router.get("/projects/{pid}/data/{fname}")
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
