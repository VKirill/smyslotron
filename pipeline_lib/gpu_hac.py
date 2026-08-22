"""HAC на GPU (PyTorch, CUDA) алгоритмом ближайшей цепи (nearest-neighbor chain).

Даёт ПОЛНОЕ дерево слияний (n-1 шагов) без порога — тот же формат, что scipy
linkage, те же разбиения. Для complete/average/single NN-chain точен
(reducibility property). Память: одна матрица дистанций n×n (float32 или
float16) в VRAM; обновление после слияния — одна строка/столбец, т.е. O(n)
на шаг, O(n²) суммарно — вместо O(n²) argmax на шаг у наивного HAC.

Запускается на арендованной GPU-машине из REMOTE_GPU_SCRIPT (remote.py) и
может использоваться локально при наличии CUDA. Вход: reps.npy (n×d float32),
выход: {PROVIDER}_{hard,soft,avg}.bin + thr.json.
"""

import json
import struct
import sys
import time

import numpy as np
import torch


def _dist_matrix(x: np.ndarray, dev, dtype) -> torch.Tensor:
    xt = torch.from_numpy(np.ascontiguousarray(x, dtype=np.float32)).to(dev)
    xt = xt / xt.norm(dim=1, keepdim=True)
    n = xt.shape[0]
    D = torch.empty((n, n), device=dev, dtype=dtype)
    step = 8192
    for i in range(0, n, step):  # блоками, чтобы не держать промежуточный float32 n×n
        blk = xt[i:i + step] @ xt.T
        D[i:i + step] = (1.0 - blk).clamp_(0, 2).to(dtype)
    D.fill_diagonal_(0)
    return D


def nn_chain_linkage(D: torch.Tensor, method: str):
    """NN-chain по матрице дистанций D (модифицируется на месте!).
    Возвращает list[(a, b, dist)] в scipy-нумерации (новый узел = n + шаг),
    отсортированный по дистанции (как у scipy)."""
    n = D.shape[0]
    dev = D.device
    INF = torch.tensor(float("inf"), device=dev, dtype=D.dtype)
    size = torch.ones(n, device=dev, dtype=torch.float32)
    active = torch.ones(n, device=dev, dtype=torch.bool)
    node_id = list(range(n))  # индекс строки -> scipy-id узла
    merges = []
    chain: list[int] = []
    D.fill_diagonal_(INF)
    t0 = time.time()
    for step in range(n - 1):
        if not chain:
            chain.append(int(torch.nonzero(active)[0]))
        while True:
            a = chain[-1]
            row = D[a]
            # ближайший активный, при равенстве — меньший индекс (детерминизм)
            row_m = torch.where(active, row, INF)
            b = int(torch.argmin(row_m))
            if len(chain) >= 2 and b == chain[-2]:
                break
            chain.append(b)
        chain.pop(); chain.pop()
        i, j = (a, b) if a < b else (b, a)
        d = float(D[i, j])
        # обновление строки i по правилу связи (Lance–Williams)
        ri, rj = D[i], D[j]
        if method == "complete":
            new = torch.maximum(ri, rj)
        elif method == "single":
            new = torch.minimum(ri, rj)
        else:  # average (UPGMA)
            si, sj = size[i], size[j]
            new = (ri * si + rj * sj) / (si + sj)
        new = torch.where(active, new, INF)
        new[i] = INF
        D[i] = new
        D[:, i] = new
        D[j].fill_(INF)
        D[:, j].fill_(INF)
        active[j] = False
        size[i] = size[i] + size[j]
        merges.append((node_id[i], node_id[j], d))
        node_id[i] = n + step
        if step % 5000 == 0 and step:
            el = time.time() - t0
            print(f"  {method}: {step}/{n - 1} слияний, {el:.0f}с, ~{el / step * (n - 1 - step):.0f}с осталось",
                  flush=True)  # guardian: allow лог скрипта на удалённой машине
    # scipy: слияния упорядочены по неубыванию дистанции, id узлов пересчитываются
    order = sorted(range(len(merges)), key=lambda k: (merges[k][2], k))
    remap = {}
    out = []
    for new_step, k in enumerate(order):
        a, b, d = merges[k]
        a = remap.get(a, a); b = remap.get(b, b)
        out.append((min(a, b), max(a, b), d))
        remap[n + k] = n + new_step
    return out


def build_gpu(reps_path: str, provider: str, p_fine: int, p_coarse: int, out_dir: str = "."):
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    x = np.load(reps_path)
    n = x.shape[0]
    free = torch.cuda.mem_get_info()[0] / 1e9 if dev.type == "cuda" else 1e9
    dtype = torch.float32 if n * n * 4 * 1.3 / 1e9 < free else torch.float16
    print(f"GPU HAC: n={n}, dev={dev}, dtype={dtype}, free VRAM {free:.0f} ГБ", flush=True)  # guardian: allow лог удалённого скрипта
    # пороги — по той же выборке, что и CPU-версия
    D = _dist_matrix(x, dev, torch.float32 if dtype == torch.float32 else torch.float16)
    rng = np.random.default_rng(42)
    s = torch.from_numpy(rng.choice(n, size=min(2000, n), replace=False)).to(dev)
    sub = D[s][:, s].float().cpu().numpy()
    tf, tc = np.percentile(sub[np.triu_indices(len(s), 1)], [p_fine, p_coarse])
    for name, method in (("hard", "complete"), ("soft", "single"), ("avg", "average")):
        Dm = D.clone()
        t0 = time.time()
        Z = nn_chain_linkage(Dm, method)
        del Dm
        torch.cuda.empty_cache()
        buf = bytearray()
        for a, b, d in Z:
            buf += struct.pack("<iif", int(a), int(b), float(d))
        open(f"{out_dir}/{provider}_{name}.bin", "wb").write(buf)
        print(f"built {name} за {time.time() - t0:.0f}с", flush=True)  # guardian: allow лог удалённого скрипта
    json.dump({"tf": float(tf), "tc": float(tc)}, open(f"{out_dir}/thr.json", "w"))
    print("DONE", flush=True)  # guardian: allow лог удалённого скрипта


if __name__ == "__main__":
    build_gpu(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]),
              sys.argv[5] if len(sys.argv) > 5 else ".")
