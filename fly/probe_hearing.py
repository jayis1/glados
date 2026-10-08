#!/usr/bin/env python3
"""Is "being talked to" a sense this fly actually has? (IST-270 layer 2a)

    python3 probe_hearing.py [--site hearing_JO]

WHY THIS EXISTS

The mood coupling is only worth shipping if something drives the network. The
sensory ingest the plan wanted (doorbell/motion -> mechanosensory, HA deltas ->
hygro_thermo) is blocked on a Home Assistant token. But gladosd's OWN inbound
request traffic needs no credential at all: every n8n call IS an event, and
"someone is talking to me" is the most natural reading of the Johnston's organ
(hearing_JO), the fly's auditory/vibration input. That makes her state move
today instead of after a credential hand-off.

Before wiring it, though, the sense has to be real. This service already has one
dead input: all 4,102 photoreceptors at full drive move no readout, because the
optic paths are not in the traced subset. Wiring traffic to a site like that
would produce a service that looks coupled and is not. So:

  anatomy (measurements/reach.log, operating-point independent) says hearing_JO
  does reach, mostly escape_GF: 8.9e-4 at hop 2 and 2.4e-3 at hop 3. That is
  ~40x weaker than mechanosensory (6.5e-2 / 9.4e-2) but ~10x stronger than
  photoreceptor (0 / 5.5e-6). Nonzero, and the honest expectation is "weak".

  dynamics is what this script measures, and it measures it on the LIVE
  calibrated service rather than from measurements/respond.json - that file was
  recorded at the pre-calibration operating point (its _network baseline is
  1.07 Hz against the current 8.22 Hz), so it cannot answer the question.

WHAT COUNTS AS A PASS

A site is usable if some drive level moves a normalised axis by more than the
calibration's own noise - mood_calibration.json carries noise_sd_hz per
population, so "moved" is measured against that rather than eyeballed. The
verdict is printed per axis per level and the raw JSON is written to
measurements/, so a later run can disagree with evidence.

The network is left at zero drive on the way out, including on Ctrl-C, because
this is the live mood bus and a probe must not leave her permanently stimulated.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:9099"
TOKEN_PATH = os.environ.get("GLADOS_TOKEN",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "token"))
AXES = ("arousal", "novelty", "valence", "reinforcement", "agitation")
OUT = os.environ.get("GLADOS_OUT",
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "measurements", "hearing.json"))


def call(path, payload=None):
    tok = open(TOKEN_PATH).read().strip()
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Authorization": "Bearer " + tok,
                 "Content-Type": "application/json"},
        method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"{}")


def settle_and_read(wait_s, reps=5, gap_s=1.6):
    """Wait out the EMA, then average several snapshots.

    MOOD_SMOOTH_MS=1500 is 1500 *simulated* ms, and the sim runs at 0.21x, so
    the smoother alone is ~7 s of wall clock. Reading once right after a drive
    change reads the previous state, which is the easy way to measure a zero.
    """
    time.sleep(wait_s)
    acc, raw = {a: [] for a in AXES}, []
    for _ in range(reps):
        out = call("/mood")
        vec = out.get("mood") or {}
        for a in AXES:
            if isinstance(vec.get(a), (int, float)):
                acc[a].append(float(vec[a]))
        raw.append(out.get("hz") or out.get("raw_hz") or {})
        time.sleep(gap_s)
    return ({a: (sum(v) / len(v) if v else None) for a, v in acc.items()},
            {a: (max(v) - min(v) if len(v) > 1 else 0.0) for a, v in acc.items()},
            raw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="hearing_JO")
    ap.add_argument("--levels", default="0.02,0.05,0.1,0.2,0.4")
    ap.add_argument("--settle", type=float, default=11.0)
    args = ap.parse_args()
    levels = [float(x) for x in args.levels.split(",") if x.strip()]

    h = call("/health")
    print("live engine: %s  %d neurons  %.3f ms/step  %.3fx realtime"
          % (h.get("engine"), h.get("neurons", 0), h.get("ms_step", 0),
             h.get("realtime", 0)))
    cal = json.load(open(os.path.join(os.environ.get("GLADOS_DATA",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")),
        "mood_calibration.json")))
    # Per-axis noise floor in NORMALISED units: the calibration records each
    # population's noise in Hz plus the lo/hi it was scaled against, so the
    # floor converts rather than being guessed at.
    floor = {}
    for ax in cal["axes"]:
        span = float(ax["hi_hz"]) - float(ax["lo_hz"])
        sd = float(ax.get("noise_sd_hz") or 0.0)
        floor[ax["name"]] = abs(sd / span) if span else 1.0
    print("noise floor (normalised, 1 sd): "
          + "  ".join("%s %.4f" % (a, floor.get(a, 0)) for a in AXES))

    results = {"site": args.site, "levels": {}, "noise_floor": floor,
               "engine": {k: h.get(k) for k in
                          ("engine", "neurons", "edges", "ms_step", "realtime")},
               "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    try:
        print("\n-- rest (drive 0) --")
        call("/drive", {args.site: 0.0})
        rest, jit, _ = settle_and_read(args.settle)
        results["rest"] = rest
        print("   " + "  ".join("%s %.4f" % (a, rest[a]) for a in AXES))

        for lv in levels:
            print("\n-- %s = %.3f --" % (args.site, lv))
            call("/drive", {args.site: lv})
            vec, jit, _ = settle_and_read(args.settle)
            row = {"mood": vec, "jitter": jit, "delta": {}, "sigma": {}}
            for a in AXES:
                d = vec[a] - rest[a]
                row["delta"][a] = d
                row["sigma"][a] = (abs(d) / floor[a]) if floor.get(a) else None
            results["levels"]["%g" % lv] = row
            for a in AXES:
                s = row["sigma"][a]
                mark = "MOVED" if (s is not None and s >= 3.0) else "     "
                print("   %-14s %.4f  d=%+.4f  %6.1f sd  %s"
                      % (a, vec[a], row["delta"][a], s or 0.0, mark))
    finally:
        # Always hand her back at rest, including on an exception or Ctrl-C.
        try:
            call("/drive", {args.site: 0.0})
            print("\ndrive returned to 0")
        except Exception as exc:  # noqa: BLE001
            print("\nWARNING: could not clear drive: %s" % exc, file=sys.stderr)

    best = {}
    for lv, row in results["levels"].items():
        for a in AXES:
            s = row["sigma"][a] or 0.0
            if s > best.get(a, (0.0, None))[0]:
                best[a] = (s, lv)
    results["verdict"] = {a: {"max_sigma": round(v[0], 1), "at_level": v[1],
                              "usable": v[0] >= 3.0}
                          for a, v in best.items()}
    usable = [a for a, v in results["verdict"].items() if v["usable"]]
    results["usable_axes"] = usable
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump(results, fh, indent=1)
    print("\nverdict: %s drives %s" % (args.site, usable or "NOTHING above noise"))
    for a, v in sorted(results["verdict"].items(), key=lambda kv: -kv[1]["max_sigma"]):
        print("   %-14s %6.1f sd at drive %s" % (a, v["max_sigma"], v["at_level"]))
    print("wrote %s" % OUT)
    return 0 if usable else 1


if __name__ == "__main__":
    sys.exit(main())
