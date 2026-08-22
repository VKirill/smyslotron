"""Пути и константы приложения. BASE — корень репозитория (там pipeline.py, .env)."""

from pathlib import Path

BASE = Path(__file__).parent.parent
DB_PATH = BASE / "semantika.db"
PROJECTS = BASE / "projects"
UPLOADS = BASE / "uploads"
ENV_FILE = Path("/home/ubuntu/apps/seo-cluster/.env")  # fallback-источник API-ключей

MAX_ROWS = 100_000      # физический предел: матрица сходства HAC упирается в RAM
MAX_UPLOAD_MB = 200
COOKIE = "sem_sess"
P = "/semantika/api"

KNOWN_VARIANTS = {"openai", "gemini", "gem_sim", "gem_query", "lemma", "intent",
                  "ensemble", "openai1536", "gemini768", "voyage", "qwen"}
