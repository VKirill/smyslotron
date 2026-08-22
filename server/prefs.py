"""KV-настройки на пользователя: sem_prefs:<pid>, sem_tpl, sem_layout и т.д."""

import json
import re
import sqlite3
import time

from fastapi import APIRouter, Depends, HTTPException, Request

from .auth import current_user
from .config import P
from .db import db

router = APIRouter(prefix=P)

PREF_KEY_RE = re.compile(r"^[a-zA-Z0-9_:.\-]{1,80}$")


@router.get("/prefs/{key}")
async def get_pref(key: str, user: sqlite3.Row = Depends(current_user)):
    if not PREF_KEY_RE.fullmatch(key):
        raise HTTPException(400, "Некорректный ключ")
    with db() as c:
        row = c.execute("SELECT value FROM user_prefs WHERE user_id=? AND key=?",
                        (user["id"], key)).fetchone()
    return {"value": json.loads(row["value"]) if row else None}


@router.post("/prefs/{key}")
async def set_pref(key: str, request: Request, user: sqlite3.Row = Depends(current_user)):
    if not PREF_KEY_RE.fullmatch(key):
        raise HTTPException(400, "Некорректный ключ")
    body = await request.json()
    value = json.dumps(body.get("value"), ensure_ascii=False)
    # корзина/правила/оценки на больших ядрах — сотни тысяч фраз; 50 МБ с запасом
    if len(value) > 50_000_000:
        raise HTTPException(400, "Слишком большое значение")
    with db() as c:
        c.execute("INSERT INTO user_prefs(user_id,key,value,updated) VALUES(?,?,?,?) "
                  "ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value, "
                  "updated=excluded.updated",
                  (user["id"], key, value, time.strftime("%F %T")))
    return {"ok": True}
