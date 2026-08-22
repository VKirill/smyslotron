"""Удалённая сборка HAC-деревьев на Vast.ai, когда локальной RAM не хватает.

Система сама: оценивает нужную память (с запасом), ищет самую дешёвую
верифицированную машину с RAM ≥ нужной, поднимает её, заливает вектора
представителей (.npy), строит деревья тем же кодом (build_all), забирает
домой только .bin + пороги и уничтожает машину. Всегда — даже при ошибке.

Env: VASTAI_API_KEY (обязателен), VASTAI_MAX_DPH (макс $/час, дефолт 1.0),
VASTAI_SSH_KEY (путь к приватному ключу, дефолт ~/.ssh/id_ed25519).
"""

import asyncio
import json
import os
import struct
import time
from pathlib import Path

import httpx
import numpy as np

from .ctx import set_status

API = "https://console.vast.ai/api/v0"
RAM_STEPS_GB = (128, 256, 384, 512, 768, 1024)
# проверенная схема из seo-cluster: PyTorch-образ + встроенный шаблон Vast.ai (sshd, numpy, scipy)
IMAGE = "pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime"
TEMPLATE_HASH = "f88cf873a299c0863309bab988ad115c"
# GPU-режим: VASTAI_MODE=gpu (дефолт) — HAC на CUDA алгоритмом NN-chain (pipeline_lib/gpu_hac.py),
# в минуты вместо часов; cpu — scipy на RAM-машине. VRAM под матрицу: n²·4 (f32) или n²·2 (f16)
VRAM_STEPS_GB = (24, 48, 80, 94, 140)  # 4090 / A6000-L40 / A100-H100 / RTX PRO 6000 / H200
SSH_KEY = Path(os.environ.get("VASTAI_SSH_KEY", "/home/ubuntu/.ssh/id_ed25519"))

# скрипт, который выполняется на удалённой машине: вход reps.npy -> деревья .bin + thr.json
REMOTE_SCRIPT = r'''
import json, struct, sys
import numpy as np
from scipy.cluster.hierarchy import linkage
from scipy.spatial.distance import squareform
x = np.load("reps.npy").astype(np.float32)
x /= np.linalg.norm(x, axis=1, keepdims=True)
dist = x @ x.T
np.subtract(1.0, dist, out=dist); np.clip(dist, 0, 2, out=dist); np.fill_diagonal(dist, 0.0)
rng = np.random.default_rng(42)
s = rng.choice(x.shape[0], size=min(2000, x.shape[0]), replace=False)
sub = dist[np.ix_(s, s)]
tf, tc = np.percentile(sub[np.triu_indices(len(s), 1)], [P_FINE, P_COARSE])
cond = squareform(dist, checks=False); del dist
for name, method in (("hard", "complete"), ("soft", "single"), ("avg", "average")):
    z = linkage(cond, method=method)
    buf = bytearray()
    for a, b, d, _ in z: buf += struct.pack("<iif", int(a), int(b), float(d))
    open(f"PROVIDER_{name}.bin", "wb").write(buf)
    print("built", name, flush=True)  # guardian: allow это скрипт для удалённой машины, stdout = его лог
json.dump({"tf": float(tf), "tc": float(tc)}, open("thr.json", "w"))
print("DONE", flush=True)  # guardian: allow удалённый скрипт
'''


def need_gb(n: int) -> float:
    """Оценка пика RAM для полной матрицы n×n: float32-матрица + cond + float64-копия scipy."""
    return n * n * 4 * 2.2 / 1e9


def avail_gb() -> float:
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1e6
    except OSError:
        pass
    return 1e9


def need_vram_gb(n: int, f16: bool = False) -> float:
    """Матрица дистанций + клон под метод + буферы. f16 — вдвое меньше (gpu_hac сам
    переключается на float16, если float32 не влезает в VRAM)."""
    return n * n * (2 if f16 else 4) * 2.3 / 1e9


def pick_vram_step(n: int) -> tuple[int, str]:
    """Ступень VRAM: float32, если есть такая карта; иначе float16 (точность разбиений
    проверена — для HAC хватает). Возвращает (ГБ, 'f32'|'f16')."""
    n32, n16 = need_vram_gb(n), need_vram_gb(n, f16=True)
    for step in VRAM_STEPS_GB:
        if step >= n32 * 1.1:
            return step, "f32"
    for step in VRAM_STEPS_GB:
        if step >= n16 * 1.1:
            return step, "f16"
    return VRAM_STEPS_GB[-1], "f16"


def pick_ram_step(need: float) -> int:
    """Ступень RAM с запасом 25% + 16 ГБ под систему/питон."""
    target = need * 1.25 + 16
    for step in RAM_STEPS_GB:
        if step >= target:
            return step
    return RAM_STEPS_GB[-1]


async def _sh(*args, timeout=600, input_bytes=None):
    p = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.PIPE if input_bytes else None,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(p.communicate(input_bytes), timeout=timeout)
    except asyncio.TimeoutError:
        p.kill()
        raise RuntimeError(f"таймаут {timeout}с: {' '.join(args[:3])}")
    if p.returncode != 0:
        raise RuntimeError(f"{args[0]} rc={p.returncode}: {err.decode()[-400:]}")
    return out


class Vast:
    def __init__(self, key: str):
        self.h = {"Authorization": f"Bearer {key}"}
        self.c = httpx.AsyncClient(timeout=60, headers=self.h)

    async def search(self, ram_gb: int, max_dph: float) -> dict:
        payload = {"verified": {"eq": True}, "rentable": {"eq": True}, "rented": {"eq": False},
                   "cpu_ram": {"gte": ram_gb * 1024}, "dph_total": {"lte": max_dph},
                   "direct_port_count": {"gte": 10}, "reliability": {"gte": 0.95},
                   "inet_down": {"gte": 200}, "disk_space": {"gte": 40},
                   "type": "on-demand", "order": [["dph_total", "asc"]], "limit": 5,
                   "allocated_storage": 40.0}
        r = await self.c.post(f"{API}/bundles/", json=payload)
        r.raise_for_status()
        offers = r.json().get("offers", [])
        if not offers:
            raise RuntimeError(f"на Vast.ai нет машин с RAM ≥ {ram_gb} ГБ до ${max_dph}/ч")
        return offers[0]

    async def ensure_account_key(self) -> None:
        """Ключ должен быть в аккаунте ДО создания инстанса — тогда шаблон его подхватит."""
        pub = SSH_KEY.with_suffix(".pub").read_text().strip()
        r = await self.c.get(f"{API}/ssh/")
        keys = r.json() if r.status_code == 200 else []
        if any((k.get("public_key") or "").strip() == pub for k in keys if isinstance(k, dict)):
            return
        await self.c.post(f"{API}/ssh/", json={"ssh_key": pub})

    async def search_gpu(self, vram_gb: int, max_dph: float) -> dict:
        payload = {"verified": {"eq": True}, "rentable": {"eq": True}, "rented": {"eq": False},
                   "gpu_ram": {"gte": vram_gb * 1024 - 512}, "num_gpus": {"eq": 1},
                   "dph_total": {"lte": max_dph}, "direct_port_count": {"gte": 10},
                   "reliability": {"gte": 0.95}, "inet_down": {"gte": 200},
                   "disk_space": {"gte": 40}, "cuda_max_good": {"gte": 12.0},
                   "type": "on-demand", "order": [["dph_total", "asc"]], "limit": 5,
                   "allocated_storage": 40.0}
        r = await self.c.post(f"{API}/bundles/", json=payload)
        r.raise_for_status()
        offers = r.json().get("offers", [])
        if not offers:
            raise RuntimeError(f"на Vast.ai нет GPU с VRAM ≥ {vram_gb} ГБ до ${max_dph}/ч")
        return offers[0]

    async def create(self, offer_id: int, label: str) -> str:
        await self.ensure_account_key()
        r = await self.c.put(f"{API}/asks/{offer_id}/", json={
            "client_id": "me", "image": IMAGE, "template_hash_id": TEMPLATE_HASH,
            "disk": 40, "runtype": "ssh", "label": label})
        r.raise_for_status()
        d = r.json()
        iid = d.get("new_contract")
        if not iid:
            raise RuntimeError(f"Vast.ai не создал инстанс: {d}")
        return str(iid)

    async def status(self, iid: str) -> dict:
        r = await self.c.get(f"{API}/instances/{iid}/")
        r.raise_for_status()
        d = r.json()
        return d.get("instances") or d

    async def wait_ssh(self, iid: str, timeout: int = 900) -> tuple[str, int]:
        """Endpoint как в vast CLI: прямой public_ipaddr + ports['22/tcp'], иначе прокси."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            st = await self.status(iid)
            if st.get("actual_status") == "running":
                ports = st.get("ports") or {}
                p22 = ports.get("22/tcp") if isinstance(ports, dict) else None
                if p22 and st.get("public_ipaddr"):
                    try:
                        return st["public_ipaddr"], int(p22[0]["HostPort"])
                    except (KeyError, ValueError, TypeError, IndexError):
                        pass
                if st.get("ssh_host") and st.get("ssh_port"):
                    return st["ssh_host"], int(st["ssh_port"])
            await asyncio.sleep(15)
        raise RuntimeError("машина Vast.ai не поднялась за 15 минут")

    async def destroy(self, iid: str) -> None:
        try:
            await self.c.delete(f"{API}/instances/{iid}/")
        except Exception:
            pass


def _opts() -> list[str]:
    return ["-i", str(SSH_KEY), "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=30",
            "-o", "ServerAliveInterval=30"]


def _ssh_base(host: str, port: int) -> list[str]:
    return ["-p", str(port), *_opts()]          # ssh: порт через -p


def _scp_base(host: str, port: int) -> list[str]:
    return ["-P", str(port), *_opts()]          # scp: порт через -P (заглавная!)


async def build_remote(emb: np.ndarray, provider: str, data_dir: Path,
                       p_fine: int, p_coarse: int) -> tuple[float, float]:
    """Строит деревья провайдера на арендованной машине. Возвращает (t_fine, t_coarse)."""
    key = os.environ.get("VASTAI_API_KEY")
    if not key:
        raise MemoryError("локальной RAM не хватает, а VASTAI_API_KEY не задан — "
                          "удалённая сборка недоступна")
    n = emb.shape[0]
    gpu = os.environ.get("VASTAI_MODE", "gpu").lower() == "gpu"
    max_dph = float(os.environ.get("VASTAI_GPU_MAX_DPH" if gpu else "VASTAI_MAX_DPH",
                                   "4.0" if gpu else "1.0"))
    vast = Vast(key)
    if gpu:
        vram, prec = pick_vram_step(n)
        needv = need_vram_gb(n, f16=(prec == "f16"))
        set_status(f"Vast.ai GPU: ищу карту с VRAM ≥ {vram} ГБ ({prec}, нужно ~{needv:.0f} ГБ для {n:,} смыслов)", 40)
        offer = await vast.search_gpu(vram, max_dph)
        desc = f"{offer.get('gpu_name', 'GPU')} {offer.get('gpu_ram', 0) // 1024} ГБ VRAM"
    else:
        need = need_gb(n)
        ram = pick_ram_step(need)
        set_status(f"Vast.ai: ищу машину с RAM ≥ {ram} ГБ (нужно ~{need:.0f} ГБ для {n:,} смыслов)", 40)
        offer = await vast.search(ram, max_dph)
        desc = f"{offer.get('cpu_ram', 0) // 1024} ГБ RAM"
    iid = await vast.create(int(offer["id"]), label=f"smyslotron-{provider}-{int(time.time())}")
    set_status(f"Vast.ai: машина {desc} за ${offer.get('dph_total', 0):.2f}/ч поднимается…", 42)
    t_start = time.time()
    try:
        host, port = await vast.wait_ssh(iid)
        base = _ssh_base(host, port)
        # ждём, пока onstart положит ключ и sshd примет соединение
        for attempt in range(30):
            try:
                await _sh("ssh", *base, f"root@{host}", "echo ok", timeout=60)
                break
            except RuntimeError:
                await asyncio.sleep(10)
        else:
            raise RuntimeError("SSH на машину Vast.ai не отвечает")

        set_status("Vast.ai: ставлю numpy/scipy, заливаю вектора…", 45)
        await _sh("ssh", *base, f"root@{host}",
                  "mkdir -p /work && python -c 'import numpy, scipy, torch' || pip install -q numpy scipy",
                  timeout=600)
        tmp = data_dir / f"_reps_{provider}.npy"
        np.save(tmp, np.asarray(emb, dtype=np.float32))
        try:
            await _sh("scp", *_scp_base(host, port), str(tmp), f"root@{host}:/work/reps.npy", timeout=3600)
        finally:
            tmp.unlink(missing_ok=True)
        if gpu:
            gpu_src = (Path(__file__).parent / "gpu_hac.py").read_bytes()
            await _sh("ssh", *base, f"root@{host}", "cat > /work/gpu_hac.py", input_bytes=gpu_src,
                      timeout=60)
            set_status(f"Vast.ai GPU: строю деревья {provider} на {desc}…", 50)
            await _sh("ssh", *base, f"root@{host}",
                      f"cd /work && python gpu_hac.py reps.npy {provider} {p_fine} {p_coarse} /work",
                      timeout=4 * 3600)
        else:
            script = (REMOTE_SCRIPT.replace("P_FINE", str(p_fine)).replace("P_COARSE", str(p_coarse))
                      .replace("PROVIDER", provider))
            await _sh("ssh", *base, f"root@{host}", "cat > /work/build.py",
                      input_bytes=script.encode(), timeout=60)
            set_status(f"Vast.ai: строю деревья {provider} на {desc}…", 50)
            await _sh("ssh", *base, f"root@{host}", "cd /work && python build.py", timeout=6 * 3600)

        set_status("Vast.ai: забираю деревья…", 90)
        for name in ("hard", "soft", "avg"):
            await _sh("scp", *_scp_base(host, port), f"root@{host}:/work/{provider}_{name}.bin",
                      str(data_dir / f"{provider}_{name}.bin"), timeout=600)
        thr = await _sh("ssh", *base, f"root@{host}", "cat /work/thr.json", timeout=60)
        thr = json.loads(thr.decode())
        # валидация: размер дерева = n-1 слияний
        for name in ("hard", "soft", "avg"):
            size = (data_dir / f"{provider}_{name}.bin").stat().st_size
            if size // 12 != n - 1:
                raise RuntimeError(f"дерево {name} битое: {size // 12} слияний вместо {n - 1}")
        return float(thr["tf"]), float(thr["tc"])
    finally:
        await vast.destroy(iid)
        hours = (time.time() - t_start) / 3600
        cost = hours * float(offer.get("dph_total", 0))
        set_status(f"Vast.ai: машина уничтожена, аренда ~${cost:.2f} ({hours * 60:.0f} мин)", 92,
                   vast_usd=round(cost, 3))
        await vast.c.aclose()
