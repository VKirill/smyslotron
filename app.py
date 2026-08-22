"""
Смыслотрон — точка входа бэкенда (совместимость с PM2: `uv run uvicorn app:app`).

Весь код — в пакете server/:
  config · db · auth · uploads · projects · prefs · prompts · worker · app
"""

from server.app import app  # noqa: F401
