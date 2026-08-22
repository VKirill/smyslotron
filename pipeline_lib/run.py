"""Главный поток прогона: дедуп → эмбеддинги → деревья → экспорт (или --label-only)."""

import json
import os
import time

import numpy as np

from . import ctx
from .cluster import apply_slice, base_labels, build_all, save_thresholds, trees_ready
from .config import DERIVED, METHODS, PRICE, TITLES, USD_RUB
from .ctx import set_status
from .dedup import pick_reps
from .embed import EmbStore
from .files import load_keys, name_of, save_costs, write_result
from .llm import deepseek_label_phrases
from .morpho import analyze


async def main(variants: list[str], target_geo: str, label_only: bool) -> None:
    import csv
    usd = {"openai": 0.0, "gemini": 0.0, "deepseek": 0.0, "voyage": 0.0, "qwen": 0.0}
    variants = [v for v in variants if v in TITLES] or ["openai"]
    qs, fb, fe, fv, ft, fq, fp = load_keys()
    n = len(qs)

    set_status("Морфология (дедуп, гео, вопросы)", 3)
    cand, chunks, geo_all, ques_auto, lemma_texts = analyze(qs)
    ques_all = [max(a, b) for a, b in zip(fq, ques_auto)]

    store = EmbStore(qs, lemma_texts, usd)

    # базовый вектор для дедупа и CSV: openai, если выбран, иначе первый базовый
    def first_base():
        for v in variants:
            if v not in DERIVED:
                return v
        kind = DERIVED[variants[0]]
        return kind[1][0] if kind[0] == "concat" else kind[1]

    base_key = "openai" if "openai" in variants else first_base()
    set_status(f"Эмбеддинги: {TITLES[base_key]}", 5)
    emb_base = await store.get(base_key)

    set_status("Дедуп смысловых дублей", 30)
    reps, groups = pick_reps(fp, emb_base, cand, chunks)
    rep_of = {m: rep for rep, ms in groups.items() for m in ms}
    pos = {r: k for k, r in enumerate(reps)}
    rep_idx = np.array(reps)
    r_qs = [qs[i] for i in reps]
    r_fp = [sum(fp[m] for m in groups[i]) for i in reps]
    r_geo = [geo_all[i] for i in reps]

    data_dir = ctx.PDIR / "data"
    data_dir.mkdir(exist_ok=True)

    if label_only:
        # пофразовая разметка: интент присваивается уникальному смыслу один раз
        # и валиден при ЛЮБЫХ настройках среза; вопросы — информационные бесплатно
        r_ques = [max(ques_all[m] for m in groups[i]) for i in reps]
        pmap: dict[str, str] = {}
        try:
            pmap = json.loads((ctx.PDIR / "phrase_intents.json").read_text())
        except (OSError, json.JSONDecodeError):
            pass
        for k, qflag in enumerate(r_ques):
            if qflag and r_qs[k] not in pmap:
                pmap[r_qs[k]] = "информационный"
        todo = [(k, r_qs[k]) for k in range(len(reps)) if r_qs[k] not in pmap]
        set_status("Разметка интентов фраз (DeepSeek)", 15)
        got, tin, tout = await deepseek_label_phrases(todo, os.environ["DEEPSEEK_API_KEY"])
        for k, intent in got.items():
            pmap[r_qs[k]] = intent
        usd["deepseek"] = (tin * PRICE["ds_in"] + tout * PRICE["ds_out"]) / 1e6
        (ctx.PDIR / "phrase_intents.json").write_text(
            json.dumps(pmap, ensure_ascii=False), encoding="utf-8")
        phrase_int = {k: pmap[r_qs[k]] for k in range(len(reps)) if r_qs[k] in pmap}
        (data_dir / "intents.json").write_text(json.dumps(
            {"i": [phrase_int.get(k, "") for k in range(len(reps))]},
            ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

        # кластерные колонки CSV — по фиксированному срезу базового варианта
        set_status("Экспорт CSV", 96)
        quick = base_labels(data_dir, base_key, len(reps))
        if quick is not None:
            fine, coarse = quick  # деревья готовы — режем из .bin за секунды
        else:
            fine, coarse, tf, tc = build_all(emb_base[rep_idx], base_key, data_dir, 96, 98)
            save_thresholds(data_dir, base_key, tf, tc)
        fine = apply_slice(fine, r_geo, target_geo)
        names_f, names_c = name_of(fine, r_qs, r_fp), name_of(coarse, r_qs, r_fp)
        if -1 in names_f:
            names_f[-1] = "Без группы"
        # агрегат по кластеру: dominant / вторичный / риск смешения (>=25% чужого интента)
        agg: dict[int, dict] = {}
        cl_freq: dict[int, dict] = {}
        for k in range(len(reps)):
            lab = int(fine[k])
            d2 = cl_freq.setdefault(lab, {})
            it = phrase_int.get(k, "")
            if it:
                d2[it] = d2.get(it, 0) + max(1, r_fp[k])
        for lab, d2 in cl_freq.items():
            tot = sum(d2.values())
            top = sorted(d2.items(), key=lambda x: -x[1])
            second = top[1] if len(top) > 1 and top[1][1] >= 0.25 * tot else None
            agg[lab] = {"dom": top[0][0], "sec": second[0] if second else "-",
                        "mixed": "да" if second else "нет"}
        with open(ctx.PDIR / "result.csv", "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["Запрос", "Базовая частотность", "Точная частотность",
                        "Очень точная частотность", "Топоним", "Вопрос", "Дубль от",
                        "Кластер", "Имя кластера", "Группа", "Имя группы",
                        "Интент фразы", "Интент кластера", "Вторичный интент кластера",
                        "Риск смешения"])
            for i in range(n):
                k = pos[rep_of[i]]
                lab = int(fine[k])
                a = agg.get(lab, {})
                w.writerow([qs[i], fb[i], fe[i], fv[i],
                            geo_all[i] or ("да" if ft[i] else ""),
                            "да" if ques_all[i] else "",
                            qs[rep_of[i]] if rep_of[i] != i else "",
                            lab, names_f[lab], int(coarse[k]), names_c[int(coarse[k])],
                            phrase_int.get(k, ""), a.get("dom", ""), a.get("sec", ""),
                            a.get("mixed", "")])
        costs = save_costs(usd, n, labeled_clusters=len(got))
        set_status("Готово", 100, done=True,
                   cost_rub=round(sum(usd.values()) * USD_RUB, 2), costs=costs,
                   uniq=len(reps), clusters=len(set(fine)))
        return

    # queries.json (общий для всех вариантов)
    dups = [[qs[m] for m in groups[i] if m != i] for i in reps]
    r_fb = [sum(fb[m] for m in groups[i]) for i in reps]
    (data_dir / "queries.json").write_text(
        json.dumps({"q": r_qs, "f": r_fp, "b": r_fb, "d": dups, "total": n,
                    "g": r_geo,
                    "l": [lemma_texts[i] for i in reps],  # леммы для поиска «любая форма слова»
                    "w": [max(ques_all[m] for m in groups[i]) for i in reps]},
                   ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # интенты из словаря (если размечали) — перевыровнять на новый список репов
    try:
        pmap = json.loads((ctx.PDIR / "phrase_intents.json").read_text())
        (data_dir / "intents.json").write_text(json.dumps(
            {"i": [pmap.get(q, "") for q in r_qs]},
            ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    except (OSError, json.JSONDecodeError):
        pass

    # деревья по каждому варианту; упавший вариант пропускаем, не валя прогон
    fine = coarse = None
    built, skipped = [], []
    span = 55 / max(1, len(variants))
    for vi, vkey in enumerate(variants):
        try:
            if trees_ready(data_dir, vkey):
                if vkey == base_key:
                    quick = base_labels(data_dir, vkey, len(reps))
                    if quick is None:
                        raise RuntimeError("rebuild")  # порогов/размера нет — пересобрать
                    fine, coarse = quick
                built.append(vkey)  # деревья уже есть с прошлого запуска
                continue
            raise RuntimeError("rebuild")
        except RuntimeError:
            pass
        try:
            emb = await store.get(vkey)
            f_, c_, tf, tc = build_all(emb[rep_idx], vkey, data_dir,
                                       int(38 + vi * span), int(38 + (vi + 1) * span))
            save_thresholds(data_dir, vkey, tf, tc)
            built.append(vkey)
            if vkey == base_key:
                fine, coarse = f_, c_
        except Exception as e:
            skipped.append(vkey)
            set_status(f"Вариант {TITLES.get(vkey, vkey)} пропущен: {type(e).__name__}",
                       int(38 + (vi + 1) * span), skipped=skipped)
    if fine is None:  # база не построилась в цикле — считаем отдельно
        fine, coarse, tf, tc = build_all(emb_base[rep_idx], base_key, data_dir, 93, 95)
        save_thresholds(data_dir, base_key, tf, tc)
    fine = apply_slice(fine, r_geo, target_geo)

    thr_keep = {}
    try:
        thr_keep = json.loads((data_dir / "meta.json").read_text()).get("thresholds", {})
    except (OSError, json.JSONDecodeError):
        pass
    (data_dir / "meta.json").write_text(json.dumps(
        {"n": len(reps), "generated": time.strftime("%F %H:%M"),
         "thresholds": thr_keep,
         "providers": {v: {"methods": list(METHODS), "title": TITLES[v]} for v in built}},
        ensure_ascii=False))

    set_status("Экспорт CSV", 96)
    names_f, names_c = name_of(fine, r_qs, r_fp), name_of(coarse, r_qs, r_fp)
    if -1 in names_f:
        names_f[-1] = "Без группы"
    rows = []
    for i in range(n):
        k = pos[rep_of[i]]
        rows.append([qs[i], fb[i], fe[i], fv[i],
                     geo_all[i] or ("да" if ft[i] else ""),
                     "да" if ques_all[i] else "",
                     qs[rep_of[i]] if rep_of[i] != i else "",
                     int(fine[k]), names_f[int(fine[k])],
                     int(coarse[k]), names_c[int(coarse[k])]])
    write_result(rows)
    costs = save_costs(usd, n)
    (ctx.PDIR / "report.md").write_text(
        f"# {ctx.PDIR.name}\n\nФраз: {n} · уникальных смыслов: {len(reps)} · "
        f"кластеров: {len(set(fine))}\n\nВарианты: "
        + ", ".join(TITLES[v] for v in variants)
        + "\n\n## Себестоимость\n"
        f"- Эмбеддинги OpenAI: ${costs['openai_usd']}\n"
        f"- Эмбеддинги Gemini: ${costs['gemini_usd']}\n"
        f"- Разметка DeepSeek: ${costs['deepseek_usd']}\n"
        f"- Итого: ${costs['total_usd']} (~{costs['total_usd'] * USD_RUB:.2f} ₽)\n"
        f"- Цена 1 запроса: ${costs['per_query_usd']}\n", encoding="utf-8")
    set_status("Готово", 100, done=True, skipped=skipped,
               cost_rub=round(sum(usd.values()) * USD_RUB, 2), costs=costs,
               uniq=len(reps), clusters=len(set(fine)))
