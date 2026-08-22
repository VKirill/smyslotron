"""HAC-деревья (complete/single/average → .bin для браузера), гео-разрез
и применение зафиксированного пользователем среза (slice.json)."""

import json
import struct

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

from . import ctx
from .config import METHODS, P_COARSE, P_FINE, TITLES
from .ctx import set_status
from .embed import _norm


def _load_slice(n_reps: int):
    """Зафиксированный пользователем срез (slice.json): метки union-find по дереву
    выбранного варианта/режима + min_size. Возвращает (labels, min_size) или None."""
    try:
        sl = json.loads((ctx.PDIR / "slice.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None
    fbin = ctx.PDIR / "data" / f"{sl.get('variant')}_{sl.get('mode')}.bin"
    if not fbin.exists():
        return None
    raw = fbin.read_bytes()
    m = len(raw) // 12
    k = max(0, min(m, round(float(sl.get("slider", 0)) / 10000 * (n_reps - 1))))
    parent = list(range(n_reps + k))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(k):
        a, b, _ = struct.unpack_from("<iif", raw, i * 12)
        node = n_reps + i
        parent[find(a)] = node
        parent[find(b)] = node
    lab = np.array([find(i) for i in range(n_reps)])
    return lab, max(1, int(sl.get("min_size", 1)))


def apply_slice(fine, r_geo, target_geo):
    """Заменяет автоматические метки на зафиксированный срез (если он сохранён),
    затем гео-разрез; кластеры меньше min_size уходят в -1 («Без группы»)."""
    slc = _load_slice(len(r_geo))
    if slc is not None:
        fine = slc[0]
    fine = split_by_geo(fine, r_geo, target_geo)
    if slc is not None and slc[1] > 1:
        from collections import Counter
        cnt = Counter(int(x) for x in fine)
        fine = np.array([int(x) if cnt[int(x)] >= slc[1] else -1 for x in fine])
    return fine


def split_by_geo(labels, geo, target=""):
    """Кластер с разными гео-ключами режется по гео. target — список синонимов
    целевого региона через запятую («москва, мо, подмосковье»): целевые слова
    вычитаются из гео-ключа фразы, пустой остаток = «наш регион» = без гео.
    «дома под ключ в москве и мо» при таком target склеится с «дома под ключ»,
    а «в казани и москве» останется отдельно (остаток «казань»)."""
    from collections import defaultdict
    tset = {t.strip() for t in target.split(",") if t.strip()}
    if tset:
        geo = [" ".join(sorted(set(g.split()) - tset)) for g in geo]
    seen = defaultdict(set)
    for k, lab in enumerate(labels):
        seen[int(lab)].add(geo[k])
    new_lab = np.asarray(labels).copy()
    remap, nxt = {}, int(np.max(labels)) + 1
    for k, lab in enumerate(labels):
        lab = int(lab)
        if len(seen[lab]) > 1:
            key = (lab, geo[k])
            if key not in remap:
                remap[key] = nxt
                nxt += 1
            new_lab[k] = remap[key]
    return new_lab


def build_all(emb, provider, data_dir, pct_from, pct_to):
    x = _norm(emb)
    dist = 1.0 - x @ x.T
    np.clip(dist, 0, 2, out=dist)
    np.fill_diagonal(dist, 0.0)
    rng = np.random.default_rng(42)
    sample = rng.choice(x.shape[0], size=min(2000, x.shape[0]), replace=False)
    sub = dist[np.ix_(sample, sample)]
    t_fine, t_coarse = np.percentile(sub[np.triu_indices(len(sample), 1)], [P_FINE, P_COARSE])
    cond = squareform(dist, checks=False)
    del dist
    z_avg = None
    for j, (name, method) in enumerate(METHODS.items()):
        set_status(f"Кластеризация: {TITLES.get(provider, provider)} ({name})",
                   pct_from + (pct_to - pct_from) * j // 3)
        z = linkage(cond, method=method)
        buf = bytearray()
        for a, b, d, _ in z:
            buf += struct.pack("<iif", int(a), int(b), float(d))
        (data_dir / f"{provider}_{name}.bin").write_bytes(buf)
        if method == "average":
            z_avg = z
    del cond
    fine = fcluster(z_avg, t=t_fine, criterion="distance")
    coarse = fcluster(z_avg, t=t_coarse, criterion="distance")
    return fine, coarse
