"""Очередь пайплайна: worker_loop берёт queued-проекты и гоняет pipeline.py subprocess'ом."""

import asyncio
import json
import re
import sqlite3

from .config import BASE, ENV_FILE
from .db import db
from .projects import project_dir


# ключи, которые пользователь может задать сам в «⚙ Настройки» (prefs sem_keys)
USER_KEYS = ("OPENAI_API_KEY", "GEMINI_API_KEY", "VOYAGE_API_KEY", "DASHSCOPE_API_KEY",
             "DEEPSEEK_API_KEY", "VASTAI_API_KEY")


def pipeline_env(user_id: int | None = None) -> dict:
    import os
    env = dict(os.environ)
    for f in (ENV_FILE, BASE / ".env"):  # локальный .env приложения поверх общего
        if f.exists():
            for line in f.read_text().splitlines():
                m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
                if m:
                    env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    if user_id is not None:  # личные ключи пользователя поверх серверных
        with db() as c:
            row = c.execute("SELECT value FROM user_prefs WHERE user_id=? AND key='sem_keys'",
                            (user_id,)).fetchone()
        try:
            keys = json.loads(row["value"]) if row else {}
        except json.JSONDecodeError:
            keys = {}
        for k in USER_KEYS:
            v = str((keys or {}).get(k) or "").strip()
            if v:
                env[k] = v
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
        *args, cwd=BASE, env=pipeline_env(row["user_id"]),
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
            msg = (st.get("error") or (err or b"").decode()[-500:])
            if not msg and proc.returncode in (-9, 137):
                msg = ("Процесс убит системой за нехватку памяти (OOM) на стадии "
                       f"«{st.get('stage', '?')}» — слишком много уникальных смыслов для "
                       "полной матрицы сходства. Сократи ядро (минус-слова, порог частотности) "
                       "или разбей на тематические проекты")
            msg = msg or f"неизвестная ошибка (код {proc.returncode})"
            c.execute("UPDATE projects SET status='failed', error=? WHERE id=?",
                      (msg[:500], row["id"]))
