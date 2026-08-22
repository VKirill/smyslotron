"""Файлы проекта: чтение keys.csv, имена кластеров, result.csv, costs.json."""

import csv
import json

from . import ctx


def load_keys():
    qs, fb, fe, fv, ft, fq = [], [], [], [], [], []
    with open(ctx.PDIR / "keys.csv", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f, delimiter=";"):
            q = (row.get("Запрос") or "").strip()
            if q:
                qs.append(q)
                fb.append(int(row.get("Базовая частотность") or 0))
                fe.append(int(row.get("Точная частотность") or 0))
                fv.append(int(row.get("Очень точная частотность") or 0))
                ft.append(1 if (row.get("Топоним") or "") == "1" else 0)
                fq.append(1 if (row.get("Вопрос") or "") == "1" else 0)
    primary = fv if any(fv) else (fe if any(fe) else fb)
    return qs, fb, fe, fv, ft, fq, primary


def name_of(labels, r_queries, r_freqs):
    best = {}
    for lab, q, f in zip(labels, r_queries, r_freqs):
        if lab not in best or f > best[lab][0]:
            best[int(lab)] = (f, q)
    return {lab: q for lab, (_, q) in best.items()}


def write_result(rows, intents=None):
    intents = intents or {}
    with open(ctx.PDIR / "result.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Запрос", "Базовая частотность", "Точная частотность",
                    "Очень точная частотность", "Топоним", "Вопрос", "Дубль от",
                    "Кластер", "Имя кластера", "Группа", "Имя группы",
                    "Интент", "Вторичный интент", "Риск смешения", "Тип страницы",
                    "Название кластера (LLM)"])
        for r in rows:
            it = intents.get(r[7], {})
            w.writerow(list(r) + [
                it.get("intent", ""), it.get("secondary", ""),
                {True: "да", False: "нет"}.get(it.get("mixed_risk"), ""),
                it.get("page_type", ""), it.get("name", "")])


def save_costs(usd, n, labeled_clusters=0):
    path = ctx.PDIR / "costs.json"
    prev = {}
    try:
        prev = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        pass
    for k in ("openai_usd", "gemini_usd", "deepseek_usd", "voyage_usd", "qwen_usd", "vast_usd"):
        prev[k] = round(prev.get(k, 0) + usd.get(k.split("_")[0], 0), 4)
    prev["total_usd"] = round(sum(prev.get(k, 0) for k in
        ("openai_usd", "gemini_usd", "deepseek_usd", "voyage_usd", "qwen_usd", "vast_usd")), 4)
    prev["per_query_usd"] = round(prev["total_usd"] / max(1, n), 6)
    if labeled_clusters:
        prev["labeled_clusters"] = labeled_clusters
    if prev.get("deepseek_usd") and prev.get("labeled_clusters"):
        prev["per_cluster_usd"] = round(prev["deepseek_usd"] / prev["labeled_clusters"], 5)
    path.write_text(json.dumps(prev, ensure_ascii=False))
    return prev
