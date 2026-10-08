#!/usr/bin/env python3
"""IST-269: anatomical reach from each sensory population to each readout.

    python3 reach.py

Pure CPU, scipy-free, a second or two. This exists because the measured
response matrix says some sensory populations move nothing downstream, and
"the simulation does not propagate it" and "the graph does not connect it"
are very different findings. This one answers the second question from the
connectome alone, with no dynamics involved at all.

What it computes, per hop, is the share of a neuron's own total input weight
that traces back to the stimulated population - the same row-normalised
|weight| the simulation uses, so a value of 0.01 means "1% of this neuron's
synaptic input comes from there, at this hop". Summed over a readout and
divided by its neuron count, that is the fraction of that population's input
budget the stimulus can reach, which is an upper bound on any dynamical effect:
no gain makes a population respond to input it does not receive.
"""
import json
import sys

import numpy as np

import engine


def main():
    hops = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    with np.load(engine.CSR_T) as zf:
        indptr = zf["indptr"]
        indices = zf["indices"]
        data = np.abs(zf["data"].astype(np.float32))
        n = int(zf["shape"][0])
    # Row-normalised |weight|: each postsynaptic row sums to 1, so a hop is a
    # weighted average and the numbers stay comparable across hops.
    rows = np.repeat(np.arange(n, dtype=np.int32), np.diff(indptr))
    tot = np.bincount(rows, weights=data, minlength=n)
    tot[tot == 0] = 1.0
    data /= tot[rows]
    del rows
    pops = json.load(open(engine.POPULATIONS))

    def spmv(v):
        out = np.zeros(n, dtype=np.float64)
        np.add.at(out, 0, 0)                      # no-op, keeps dtype explicit
        # row-wise: out[i] = sum_j A[i,j] v[j]
        prod = data * v[indices]
        return np.add.reduceat(prod, indptr[:-1]) * (np.diff(indptr) > 0)

    print(f"share of a readout's input weight traceable to each sensory "
          f"population, per hop ({hops} hops)\n")
    width = 13
    print(f"{'stimulus':>16} {'hop':>4}  " +
          "".join(f"{r[:12]:>{width}}" for r in engine.READOUTS))
    for name in engine.SENSORS:
        v = np.zeros(n, dtype=np.float64)
        v[np.asarray(pops[name], dtype=np.int64)] = 1.0
        for h in range(1, hops + 1):
            v = spmv(v)
            cells = []
            for r in engine.READOUTS:
                idx = np.asarray(pops[r], dtype=np.int64)
                cells.append(f"{v[idx].mean():{width}.3e}")
            print(f"{name:>16} {h:>4}  " + "".join(cells))
    print("\n0 means the stimulus has no path of that length into the "
          "population at all.")


if __name__ == "__main__":
    main()
