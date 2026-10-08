#!/usr/bin/env python3
"""IST-270: measure qwen2.5:7b-instruct decode speed per placement option.

The box is a 2013 Xeon E5-2690 v2 (AVX1, no FMA/AVX2) and the only GPUs with
room for a 7B are the two T400s, which IST-269's fly simulation keeps at ~98%
utilisation in bursts. So a single number is misleading: this samples the
neighbour's GPU utilisation during each run and reports the quiet runs and the
contended runs separately.

  ./bench_placement.py --endpoint 127.0.0.1:11501 --label cpu --num-gpu 0 --runs 3
  ./bench_placement.py --endpoint 127.0.0.1:11599 --label t400x2 --runs 5
"""
import argparse
import json
import statistics
import subprocess
import threading
import time
import urllib.request

PROMPT = ("List the seven deadly sins of homelab administration, one short line each, "
          "in your usual deadpan tone.")
SYSTEM = ("You are GLaDOS from Portal. Deadpan, sarcastic, condescending. Never break "
          "character. Never mention being an AI or LLM.")


def sample_gpu(stop, out):
    while not stop.is_set():
        try:
            raw = subprocess.run(
                ["nvidia-smi", "--query-gpu=index,utilization.gpu,memory.used",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5).stdout.strip()
            row = {}
            for line in raw.splitlines():
                idx, util, mem = [x.strip() for x in line.split(",")]
                row[int(idx)] = (int(util), int(mem))
            out.append(row)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.0)


def one_run(endpoint, num_predict, num_gpu, keep_alive):
    body = {"model": "qwen2.5:7b-instruct", "stream": False,
            "options": {"temperature": 0.85, "num_predict": num_predict},
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": PROMPT}]}
    if num_gpu is not None:
        body["options"]["num_gpu"] = num_gpu
    if keep_alive is not None:
        body["keep_alive"] = keep_alive
    req = urllib.request.Request("http://%s/api/chat" % endpoint,
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    samples = []
    stop = threading.Event()
    t = threading.Thread(target=sample_gpu, args=(stop, samples), daemon=True)
    t.start()
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=1800) as resp:
        out = json.loads(resp.read())
    wall = time.time() - t0
    stop.set()
    t.join(timeout=3)
    neighbour = {}
    for idx in (0, 1, 2):
        vals = [s[idx][0] for s in samples if idx in s]
        neighbour[idx] = max(vals) if vals else None
    return {
        "wall_s": round(wall, 2),
        "load_ms": round(out.get("load_duration", 0) / 1e6),
        "prompt_tokens": out.get("prompt_eval_count"),
        "prompt_eval_s": round(out.get("prompt_eval_duration", 0) / 1e9, 2),
        "out_tokens": out.get("eval_count"),
        "tok_s": round(out["eval_count"] / (out["eval_duration"] / 1e9), 2)
        if out.get("eval_count") and out.get("eval_duration") else None,
        "peak_gpu_util": neighbour,
        "reply_head": (out.get("message", {}).get("content") or "")[:120].replace("\n", " "),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--num-predict", type=int, default=120)
    ap.add_argument("--num-gpu", type=int, default=None)
    ap.add_argument("--keep-alive", default=None)
    args = ap.parse_args()

    runs = []
    for i in range(args.runs):
        r = one_run(args.endpoint, args.num_predict, args.num_gpu, args.keep_alive)
        r["run"] = i + 1
        runs.append(r)
        print(json.dumps({"label": args.label, **r}), flush=True)
    rates = [r["tok_s"] for r in runs if r["tok_s"]]
    if rates:
        print(json.dumps({"label": args.label, "summary": {
            "runs": len(rates), "tok_s_min": min(rates), "tok_s_max": max(rates),
            "tok_s_median": round(statistics.median(rates), 2),
            "first_load_ms": runs[0]["load_ms"]}}), flush=True)


if __name__ == "__main__":
    main()
