#!/usr/bin/env python3
"""IST-266 / GLaDOS layer 1: benchmark the connectome LIF step on each GPU.

    . env.sh
    CUDA_VISIBLE_DEVICES=1 python3 bench.py

env.sh is required: cupy JIT-compiles its kernels through NVRTC, so unlike the
ctranslate2/onnxruntime speech stack on this host it needs CUDA *headers* on
CUDA_PATH, not just the runtime libraries. Without it you get
"Failed to find CUDA headers" at the first elementwise kernel, after the device
has already been opened successfully - so a working device is not evidence the
stack works.

Measured 2026-10-07 on the GPU host, 164,587 neurons / 24,539,704 signed edges:

    GPU0  GTX 1660 SUPER   1.77 ms/step   565 steps/s   0.57x realtime
    GPU1  T400 4GB         9.01 ms/step   111 steps/s   0.11x realtime
    GPU2  T400 4GB         8.98 ms/step   111 steps/s   0.11x realtime
    CPU   20 cores       352    ms/step     2.8 steps/s  0.003x realtime

Graph costs 201 MB of a T400's 3896 MB, so memory is nowhere near the limit;
this is bandwidth-bound, which is why the 1660 SUPER is ~5x the T400.
"""
import os
import time

import cupy as cp
import numpy as np
from cupyx.scipy.sparse import csr_matrix

DATA = os.environ.get("GLADOS_DATA",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")).rstrip("/") + "/"
TAU, VTH, DT, SCALE, REFRACTORY = 20.0, 1.0, 1.0, 0.02, 2


def main(steps=300, warmup=20):
    name = cp.cuda.runtime.getDeviceProperties(0)["name"].decode()
    z = np.load(DATA + "csr_traced_signed.npz")
    n = len(z["indptr"]) - 1
    free0, total = cp.cuda.runtime.memGetInfo()

    a = csr_matrix(
        (cp.asarray(z["data"].astype(np.float32)),
         cp.asarray(z["indices"]), cp.asarray(z["indptr"])),
        shape=(n, n),
    )
    # Transpose once up front: the step needs current per *postsynaptic* neuron,
    # and doing it per step would dominate the measurement.
    at = a.T.tocsr()
    del a
    cp.get_default_memory_pool().free_all_blocks()
    free1, _ = cp.cuda.runtime.memGetInfo()

    v = cp.zeros(n, dtype=cp.float32)
    ref = cp.zeros(n, dtype=cp.int32)

    def step(spk):
        nonlocal v, ref
        current = at.dot(spk)
        v += (-v / TAU + current * SCALE) * DT
        fired = (v >= VTH) & (ref <= 0)
        v = cp.where(fired, 0.0, v)
        ref = cp.where(fired, REFRACTORY, ref - 1)
        return fired.astype(cp.float32)

    spk = cp.zeros(n, dtype=cp.float32)
    spk[:500] = 1.0
    for _ in range(warmup):
        spk = step(spk)
    cp.cuda.Stream.null.synchronize()

    t0 = time.time()
    for _ in range(steps):
        spk = step(spk)
    cp.cuda.Stream.null.synchronize()
    dt = (time.time() - t0) / steps
    free2, _ = cp.cuda.runtime.memGetInfo()

    gpu = os.environ.get("CUDA_VISIBLE_DEVICES", "?")
    print(f"GPU{gpu} {name:<26} {dt * 1000:6.2f} ms/step  {1 / dt:6.0f} steps/s  "
          f"{1 / dt / 1000:5.2f}x realtime at {DT:g} ms dt")
    print(f"     graph {(free0 - free1) / 1e6:.1f} MB   "
          f"in use {(total - free2) / 1e6:.1f} / {total / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
