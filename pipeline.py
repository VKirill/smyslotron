# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27", "numpy>=1.26", "scipy>=1.11", "pymorphy3>=2.0"]
# ///
"""
Пайплайн кластеризации одного проекта Semantika Web — точка входа.

Запуск: uv run pipeline.py <project_dir> [--variants a,b,c] [--target-geo "..."] [--label-only]

Вход:  <project_dir>/keys.csv (Запрос;Базовая;Точная;Очень точная;Топоним;Вопрос)
Выход: <project_dir>/data/{queries.json, meta.json, {variant}_{hard|soft|avg}.bin}
       <project_dir>/{result.csv, report.md, emb_*.npy, costs.json, status.json}

Вся логика — в пакете pipeline_lib/:
  ctx · config · morpho · dedup · embed · cluster · llm · files · run
"""

import asyncio
import sys
from pathlib import Path

from pipeline_lib import ctx

ctx.init(Path(sys.argv[1]))


def cli_target_geo() -> str:
    if "--target-geo" in sys.argv:
        i = sys.argv.index("--target-geo")
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1].strip().lower()
    return ""


def cli_variants() -> list[str]:
    if "--variants" in sys.argv:
        raw = sys.argv[sys.argv.index("--variants") + 1]
        return [v for v in raw.split(",") if v]
    return ["openai", "gemini"]


if __name__ == "__main__":
    from pipeline_lib.run import main
    try:
        asyncio.run(main(cli_variants(), cli_target_geo(), "--label-only" in sys.argv))
    except Exception as e:
        ctx.set_status("Ошибка", 0, error=f"{type(e).__name__}: {e}"[:300])
        sys.exit(1)
