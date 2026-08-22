"""Дедуп смысловых дублей: жадный выбор представителей внутри лемма-групп.

Контрольные пары при правках порогов/правил:
«молитва матери о дочери» / «молитва дочери о матери» — НЕ склеивать;
«фотосессия семейная» / «семейная фотосессия» — склеивать."""

from .config import DEDUP_SIM_FLOOR, DEDUP_SIM_SURE
from .embed import _norm


def pick_reps(freqs, emb, cand, chunks):
    """Дубль: sim >= SURE, или совпали чанки предлогов и sim >= FLOOR."""
    x = _norm(emb)
    out = {}
    for members in cand.values():
        rest = sorted(members, key=lambda i: -freqs[i])
        while rest:
            rep, tail = rest[0], rest[1:]
            mine, other = [rep], []
            for m in tail:
                sim = float(x[rep] @ x[m])
                ok = sim >= DEDUP_SIM_SURE or (chunks[rep] == chunks[m] and sim >= DEDUP_SIM_FLOOR)
                (mine if ok else other).append(m)
            out[rep] = mine
            rest = other
    return sorted(out), out
