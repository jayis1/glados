#!/usr/bin/env python3
"""IST-269 Task 2: settle the LIF operating point against measured output.

    . env.sh
    python3 tune.py sweep
    python3 tune.py respond

`sweep` scans gain x bias x sigma for a regime where the network is neither
silent nor saturated. Both failure modes are real and neither announces itself:

  silent      bias*gain*tau < v_th, so no neuron ever reaches threshold
              without synaptic input, and because the traced connectome is
              net-inhibitory in effect there is no synaptic input to bootstrap
              from. The mood vector reads a constant zero and looks like a
              working service.
  saturated   every neuron fires at the refractory ceiling, 1000/(1+2) =
              333 Hz. The mood vector reads a constant maximum and *also*
              looks like a working service.

`respond` drives each sensory population in turn at several intensities and
prints the readout response matrix - which is what actually decides the mood
mapping, rather than the a priori guesses in the ticket.

One Connectome is built and reused across the whole sweep; gain/bias/sigma are
plain scalars handed to the fused kernel per step, so a new operating point
costs a reset, not a 2-second reload of 24.5M edges.
"""
import argparse
import itertools
import json
import sys

import numpy as np

import engine

SETTLE = 400
WINDOW = 400
CEILING = 1000.0 / (1 + engine.REFRACTORY)


def measure(sim, settle=SETTLE, window=WINDOW):
    sim.step(settle)
    sim.rates_hz()                      # discard the settling window
    sim.step(window)
    return sim.rates_hz()


def fresh(sim, **drive):
    sim.reset()
    sim.set_drive(**{k: 0.0 for k in engine.SENSORS})
    if drive:
        sim.set_drive(**drive)


def verdict(r):
    """A whole-network rate inside the sane band is NOT enough.

    The first sweep over the un-normalised graph found 21 operating points at
    25-57 Hz network mean, and every single one had kenyon_cell, MBON and DAN
    pinned at the 333 Hz ceiling. So judge per readout, not in aggregate.
    """
    net = r[engine.ALL]
    if net < 0.05:
        return "SILENT"
    pinned = [k for k in engine.READOUTS if r[k] > 0.7 * CEILING]
    if pinned:
        return "PINNED:" + ",".join(k[:4] for k in pinned)
    dead = [k for k in engine.READOUTS if r[k] < 0.05]
    if len(dead) >= 3:
        return "DEAD:" + ",".join(k[:4] for k in dead)
    if net > 0.5 * CEILING:
        return "hot"
    return "ok"


def sweep(args):
    """Score every operating point on *responsiveness*, not just liveness.

    A point that is alive and unpinned is still useless if driving 2,635 ORNs
    does not move the readouts, which is exactly what row normalisation alone
    produced. So each point is measured twice - silent, then under a reference
    olfactory stimulus - and ranked by how much the readouts actually moved and
    by how much they differ from each other.
    """
    gains = [float(x) for x in args.gain.split(",")]
    biases = [float(x) for x in args.bias.split(",")]
    sigmas = [float(x) for x in args.sigma.split(",")]
    inhs = [float(x) for x in args.inh.split(",")]
    p0s = [float(x) for x in args.p0.split(",")]
    cols = list(engine.READOUTS)
    devs = engine.available_devices()
    print(f"refractory ceiling {CEILING:.0f} Hz   "
          f"reference stimulus olfactory_ORN={args.stim}\n")
    print(f"{'inh':>6} {'gain':>6} {'bias':>6} {'sigma':>6} {'p0':>6} "
          f"{'net':>7}{'net+':>7}  " + "".join(f"{c[:9]:>10}" for c in cols) +
          f"{'spread':>8}{'resp':>8}  verdict")
    best = []
    for inh in inhs:
        sim = engine.Connectome(devices=devs, inh_scale=inh)
        for gain, bias, sigma, p0 in itertools.product(gains, biases,
                                                       sigmas, p0s):
            sim.gain, sim.bias, sim.sigma, sim.p0 = gain, bias, sigma, p0
            fresh(sim)
            base = measure(sim, args.settle, args.window)
            fresh(sim, olfactory_ORN=args.stim)
            stim = measure(sim, args.settle, args.window)
            v = verdict(base) if verdict(base) != "ok" else verdict(stim)
            # spread: do the five axes carry different numbers at all?
            vals = np.array([base[c] for c in cols])
            spread = float(vals.std() / max(vals.mean(), 1e-9))
            # resp: mean |delta| across readouts, in Hz
            resp = float(np.mean([abs(stim[c] - base[c]) for c in cols]))
            print(f"{inh:6.3f} {gain:6.2f} {bias:6.3f} {sigma:6.3f} "
                  f"{p0:6.4f} "
                  f"{base[engine.ALL]:7.2f}{stim[engine.ALL]:7.2f}  " +
                  "".join(f"{base[c]:10.2f}" for c in cols) +
                  f"{spread:8.2f}{resp:8.2f}  {v}")
            if v == "ok":
                best.append({"inh_scale": inh, "gain": gain, "bias": bias,
                             "sigma": sigma, "p0": p0,
                             "net": base[engine.ALL],
                             "spread": spread, "resp": resp,
                             "base": {k: base[k] for k in engine.READOUT_ROWS},
                             "stim": {k: stim[k] for k in engine.READOUT_ROWS}})
        del sim
        import cupy as cp
        for d in devs:
            with cp.cuda.Device(d):
                cp.get_default_memory_pool().free_all_blocks()

    print(f"\n{len(best)} usable operating points")
    best.sort(key=lambda b: -(b["resp"] + 20.0 * b["spread"]))
    for b in best[:5]:
        print(f"  inh={b['inh_scale']} gain={b['gain']} bias={b['bias']} "
              f"sigma={b['sigma']} p0={b['p0']}  net {b['net']:.2f} Hz  "
              f"spread {b['spread']:.2f}  response {b['resp']:.2f} Hz")
    if args.out:
        json.dump(best, open(args.out, "w"), indent=1)
        print(f"wrote {args.out}")


def respond(args):
    sim = engine.Connectome(devices=engine.available_devices(),
                            inh_scale=args.at_inh)
    print(json.dumps(sim.describe()), file=sys.stderr)
    sim.gain, sim.bias, sim.sigma = args.at_gain, args.at_bias, args.at_sigma
    sim.p0 = args.at_p0
    levels = [float(x) for x in args.levels.split(",")]
    cols = list(engine.READOUTS)
    fresh(sim)
    base = measure(sim, args.settle, args.window)
    print(f"operating point inh={sim.inh_scale} gain={sim.gain} "
          f"bias={sim.bias} sigma={sim.sigma} p0={sim.p0}  "
          f"ceiling {CEILING:.0f} Hz\n")
    print(f"{'stimulus':>16} {'level':>6} {'network':>9}  " +
          "".join(f"{c[:11]:>12}" for c in cols))
    print(f"{'(baseline)':>16} {0.0:6.2f} {base[engine.ALL]:9.2f}  " +
          "".join(f"{base[c]:12.2f}" for c in cols))
    out = {"baseline": {k: base[k] for k in engine.READOUT_ROWS}, "response": {}}
    for name in engine.SENSORS:
        out["response"][name] = {}
        for lv in levels:
            fresh(sim, **{name: lv})
            r = measure(sim, args.settle, args.window)
            out["response"][name][str(lv)] = {k: r[k]
                                              for k in engine.READOUT_ROWS}
            print(f"{name:>16} {lv:6.2f} {r[engine.ALL]:9.2f}  " +
                  "".join(f"{r[c]:12.2f}" for c in cols))
    if args.out:
        json.dump(out, open(args.out, "w"), indent=1)
        print(f"\nwrote {args.out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["sweep", "respond"])
    ap.add_argument("--gain", default="2,5,10,20")
    ap.add_argument("--bias", default="0.015,0.025")
    ap.add_argument("--sigma", default="0.04,0.08")
    ap.add_argument("--inh", default="1.0,1.553")
    ap.add_argument("--p0", default=str(engine.P0))
    ap.add_argument("--stim", type=float, default=0.05)
    ap.add_argument("--settle", type=int, default=SETTLE)
    ap.add_argument("--window", type=int, default=WINDOW)
    ap.add_argument("--at-gain", type=float, default=engine.GAIN)
    ap.add_argument("--at-bias", type=float, default=engine.BIAS)
    ap.add_argument("--at-sigma", type=float, default=engine.SIGMA)
    ap.add_argument("--at-inh", type=float, default=engine.INH_SCALE)
    ap.add_argument("--at-p0", type=float, default=engine.P0)
    ap.add_argument("--levels", default="0.01,0.02,0.05,0.1")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if not engine.available_devices():
        sys.exit("no T400 visible")
    (sweep if args.mode == "sweep" else respond)(args)


if __name__ == "__main__":
    main()
