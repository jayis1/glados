#!/usr/bin/env python3
"""IST-269 Task 1: measure the dual-T400 row split against a single T400.

    . env.sh
    python3 bench_split.py

Reports ms/step for each configuration plus the cost of the spike exchange,
which is the only serial part of the 2-GPU step and the thing that decides
whether the split is worth keeping.
"""
import argparse
import statistics
import time

import engine


def timed(devices, steps, warmup, drive):
    sim = engine.Connectome(devices=devices)
    if drive:
        sim.set_drive(olfactory_ORN=0.05)
    sim.step(warmup)
    samples = []
    for _ in range(5):
        t0 = time.perf_counter()
        sim.step(steps // 5)
        samples.append((time.perf_counter() - t0) / (steps // 5) * 1000.0)
    d = sim.describe()
    rates = sim.rates_hz()          # one call: it consumes the window
    mean = rates[engine.ALL]
    del sim
    import cupy as cp
    for dev in devices:
        with cp.cuda.Device(dev):
            cp.get_default_memory_pool().free_all_blocks()
    return {
        "devices": list(devices),
        "ms_step": round(statistics.median(samples), 3),
        "ms_step_min": round(min(samples), 3),
        "ms_step_max": round(max(samples), 3),
        "shards": d["shards"],
        "mean_hz": round(mean, 2),
        "exchange": d["exchange"],
        "rates_hz": {k: round(v, 2) for k, v in rates.items()},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=500)
    ap.add_argument("--warmup", type=int, default=50)
    ap.add_argument("--drive", action="store_true")
    args = ap.parse_args()

    avail = engine.available_devices()
    print(f"T400s visible: {avail}")
    rows = []
    for devices in [(avail[0],), (avail[1],), avail]:
        r = timed(devices, args.steps, args.warmup, args.drive)
        rows.append(r)
        label = "+".join(f"GPU{d}" for d in r["devices"])
        print(f"{label:<12} {r['ms_step']:7.3f} ms/step  "
              f"[{r['ms_step_min']:.3f}-{r['ms_step_max']:.3f}]  "
              f"{1000 / r['ms_step']:6.0f} steps/s  "
              f"{1 / r['ms_step']:5.2f}x realtime   mean {r['mean_hz']:.2f} Hz"
              f"   exch {r['exchange']}")
        for sh in r["shards"]:
            print(f"             GPU{sh['gpu']} rows "
                  f"{sh['rows'][0]:>7,}-{sh['rows'][1]:<7,} nnz {sh['nnz']:>12,}")

    one = min(rows[0]["ms_step"], rows[1]["ms_step"])
    two = rows[2]["ms_step"]
    print(f"\nbest 1-GPU {one:.3f} ms  ->  2-GPU {two:.3f} ms   "
          f"speedup {one / two:.2f}x   "
          f"(ideal 2.00x; overhead {two - one / 2:.3f} ms/step)")


if __name__ == "__main__":
    main()
