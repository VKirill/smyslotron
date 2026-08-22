"""Реестр вариантов эмбеддингов, цены, пороги и настройки DeepSeek."""

import os

PRICE = {"openai": 0.13, "gemini": 0.15, "voyage": 0.18, "qwen": 0.07,
         "ds_in": 0.28, "ds_out": 0.42}  # $/1M токенов
USD_RUB = 80.0
P_FINE, P_COARSE = 15, 75
DEDUP_SIM_SURE, DEDUP_SIM_FLOOR = 0.97, 0.85
METHODS = {"hard": "complete", "soft": "single", "avg": "average"}

INTENT_PREFIX = "намерение пользователя, который ищет: "

TITLES = {
    "openai": "OpenAI · 3-large",
    "gemini": "Gemini · CLUSTERING",
    "gem_sim": "Gemini · SEM_SIMILARITY",
    "gem_query": "Gemini · RETRIEVAL_QUERY",
    "ensemble": "Ансамбль OpenAI+Gemini",
    "openai1536": "OpenAI · 1536d",
    "gemini768": "Gemini · 768d",
    "lemma": "OpenAI · леммы",
    "intent": "OpenAI · интент-префикс",
    "voyage": "Voyage · 3-large",
    "qwen": "Qwen3 · text-embedding-v4",
}
# производные: (вид, параметры)
DERIVED = {
    "ensemble": ("concat", ["openai", "gemini"]),
    "openai1536": ("trunc", "openai", 1536),
    "gemini768": ("trunc", "gemini", 768),
}
GEMINI_TASK = {"gemini": "CLUSTERING", "gem_sim": "SEMANTIC_SIMILARITY",
               "gem_query": "RETRIEVAL_QUERY"}
# OpenAI-совместимые сторонние провайдеры: (url, env-ключ, модель, батч, доп. параметры, вендор)
COMPAT_API = {
    "voyage": ("https://api.voyageai.com/v1/embeddings", "VOYAGE_API_KEY",
               "voyage-3-large", 400, {"output_dimension": 2048}, "voyage"),
    "qwen": ("https://dashscope-intl.aliyuncs.com/compatible-mode/v1/embeddings",
             "DASHSCOPE_API_KEY", "text-embedding-v4", 10, {"dimensions": 2048}, "qwen"),
}


def ds_params() -> dict:
    """Настройки DeepSeek из окружения (.env): модель, ризонинг, уровень мышления, температура.

    DEEPSEEK_REASONING=1 включает thinking-режим (chain-of-thought до ответа) —
    точнее на спорных интентах, но заметно дороже и медленнее: reasoning-токены
    тарифицируются как выходные. DEEPSEEK_EFFORT (high|max) — глубина мышления.
    """
    p = {"model": os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
         "temperature": float(os.environ.get("DEEPSEEK_TEMP", "0.1"))}
    if os.environ.get("DEEPSEEK_REASONING", "0") == "1":
        p["thinking"] = {"type": "enabled"}
        p["reasoning_effort"] = os.environ.get("DEEPSEEK_EFFORT", "high")
    else:
        p["thinking"] = {"type": "disabled"}
    return p
