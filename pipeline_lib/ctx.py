"""Контекст прогона: каталог проекта (PDIR) и статус-файл для прогресс-бара."""

import json
from pathlib import Path

PDIR: Path | None = None
_status: dict = {"stage": "", "pct": 0, "done": False}


def init(pdir: Path) -> None:
    global PDIR
    PDIR = pdir


def set_status(stage: str, pct: int, **kw) -> None:
    _status.update({"stage": stage, "pct": pct})
    _status.update(kw)
    (PDIR / "status.json").write_text(json.dumps(_status, ensure_ascii=False))
