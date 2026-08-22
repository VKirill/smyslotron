"""Морфология (pymorphy3): лемма-группы для дедупа, чанки предлогов, гео, вопросы."""

import re
from functools import lru_cache

_PREPS = {"в", "во", "на", "с", "со", "для", "за", "по", "из", "изо", "у", "к",
          "ко", "от", "ото", "про", "под", "подо", "над", "о", "об", "обо", "при",
          "без", "безо", "через", "до", "возле", "около", "перед", "передо",
          "между", "сквозь", "вместо", "кроме", "ради", "вдоль", "среди"}
_QWORDS = {"как", "что", "почему", "сколько", "где", "когда", "зачем", "какой",
           "какая", "какие", "каков", "чем", "кто", "куда", "откуда", "ли", "можно"}


def analyze(queries):
    """(лемма-группы, chunk-ключи, гео-ключ, вопрос 0/1, лемма-текст) на фразу."""
    import pymorphy3
    morph = pymorphy3.MorphAnalyzer()

    @lru_cache(maxsize=None)
    def parse(w):
        pr = morph.parse(w)[0]
        return pr.normal_form, "Geox" in str(pr.tag)

    groups, chunk_keys, geo_keys, ques, lemma_texts = {}, [], [], [], []
    for i, q in enumerate(queries):
        words = re.findall(r"[а-яёa-z0-9]+", q.lower())
        parsed = [parse(w) for w in words]
        lemmas = [l for l, _ in parsed]
        lemma_texts.append(" ".join(lemmas))
        groups.setdefault(" ".join(sorted(lemmas)), []).append(i)
        geo_keys.append(" ".join(sorted({l for l, g in parsed if g})))
        ques.append(1 if any(l in _QWORDS for l in lemmas) else 0)
        chunks, cur = [], []
        for w in lemmas:
            if w in _PREPS:
                if cur:
                    chunks.append(" ".join(sorted(cur)))
                cur = [w]
            else:
                cur.append(w)
        if cur:
            chunks.append(" ".join(sorted(cur)))
        chunk_keys.append(" | ".join(sorted(chunks)))
    return groups, chunk_keys, geo_keys, ques, lemma_texts
