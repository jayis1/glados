# Honesty

This project has an unusually high ratio of *sounds amazing* to *is amazing*,
and the real version is genuinely more interesting than the oversold one. So
every claim that could drift upward is pinned down here, along with the ones we
have already had to withdraw.

If you find a claim in this repository that is not supported by a file in
[`../measurements/raw/`](../measurements/raw/), that is a bug. Please open an
issue.

---

## Claims, and exactly how far they go

### "An AI model whose moods come from a fruit fly"

**True, with the scope stated.** A 164,587-neuron simulation of the Janelia
MaleCNS connectome runs continuously; its population firing rates are read out,
mapped onto five calibrated axes, and those axes change (a) one appended clause
in the system message and (b) the sampling temperature, within the caller's
own band. That is the entire causal channel. It is a real channel —
[`fly_centred_proof.json`](../measurements/raw/fly_centred_proof.json) measures
it end to end with a control arm — and it is a *narrow* one.

What it is **not**: the fly does not choose her words, pick her topics, or
contribute anything to the text beyond a mood label and a temperature nudge.

### "We made our own model"

**True in a specific and limited sense.** We define the persona, the sampling
band, the device placement, the senses and — through the coupling — the state.
We did **not** train any weights. The language organ is `qwen2.5:7b-instruct`,
roughly 7.6 × 10⁹ parameters fitted by someone else on roughly 10¹³ tokens.

We also *could not* have trained one here, and the reasons are worth stating
because they are structural rather than budgetary:

- A fly has no language circuit to borrow. There is nothing in the connectome
  that could be fine-tuned toward English.
- Connectome edge weights are **synapse counts** from electron microscopy, not
  learned parameters. Nothing in layer 1 is differentiable in the way training
  requires.
- The simulation runs at 0.22× wall-clock. That is fine for having moods and
  hopeless for gradient descent.

### "One model that sees, hears, thinks and speaks"

**One name, one API, one personality over five engines — not one set of
weights.** A decoder-only transformer, a ViT captioner, an encoder–decoder ASR
model and an ONNX vocoder have different architectures, tokenisers and output
spaces. You cannot concatenate them into a file. Models that genuinely are
single-weight multimodal are *trained* that way from the start.

The claim we make is about the API surface, and
[`one_model_proof.json`](../measurements/raw/one_model_proof.json) tests it
over the wire only — no imports, no internals — because that is what the claim
is about.

### "The fly's state is continuous and never resets"

**True, and it is the point.** Membrane potentials persist between requests.
There is no per-conversation state object being initialised. Restarting the
service does reset the simulation, which is the honest caveat: she is
continuous *while running*, not immortal.

### "She is measurably annoyed by being talked to"

**True, and it is the single best thing in this repository**, so it deserves
the most scrutiny. Under sustained drive into the auditory nerve, valence goes
from +0.010 at rest to −0.005 at drive 0.2
([`hearing.json`](../measurements/raw/hearing.json)).

The honest framing: the *direction* is real and reproducible, the *magnitude*
is tiny — a span of about 0.015 against a noise floor of 0.0021 — and the
response is **not monotonic** (it recovers to +0.003 at drive 0.4). That is
precisely why valence is **not** in the rescaling table and does **not** drive
her state line. It is a real finding we declined to build on.

Calling it "she gets annoyed when you talk to her" is a fair description of a
measured effect in a simulated nervous system. Calling it an emotion is not
something we can support.

### "It is a simulated fly having experiences"

**We do not claim this, in either direction.** It is a sparse matrix being
multiplied. We have no idea what, if anything, that is like, and this
repository is not evidence either way.

---

## The dead input, which is why we measure first

**All 4,102 photoreceptors, driven to full saturation, move no readout at
all.** The optic paths are not in the traced subset. If we had wired the
doorstep camera to `photoreceptor` because the name was obviously right, we
would have shipped a sensory pathway that did nothing, demonstrated it
confidently, and never known.

Hence the rule, enforced by [`probe_hearing.py`](../fly/probe_hearing.py):
**a plausible sensory site name is not evidence.** Prove the site moves a
readout before wiring anything to it.

## The rescaling, which is why "it works" needed a second look

Raw arousal cannot exceed ~0.19 through the auditory nerve. The state
thresholds read like fractions of a 0–1 axis, so every threshold at 0.20 and
above was **unreachable**. Left alone, she would have read `idle` forever: a
coupling that was live, wired, tested, and completely inert.

Rescaling against the measured ceiling is what makes the state line mean
anything. Only axes with a clean monotonic ladder are rescaled; an axis with no
measurement is passed through unscaled rather than guessed at.

## `agitation` is inverted

1.0 is undisturbed and coiled. 0.0 is maximally driven. A resting value of 0.85
means *calm*. Reading it as "agitated" inverts her entire emotional state, and
it is the easiest mistake to make against this service — so it is flagged in
the README, in `mood.py`, and here.

---

## Withdrawn claims

Kept rather than deleted, because a project that only publishes its wins is not
publishing measurements.

| claim | status |
|---|---|
| "The two T400s do 13.5 tok/s on the 7B, against 0.5 on the CPU." | **Withdrawn.** Does not reproduce. Three reps, variance under 0.03, full 29/29 offload confirmed in the loader log: **7.06 tok/s**, which is within noise of the tuned CPU's 7.39. The 0.5 figure was the *untuned* CPU default, since measured at 1.35–1.54. |
| "Full GPU offload is the fast path for the language model." | **Withdrawn.** A T400 is a 64-bit-bus card and decode is bandwidth-bound. Flash attention changes nothing at these context lengths (7.42 vs 7.06, i.e. a no-op). The text model stays on the CPU, which also leaves both cards free for the fly. |
| "More threads are faster." | **Withdrawn, emphatically.** 10 threads: 8.18 tok/s. 20 threads (Ollama's own default on this box): **1.54 tok/s**. The cgroup straddles two NUMA nodes with only 10 full physical cores, so every extra thread buys cross-socket memory traffic. Raising it is not a free win; it collapses. |

---

## Credit where the actual science is

The connectome is **MaleCNS v1.0**, from Janelia FlyEM and Google Research,
released under **CC-BY 4.0** — thousands of hours of electron microscopy and
neuron tracing by people who were not thinking about voice assistants. That is
the scientific achievement in this repository. We downloaded it and were rude
with it.

<https://male-cns.janelia.org/>
