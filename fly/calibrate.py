#!/usr/bin/env python3
"""IST-269 Task 2: derive the mood axes from measured simulation output.

    . env.sh
    python3 calibrate.py \
        --out data/mood_calibration.json --report measurements/calibration.log

Writes the file moodd.py refuses to start without. Every lo/hi in it is a rate
this simulation was measured producing, because the failure mode this whole
exercise is shaped around is a vector that is pinned at a constant and looks
like a working service either way.

Three things are measured, in this order:

1. NOISE. The same condition, held, sampled `--repeats` times. A readout of 34
   neurons over a 2 s window is a count in the low hundreds, so its sampling
   noise is not negligible and it sets the floor for what counts as a
   response. Reported as the per-readout standard deviation in Hz.

2. RESPONSE. Each sensory population driven alone at each level. This is the
   response matrix that decides the mapping; the axis names in the ticket are
   intentions, and an axis only earns one if its population moves.

3. SNR. (max - min) / noise per readout. An axis whose dynamic range does not
   clear `--min-snr` is written out with `"responsive": false` and its measured
   numbers kept, so the service exposes it as present-but-flat rather than
   quietly scaling noise into a confident 0.0-1.0.
"""
import argparse
import json
import sys
import time

import numpy as np

import engine


# Settled against the measured response matrix and reach.py, not against the
# ticket's table. Four of the five readouts kept the population the ticket
# proposed, because they measurably move:
#
#   novelty        kenyon_cell  <- olfactory_ORN carries 10.6% of KC's total
#                                 input weight at 2 hops; 4.0 -> 138 Hz over
#                                 the drive range
#   valence        MBON         <- olfactory at 3 hops, 7.0%; 4.2 -> 77 Hz
#   reinforcement  DAN          <- olfactory at 3 hops, 7.1%; 3.6 -> 86 Hz
#   agitation      escape_GF    <- mechanosensory at 2 hops, 6.5%; 7.9 -> 4.2
#                                 Hz, i.e. this one is suppressed by touch
#
# arousal did NOT keep central_complex. No sensory population reaches CX with
# more than 0.14% of its input weight within three hops, and measured, CX sat
# at 7.8-8.3 Hz under every one of the 30 stimulus conditions - a 0.5 Hz band
# against a 0.08 Hz noise floor, with no monotone trend in any of them. The
# ticket's intent for the axis was "arousal", and the quantity that actually
# tracks total sensory load is the whole-network mean rate (8.2 -> 16.8 Hz),
# so arousal reads _network. central_complex is still measured, still in
# /mood's rates_hz, and recorded in readout_stats as flat; what it is not is
# an axis that would have published a constant.
AXES = [("arousal", engine.ALL, False),
        ("novelty", "kenyon_cell", False),
        ("valence", "MBON", True),
        ("reinforcement", "DAN", False),
        ("agitation", "escape_GF", False)]


def readout_stats(base, noise, response, levels, min_snr):
    """Measured range and SNR for every readout, axis or not.

    central_complex is the reason this exists separately from the axis table:
    "this population does not respond" is a result worth storing, and storing
    it next to the axes is what keeps the next person from re-deriving the
    mapping from the ticket's intentions.
    """
    sensors = list(response.keys())
    out = {}
    for pop in base:
        vals = [base[pop]] + [response[s][str(lv)][pop]
                              for s in sensors for lv in levels]
        sd = noise.get(pop) or 1e-9
        lo, hi = min(vals), max(vals)
        out[pop] = {"baseline_hz": round(base[pop], 4),
                    "min_hz": round(lo, 4), "max_hz": round(hi, 4),
                    "noise_sd_hz": round(sd, 4),
                    "snr": round((hi - lo) / sd, 2),
                    "responsive": bool((hi - lo) / sd >= min_snr)}
    return out


def build_axes(base, noise, response, levels, describe, args):
    """Measured rates -> axis definitions.

    The scale is logarithmic, which is not a cosmetic choice. Measured response
    of kenyon_cell to olfactory drive, same operating point:

        drive   0.00   0.01   0.02   0.05   0.10
        Hz      4.01   8.69  19.06  56.26  94.49

    i.e. roughly a constant *factor* per step of drive, which is what a rate
    model does. On a linear axis the whole lower half of the stimulus range
    lands in the bottom 10% of the axis and a clear smell reads 0.03 - true,
    and useless to anything consuming the vector. In log units the same five
    points come out 0.00 / 0.22 / 0.44 / 0.74 / 0.89.
    """
    sensors = list(response.keys())
    out, lines = [], []
    lines.append(f"{'axis':>14} {'population':>16} {'lo_hz':>8}{'base':>8}"
                 f"{'hi_hz':>8}{'noise':>8}{'snr':>8}{'scale':>6}  responsive")
    for name, pop, signed in AXES:
        vals = [base[pop]] + [response[s][str(lv)][pop]
                              for s in sensors for lv in levels]
        lo, hi = min(vals), max(vals)
        sd = noise[pop] or 1e-9
        # The floor is the noise sd, not zero: a log axis needs lo > 0, and a
        # rate indistinguishable from the sampling noise is the bottom of the
        # axis by definition.
        lo = max(lo, sd)
        snr = (hi - lo) / sd
        responsive = snr >= args.min_snr
        ax = {
            "name": name,
            "population": pop,
            "neurons": describe["readouts"].get(pop, describe["neurons"]),
            "signed": signed,
            "scale": "log",
            "lo_hz": round(lo, 4),
            "hi_hz": round(hi, 4),
            "baseline_hz": round(base[pop], 4),
            "noise_sd_hz": round(sd, 4),
            "snr": round(snr, 2),
            "responsive": bool(responsive),
        }
        if signed:
            mid = max(base[pop], sd)
            ax["mid_hz"] = round(mid, 4)
            # A signed log axis divides by log(mid/lo) below the midpoint, and
            # MBON's measured minimum (3.711 Hz) is only 0.45 Hz under its
            # resting 4.160 - i.e. the whole measured "suppressed" range IS the
            # noise. Scaling the negative half to it multiplies that noise by
            # ~8: measured, valence read -0.33 and then -0.01 on two successive
            # resting samples. Nothing in 30 stimulus conditions suppressed
            # MBON, so there is no measured negative range to scale to, and the
            # honest choice is to mirror the excitatory span - the same factor
            # of rate change reads the same magnitude either side of rest. A
            # real suppression then shows up proportionately instead of
            # pinning at -1.
            up = np.log(hi / mid)
            if np.log(mid / max(lo, 1e-9)) < 0.25 * up:
                ax["lo_hz_measured"] = round(lo, 4)
                ax["lo_hz"] = round(mid * mid / hi, 4)
                ax["neg_span"] = "mirrored"
                ax["neg_span_reason"] = (
                    "measured suppression range (%.3f Hz) is within the %.3f Hz "
                    "noise floor; negative half mirrors the excitatory span"
                    % (mid - lo, sd))
        # Which way does the axis actually move? For escape_GF the answer is
        # down: nothing measured raises it and mechanosensory drive halves it,
        # so rest is the TOP of the axis. That has to be stated rather than
        # left for a consumer to discover from an "agitation" of 0.87 on an
        # undisturbed network.
        if (hi - base[pop]) < 3 * sd < (base[pop] - lo):
            worst = min(((response[s][str(lv)][pop], s, lv)
                         for s in sensors for lv in levels))
            ax["direction"] = "suppressed_by_stimulus"
            ax["note"] = (
                "rest is the top of this axis: no measured stimulus raises %s, "
                "and %s=%.2f lowers it %.2f -> %.2f Hz. So 1.0 means "
                "undisturbed and 0.0 means maximally driven."
                % (pop, worst[1], worst[2], base[pop], worst[0]))
        out.append(ax)
        lines.append(f"{name:>14} {pop:>16} {ax['lo_hz']:8.3f}"
                     f"{base[pop]:8.3f}{hi:8.3f}{sd:8.3f}{snr:8.1f}"
                     f"{'log':>6}  {responsive}"
                     f"{'  ' + ax['direction'] if ax.get('direction') else ''}")
    return out, lines


def rebuild(args):
    """Recompute the axes from an existing calibration's stored measurements.

    The response matrix costs four minutes of GPU time; changing how an axis is
    *scaled* should not cost it again, and re-measuring would also silently
    change the numbers under the comparison.
    """
    with open(args.rebuild, "r", encoding="utf-8") as fh:
        cal = json.load(fh)
    levels = [float(x) for x in cal["measurement"]["levels"]]
    axes, lines = build_axes(cal["baseline_hz"], cal["noise_sd_hz"],
                             cal["response_hz"], levels,
                             {"readouts": {a["population"]: a["neurons"]
                                           for a in cal["axes"]},
                              "neurons": max(a["neurons"]
                                             for a in cal["axes"])}, args)
    cal["axes"] = axes
    cal["rebuilt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    for line in lines:
        print(line)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(cal, fh, indent=1)
    print(f"\nwrote {args.out} (axes rebuilt from "
          f"{cal.get('generated')} measurements, not re-measured)")


def measure(sim, settle, window):
    sim.step(settle)
    sim.rates_hz()
    sim.step(window)
    return sim.rates_hz()


def fresh(sim, **drive):
    sim.reset()
    sim.set_drive(**{k: 0.0 for k in engine.SENSORS})
    if drive:
        sim.set_drive(**drive)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gain", type=float, default=engine.GAIN)
    ap.add_argument("--bias", type=float, default=engine.BIAS)
    ap.add_argument("--sigma", type=float, default=engine.SIGMA)
    ap.add_argument("--inh", type=float, default=engine.INH_SCALE)
    ap.add_argument("--p0", type=float, default=engine.P0)
    ap.add_argument("--settle", type=int, default=600)
    ap.add_argument("--window", type=int, default=2000)
    ap.add_argument("--repeats", type=int, default=6)
    ap.add_argument("--levels", default="0.02,0.05,0.1,0.2")
    ap.add_argument("--min-snr", type=float, default=3.0)
    ap.add_argument("--out", default="data/mood_calibration.json")
    ap.add_argument("--report", default=None)
    ap.add_argument("--rebuild", default=None,
                    help="rebuild axes from this calibration's stored "
                         "measurements instead of re-measuring")
    args = ap.parse_args()

    if args.rebuild:
        return rebuild(args)

    devs = engine.available_devices()
    if not devs:
        sys.exit("no T400 visible")
    sim = engine.Connectome(devices=devs, gain=args.gain, bias=args.bias,
                            sigma=args.sigma, inh_scale=args.inh, p0=args.p0)
    d = sim.describe()
    cols = list(engine.READOUTS)
    levels = [float(x) for x in args.levels.split(",")]
    out_lines = []

    def emit(line=""):
        print(line, flush=True)
        out_lines.append(line)

    emit(json.dumps(d))
    emit(f"operating point inh={args.inh} gain={args.gain} bias={args.bias} "
         f"sigma={args.sigma} p0={args.p0}   window {args.window} ms   "
         f"ceiling {d['ceiling_hz']:.0f} Hz")

    # ---- 1. noise floor, measured on the resting network ------------------
    fresh(sim)
    sim.step(args.settle)
    sim.rates_hz()
    reps = []
    for _ in range(args.repeats):
        sim.step(args.window)
        reps.append(sim.rates_hz())
    noise = {k: float(np.std([r[k] for r in reps], ddof=1))
             for k in engine.READOUT_ROWS}
    base = {k: float(np.mean([r[k] for r in reps]))
            for k in engine.READOUT_ROWS}
    emit()
    emit(f"{'readout':>16} {'baseline':>9} {'noise_sd':>9}  "
         f"({args.repeats} x {args.window} ms, no stimulus)")
    for c in engine.READOUT_ROWS:
        emit(f"{c:>16} {base[c]:9.3f} {noise[c]:9.3f}")

    # ---- 2. response matrix ----------------------------------------------
    emit()
    emit(f"{'stimulus':>16} {'level':>6} {'network':>9}  " +
         "".join(f"{c[:11]:>12}" for c in cols))
    emit(f"{'(baseline)':>16} {0.0:6.2f} {base[engine.ALL]:9.2f}  " +
         "".join(f"{base[c]:12.2f}" for c in cols))
    response = {}
    for name in engine.SENSORS:
        response[name] = {}
        for lv in levels:
            fresh(sim, **{name: lv})
            r = measure(sim, args.settle, args.window)
            response[name][str(lv)] = {k: r[k] for k in engine.READOUT_ROWS}
            emit(f"{name:>16} {lv:6.2f} {r[engine.ALL]:9.2f}  " +
                 "".join(f"{r[c]:12.2f}" for c in cols))

    # ---- 3. axes ---------------------------------------------------------
    axes, lines = build_axes(base, noise, response, levels, d, args)
    emit()
    for line in lines:
        emit(line)
    stats = readout_stats(base, noise, response, levels, args.min_snr)
    emit()
    emit(f"{'readout':>16} {'min_hz':>9}{'max_hz':>9}{'noise':>9}{'snr':>8}"
         f"  responds to a stimulus")
    for pop, st in stats.items():
        emit(f"{pop:>16} {st['min_hz']:9.3f}{st['max_hz']:9.3f}"
             f"{st['noise_sd_hz']:9.3f}{st['snr']:8.1f}  {st['responsive']}")

    cal = {
        "version": 1,
        "issue": "IST-269",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "generator": "calibrate.py",
        "operating_point": {"gain": args.gain, "bias": args.bias,
                            "sigma": args.sigma, "inh_scale": args.inh,
                            "p0": args.p0},
        "measurement": {"settle_ms": args.settle, "window_ms": args.window,
                        "repeats": args.repeats, "levels": levels,
                        "min_snr": args.min_snr,
                        "devices": d["devices"], "exchange": d["exchange"]},
        "ceiling_hz": d["ceiling_hz"],
        "baseline_hz": {k: round(v, 4) for k, v in base.items()},
        "noise_sd_hz": {k: round(v, 4) for k, v in noise.items()},
        "axes": axes,
        "readout_stats": stats,
        "response_hz": response,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(cal, fh, indent=1)
    emit()
    emit(f"wrote {args.out}")
    flat = [a["name"] for a in axes if not a["responsive"]]
    if flat:
        emit(f"NOT RESPONSIVE: {', '.join(flat)}  "
             f"(snr < {args.min_snr}; the service will report these as flat)")
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out_lines) + "\n")


if __name__ == "__main__":
    main()
