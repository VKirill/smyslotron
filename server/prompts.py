"""Пользовательские промты: прогон групп запросов через DeepSeek."""

import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request

from .auth import current_user
from .config import P
from .worker import pipeline_env

router = APIRouter(prefix=P)


@router.post("/prompt_eval")
async def prompt_eval(request: Request, user: sqlite3.Row = Depends(current_user)):
    """Прогон пользовательского промта по батчу групп запросов через DeepSeek.
    Вход: {prompt, schema?, items: [{id, text}]} (до 40 items за вызов).
    Выход: {items: [{id, ...поля схемы}], usage: {...}}."""
    import httpx
    body = await request.json()
    prompt = str(body.get("prompt") or "").strip()[:4000]
    schema = str(body.get("schema") or "").strip()[:2000]
    items = body.get("items") or []
    if not prompt or not isinstance(items, list) or not 0 < len(items) <= 40:
        raise HTTPException(400, "Нужен prompt и от 1 до 40 items")
    env = pipeline_env()
    key = env.get("DEEPSEEK_API_KEY")
    if not key:
        raise HTTPException(500, "DEEPSEEK_API_KEY не настроен")
    sysmsg = (prompt +
              '\n\nВход: строки вида "id: запросы группы через ;". '
              'Ответ верни СТРОГО одним JSON-объектом вида '
              '{"items": [{"id": <id>, ...}]} — по одному объекту на каждый входной id.' +
              (f"\nФормат полей каждого объекта:\n{schema}" if schema else ""))
    lines = "\n".join(f"{int(it.get('id', 0))}: {str(it.get('text', ''))[:700]}" for it in items)
    payload = {"model": env.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
               "temperature": float(env.get("DEEPSEEK_TEMP", "0.1") or 0.1),
               "response_format": {"type": "json_object"},
               "messages": [{"role": "system", "content": sysmsg},
                            {"role": "user", "content": lines}]}
    if env.get("DEEPSEEK_REASONING", "0") == "1":
        payload["thinking"] = {"type": "enabled"}
        payload["reasoning_effort"] = env.get("DEEPSEEK_EFFORT", "high")
    else:
        payload["thinking"] = {"type": "disabled"}
    async with httpx.AsyncClient(timeout=240) as client:
        r = await client.post("https://api.deepseek.com/chat/completions",
                              headers={"Authorization": f"Bearer {key}"}, json=payload)
    d = r.json()
    if r.status_code != 200:
        raise HTTPException(502, f"DeepSeek: {d.get('error', {}).get('message', r.status_code)}")
    try:
        parsed = json.loads(d["choices"][0]["message"]["content"])
        out = parsed.get("items", [])
    except (json.JSONDecodeError, KeyError, TypeError):
        out = []
    return {"items": out, "usage": d.get("usage", {})}
