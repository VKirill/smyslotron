"""Глобальная база эмбеддингов (SQLite, общая для ВСЕХ проектов).

Фраза, оплаченная один раз в любом проекте, дальше берётся отсюда бесплатно —
в новых проектах, при пересозданиях ядра, при пополнениях. Ключ: (вариант, sha1
текста). Пофайловый emb_*.npy в проекте остаётся быстрым локальным слоем."""

import hashlib
import sqlite3
from pathlib import Path

import numpy as np

DB = Path(__file__).parent.parent / "embeddings.db"


def _con() -> sqlite3.Connection:
    c = sqlite3.connect(DB, timeout=120)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE IF NOT EXISTS emb("
              "variant TEXT NOT NULL, h TEXT NOT NULL, v BLOB NOT NULL, "
              "PRIMARY KEY(variant, h))")
    return c


def _h(t: str) -> str:
    return hashlib.sha1(t.encode()).hexdigest()


def fetch(variant: str, texts: list[str]):
    """-> (dict индекс->вектор float32, список индексов-промахов)."""
    con = _con()
    out, missing = {}, []
    hs = [_h(t) for t in texts]
    for i0 in range(0, len(hs), 900):
        chunk = hs[i0:i0 + 900]
        rows = con.execute(
            f"SELECT h, v FROM emb WHERE variant=? AND h IN ({','.join('?' * len(chunk))})",
            [variant, *chunk]).fetchall()
        got = dict(rows)
        for j, h in enumerate(chunk):
            if h in got:
                out[i0 + j] = np.frombuffer(got[h], dtype=np.float32)
            else:
                missing.append(i0 + j)
    con.close()
    return out, missing


def put(variant: str, texts: list[str], mat: np.ndarray) -> None:
    con = _con()
    con.executemany(
        "INSERT OR REPLACE INTO emb(variant, h, v) VALUES(?,?,?)",
        [(variant, _h(t), np.asarray(mat[i], dtype=np.float32).tobytes())
         for i, t in enumerate(texts)])
    con.commit()
    con.close()
