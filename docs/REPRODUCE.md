# Reproducing her

Two very different levels of effort, so pick your altitude.

| | needs | gets you |
|---|---|---|
| **1. The model** | Ollama, 5 GB disk | GLaDOS, with a fixed personality |
| **2. The fly** | 2 CUDA GPUs (~4 GB each), ~565 MB download, patience | GLaDOS with a continuously running insect nervous system behind her moods |

---

## 1. The model

```bash
./install.sh
```

That is it. It checks Ollama is answering, pulls `qwen2.5:7b-instruct` if you
do not already have it, builds `glados` from [`../Modelfile`](../Modelfile),
and asks her to confirm she is awake.

`FROM` a tag that is already local costs **no extra disk** — Ollama points the
new manifest at the same blobs — and `ollama rm glados` later cannot harm the
base model.

### A note on the two Modelfiles

[`../Modelfile`](../Modelfile) is portable and deliberately specifies **no
device placement**. [`../Modelfile.cpu-tuned`](../Modelfile.cpu-tuned) is the
deployed build, and its pinned `num_thread 10` / `num_gpu 0` are correct for
exactly one 2013 Xeon E5-2690 v2 and likely wrong for your machine. Read it for
the measurements, not for the values.

---

## 2. The fly

### What you actually need

- **Two CUDA GPUs**, roughly 4 GB each. Two T400s are enough and that is what
  this was built on. One card works at half speed — see the table in
  [FLY.md](FLY.md).
- **CuPy**, with a CUDA runtime. The awkward part: CuPy needs **CUDA headers**
  at runtime for JIT, and a plain driver install does not have them. The pip
  CUDA wheels do. [`../fly/env.sh`](../fly/env.sh) points `CUDA_PATH` at a
  shim directory whose `include` symlinks into the wheel's headers, which is
  why `source env.sh` is step zero and not optional.
- **pyarrow**, for the Feather tables.
- ~850 MB of disk for the data plus the derived matrices.

### Steps

```bash
cd fly
./fetch_connectome.sh        # ~565 MB from Janelia, anonymous, CC-BY 4.0
source env.sh                # CUDA header shim + loader path  ← do not skip
python3 build_csr.py         # signed sparse matrix + population index
python3 build_transpose.py   # the step runs against the transpose
```

`build_csr.py` is where the one genuinely dangerous mistake lives. The
published weights table is **unsigned** — it records how many synapses, not
whether they excite or inhibit. Build the matrix without the neurotransmitter
table and every connection becomes excitatory, the network saturates, and you
have something that runs perfectly and means nothing. The sign convention used
(acetylcholine excitatory; GABA, glutamate and histamine inhibitory; monoamines
left at zero rather than guessed) is documented at the top of that file.

### Measure before you believe

```bash
python3 reach.py             # which nerves can move which readouts, per hop
python3 bench_split.py       # one GPU vs two
python3 calibrate.py         # each axis's real lo/hi, by saturating each site
```

**Run `reach.py` first and read it.** It is the file that tells you all 4,102
photoreceptors reach nothing, which is the result that set the rule for this
whole project: a plausible sensory site name is not evidence.

### Serve her moods

```bash
python3 moodd.py             # GET /mood → the five axes
```

Then point layer 2 at it — see [SERVER.md](SERVER.md) and
[`../server/config.example.json`](../server/config.example.json). Set
`mood_coupling: true` and list the model in `mood_models`.

### Prove it works

```bash
python3 ../server/test_mood.py          # fail-open paths, against real sockets
python3 ../server/proof_fly_centred.py  # end to end, with a control arm
```

`test_mood.py` is worth reading even if you never run it. Every failure mode is
exercised against a **real socket** rather than a mock: a closed port, an
unreachable host, a listener that accepts and then hangs forever, a missing
token, a stale snapshot. A mock proves your code handles the failure you
imagined; a socket that accepts and never answers proves it handles the one you
did not.

---

## Things that will bite you

- **`source env.sh` before anything that imports CuPy.** Otherwise the JIT
  fails looking for headers, usually with an error that blames something else.
- **A short download does not crash the build.** It quietly produces a smaller,
  wrong brain. `fetch_connectome.sh` verifies exact byte counts and refuses to
  continue, which is why it has that check at all.
- **Do not share a GPU between the fly and the language model.** It was
  measured: full GPU offload of the 7B lands at 7.06 tok/s against the tuned
  CPU's 7.39, so you pay for both workloads and gain neither.
- **`agitation` is inverted.** 1.0 is calm. See [HONESTY.md](HONESTY.md).
- **Restarting resets the simulation.** Her state is continuous while running,
  not immortal. Post the sensory sites to zero on *start* as well as on exit —
  `SIGTERM` does not run `atexit`, so a restart is the case that actually
  needs covering.
