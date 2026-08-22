"""Авторизация: scrypt-пароли, cookie-сессии, rate-limit, зависимость current_user."""

import hashlib
import hmac
import re
import secrets
import sqlite3
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .config import COOKIE, P
from .db import db

router = APIRouter(prefix=P)


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


@router.post("/auth/register")
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


@router.post("/auth/login")
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


@router.post("/auth/logout")
async def logout(request: Request, resp: Response):
    tok = request.cookies.get(COOKIE, "")
    if tok:
        with db() as c:
            c.execute("DELETE FROM sessions WHERE token=?", (tok,))
    resp.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
async def me(user: sqlite3.Row = Depends(current_user)):
    return {"email": user["email"]}
