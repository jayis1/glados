# Measurements

Every number quoted anywhere in this repository comes from a file in
[`raw/`](raw/), as the tool emitted it. Nothing here is retyped, rounded for
readability, or reconstructed afterwards. If a claim in the README is not
backed by one of these, that is a bug.

All taken on the deployment host — a 2013 Xeon E5-2690 v2 (20 CPUs across two
NUMA nodes, only 10 of them full physical cores), one GTX 1660 SUPER, two T400
4 GB — on 2026-10-07, except the two files dated 2026-10-08 in the end-to-end
table below.

## The fly

| file | what it settles |
|---|---|
| [`raw/reach.log`](raw/reach.log) | **Start here.** Share of each readout's input weight traceable to each sensory population, per hop. This is the file that says all 4,102 photoreceptors reach nothing, and that mechanosensory has a 40× stronger path into the escape circuit than hearing (9.4e-2 vs 2.4e-3). `0` means no path of that length exists at all. |
| [`raw/bench-split.log`](raw/bench-split.log) | One T400 vs two. 8.918 / 8.889 ms per step alone, **4.575 together** — 1.94× of the ideal 2.00×. |
| [`raw/calibration.log`](raw/calibration.log), [`raw/calibration-pass1.log`](raw/calibration-pass1.log) | Each axis's lo/hi, measured by saturating each sensory site rather than chosen to look tidy. Pass 1 is kept because pass 2 superseded it. |
| [`raw/sweep-gain.json`](raw/sweep-gain.json), [`raw/sweep-gain2.json`](raw/sweep-gain2.json) | Operating-point sweeps. |
| [`raw/respond.json`](raw/respond.json) | The response matrix: which sensory site moves which readout, and by how much. |
| [`raw/probe.log`](raw/probe.log) | Acceptance probe against the running mood service. |

## The senses

| file | what it settles |
|---|---|
| [`raw/hearing.json`](raw/hearing.json) | The auditory nerve earning its wiring. Five snapshots per drive level, on the live calibrated service. Arousal 0.000 → 0.188, monotonic, 40 sd at the top. Also the valence drift: +0.010 at rest to −0.005 at drive 0.2 — she is measurably annoyed by being talked to. Read [`../docs/HONESTY.md`](../docs/HONESTY.md) before quoting that one; the direction is real, the magnitude is tiny and it is not monotonic. |

## End to end

| file | what it settles |
|---|---|
| [`raw/fly_centred_proof.json`](raw/fly_centred_proof.json) | The fly changes what she says, **with a control arm**: same prompt and conditions to an uncoupled tag, which reports `mood_skipped=model_not_coupled`. Rest → `idle`, temp 0.700. Busy → `alert`, raw arousal 0.1609, temp 0.869. Then decay: alert → stirring → idle over three minutes. 9 assertions. |
| [`raw/one_model_proof.json`](raw/one_model_proof.json) | One name over five engines, tested over the wire only — no imports, no internals — because that is what the claim is about. Includes the control arm that sends all four sense fields to the uncoupled tag and asserts they do nothing. 20 assertions. |
| [`raw/byom_proof.json`](raw/byom_proof.json) | **Bring your own model** (2026-10-08). Two tags built from the same `llama3.2:1b` blob, differing only in their name: `glados-byom-proof` is coupled with no config edit anywhere, and `byom-proof-control` reports `model_not_coupled` with its system message untouched. Proves the fence is the *name* and the coupling is base-independent. 10 assertions. |
| [`raw/fly_centred_proof_after_byom_fix.json`](raw/fly_centred_proof_after_byom_fix.json) | The end-to-end proof re-run (2026-10-08) after the empty-system-prompt fix, as that fix's regression guard. 9/9 again. Also the file showing the arousal ceiling is **not a constant**: raw 0.3493 here against 0.1609 the day before, same script and same drive burst. Read [`../docs/HONESTY.md`](../docs/HONESTY.md) before quoting either number. |

## Device placement for the language model

The thread sweep, which is the whole performance story: Ollama's own default on
this box is **5× slower** than the tuned value, because the cgroup straddles
two sockets.

| file | threads | tok/s |
|---|--:|--:|
| [`raw/text_cpu_default.json`](raw/text_cpu_default.json) | unset | 1.35 |
| [`raw/text_cpu_4t.json`](raw/text_cpu_4t.json) | 4 | 3.88 |
| [`raw/text_cpu_6t.json`](raw/text_cpu_6t.json) | 6 | 5.93 |
| [`raw/text_cpu_8t.json`](raw/text_cpu_8t.json) | 8 | 7.71 |
| [`raw/text_cpu_10t.json`](raw/text_cpu_10t.json) | **10** | **8.18** |
| [`raw/text_cpu_12t.json`](raw/text_cpu_12t.json) | 12 | 7.80 |
| [`raw/text_cpu_20t.json`](raw/text_cpu_20t.json) | 20 (the default) | **1.54** |

And the GPUs, which do not help a 7B on a 64-bit-bus card, because decode is
bandwidth-bound:

| file | placement | tok/s |
|---|---|--:|
| [`raw/text_cpu_10t_long.json`](raw/text_cpu_10t_long.json) | CPU, 10 threads | **7.39** |
| [`raw/text_t400x2.json`](raw/text_t400x2.json) | both T400s, 29/29 layers offloaded | 7.06 |
| [`raw/text_t400x2_fa.json`](raw/text_t400x2_fa.json) | both T400s + flash attention | 7.42 (a no-op) |
| [`raw/text_numa0_6t.json`](raw/text_numa0_6t.json), [`raw/text_numa0_8t.json`](raw/text_numa0_8t.json), [`raw/text_numa0_11t.json`](raw/text_numa0_11t.json) | pinned to one NUMA node | — |

This is also why the language model stays on the CPU: it leaves both T400s
entirely free for the fly, which really does run them at ~98%. Co-locating
would have cost both workloads and gained neither.
