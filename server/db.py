"""SQLite (WAL): соединение и схема."""

import sqlite3

from .config import DB_PATH


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
        CREATE TABLE IF NOT EXISTS mapping_templates(
            sig TEXT PRIMARY KEY, mapping TEXT NOT NULL,
            uses INTEGER DEFAULT 1, updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS user_prefs(
            user_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
            updated TEXT NOT NULL, PRIMARY KEY(user_id, key));
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
