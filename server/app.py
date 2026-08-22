"""Сборка FastAPI-приложения: роутеры + startup (init_db, восстановление, воркер)."""

import asyncio

from fastapi import FastAPI

from . import auth, prefs, projects, prompts, uploads
from .config import PROJECTS
from .db import db, init_db
from .worker import worker_loop

app = FastAPI(docs_url=None, redoc_url=None)
for mod in (auth, uploads, projects, prefs, prompts):
    app.include_router(mod.router)


@app.on_event("startup")
async def startup() -> None:
    init_db()
    PROJECTS.mkdir(exist_ok=True)
    with db() as c:  # зависшие после рестарта running -> failed c возможностью перезапуска
        c.execute("UPDATE projects SET status='failed', error='Прервано перезапуском сервера' "
                  "WHERE status='running'")
    asyncio.get_event_loop().create_task(worker_loop())
