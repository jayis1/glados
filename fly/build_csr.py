#!/usr/bin/env python3
"""IST-266 / GLaDOS layer 1: build the signed sparse connectome.

Turns the Janelia male-CNS flat connectome into a sign-resolved CSR matrix plus a
population index, both saved under data/.

Why the three input files and not just the weights table the ticket linked:

  weights         the graph. We use the *traced-only* variant: the full
                  minconf-0.5 table (1.05 GB) also contains untraced fragments,
                  which are partial reconstructions and add noise with no signal.
  neurotransmitters  the weights table is UNSIGNED. Without a per-neuron
                  transmitter it is impossible to tell excitation from
                  inhibition, every synapse ends up excitatory, the network
                  saturates and the simulation is meaningless. This is the one
                  way to build something that runs and is worthless.
  annotations     names the populations, so sensor input can be injected into
                  real olfactory/visual/auditory neurons rather than arbitrary
                  row indices.

Drosophila sign convention: acetylcholine is excitatory; GABA, glutamate (via
GluCl - glutamate is commonly inhibitory in the fly, unlike mammals) and
histamine are inhibitory; the monoamines are modulatory and are left at zero
here rather than guessed at.

Measured on the GPU host, 2026-10-07:
    25,563,197 edges -> 24,539,704 signed non-zero, 164,587 neurons
    CSR = 147.9 MB (indptr 0.66 + indices 98.16 + data fp16 49.08)
"""
import os
import collections
import json
import time

import numpy as np
import pyarrow.feather as feather

DATA = os.environ.get("GLADOS_DATA",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")).rstrip("/") + "/"
WEIGHTS = "connectome-weights-male-cns-v1.0-minconf-0.5-traced-only.feather"
NEUROTRANSMITTERS = "body-neurotransmitters-male-cns-v1.0.feather"
ANNOTATIONS = "body-annotations-male-cns-v1.0-minconf-0.5.feather"

SIGN = {
    "acetylcholine": 1.0,
    "gaba": -1.0,
    "glutamate": -1.0,
    "histamine": -1.0,
    "dopamine": 0.0,
    "octopamine": 0.0,
    "serotonin": 0.0,
}

# Injection sites and mood readouts, by annotation rather than by guesswork.
POPULATIONS = {
    "olfactory_ORN": lambda t, c: isinstance(t, str) and t.startswith("ORN_"),
    "photoreceptor": lambda t, c: isinstance(t, str) and t[:2] in ("R1", "R7", "R8"),
    "hearing_JO": lambda t, c: isinstance(t, str) and t.startswith("JO"),
    "mechanosensory": lambda t, c: isinstance(c, str) and c.startswith("mechanosensory"),
    "gustatory": lambda t, c: c == "gustatory",
    "hygro_thermo": lambda t, c: c in ("hygrosensory", "thermosensory"),
    "kenyon_cell": lambda t, c: c == "Kenyon_Cell",
    "MBON": lambda t, c: c == "MBON",
    "DAN": lambda t, c: c == "DAN",
    "escape_GF": lambda t, c: isinstance(t, str) and (t == "GF" or t.startswith("GFC")),
    "central_complex": lambda t, c: c == "CX",
}


def build():
    t0 = time.time()
    w = feather.read_table(
        DATA + WEIGHTS, columns=["body_pre", "body_post", "weight"], memory_map=True
    )
    pre = w["body_pre"].to_numpy()
    post = w["body_post"].to_numpy()
    wt = w["weight"].to_numpy()
    ids = np.union1d(np.unique(pre), np.unique(post))
    n = len(ids)
    print(f"edges {len(pre):,}  neurons {n:,}  load {time.time() - t0:.1f}s")

    nt = feather.read_table(
        DATA + NEUROTRANSMITTERS, columns=["body", "consensus_nt"], memory_map=True
    )
    nb = nt["body"].to_numpy()
    cons = np.array(nt["consensus_nt"].to_pylist(), dtype=object)
    order = np.argsort(nb)
    nbs, cons = nb[order], cons[order]
    idx = np.clip(np.searchsorted(nbs, ids), 0, len(nbs) - 1)
    hit = nbs[idx] == ids

    sign = np.zeros(n, dtype=np.float32)
    known = np.zeros(n, dtype=bool)
    for i, (h, c) in enumerate(zip(hit, cons[idx])):
        if h and isinstance(c, str) and c in SIGN:
            sign[i] = SIGN[c]
            known[i] = True
    print(
        f"signed {known.sum():,}/{n:,} ({100 * known.sum() / n:.1f}%)  "
        f"exc {(sign > 0).sum():,}  inh {(sign < 0).sum():,}"
    )

    row = np.searchsorted(ids, pre).astype(np.int32)
    col = np.searchsorted(ids, post).astype(np.int32)
    data = (wt.astype(np.float32) * sign[row]).astype(np.float16)
    nz = data != 0
    row, col, data = row[nz], col[nz], data[nz]
    o = np.argsort(row, kind="stable")
    row, col, data = row[o], col[o], data[o]
    indptr = np.zeros(n + 1, dtype=np.int32)
    np.cumsum(np.bincount(row, minlength=n), out=indptr[1:])

    total = indptr.nbytes + col.nbytes + data.nbytes
    print(f"CSR nnz {len(data):,}  {total / 1e6:.1f} MB")
    np.savez(
        DATA + "csr_traced_signed.npz",
        indptr=indptr, indices=col, data=data, ids=ids, sign=sign, known=known,
    )

    a = feather.read_table(
        DATA + ANNOTATIONS, columns=["bodyId", "class", "type"], memory_map=True
    )
    bid = a["bodyId"].to_numpy()
    ty = np.array(a["type"].to_pylist(), dtype=object)
    cl = np.array(a["class"].to_pylist(), dtype=object)
    pos = np.clip(np.searchsorted(ids, bid), 0, len(ids) - 1)
    in_graph = ids[pos] == bid

    pops = {}
    for name, pred in POPULATIONS.items():
        sel = np.array([in_graph[i] and pred(ty[i], cl[i]) for i in range(len(bid))])
        pops[name] = sorted(int(x) for x in pos[sel])
        print(f"  {name:<18} {len(pops[name]):>6,}")
    json.dump(pops, open(DATA + "populations.json", "w"))
    print("wrote csr_traced_signed.npz and populations.json")


if __name__ == "__main__":
    build()
