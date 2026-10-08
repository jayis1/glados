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

### "Bring your own model — the fly can drive anything"

**True, with one measured caveat about what "drive" means.** `byom.sh` builds a
GLaDOS on any base Ollama will accept, and the fly reaches it with no config
edit and no restart, because the coupling is fenced by name prefix.
[`byom_proof.json`](../measurements/raw/byom_proof.json) measures it on
`llama3.2:1b` — a different model family, a seventh of the size — with a
control arm that is **the same weights blob under a non-matching name**, and
which is correctly left alone. 10/10.

The caveat: "drive" means one appended clause and a temperature nudge, exactly
as it does for the deployed model. A bigger base does not get a bigger channel.
It renders the same five axes more fluently, and `arousal` is still the only
one of them usefully driven today.

What we have **not** measured: whether any particular large model is *better*
at being her. We own a 2013 Xeon. The claim is that the swap is one command,
that the fly follows it, and that an unmatched name does not get coupled by
accident — which is what the proof covers.

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

Raw arousal did not exceed ~0.19 anywhere in the calibration sweep of the
auditory nerve. The state thresholds read like fractions of a 0–1 axis, so
every threshold at 0.20 and above was **unreachable**. Left alone, she would
have read `idle` forever: a coupling that was live, wired, tested, and
completely inert.

Rescaling against the measured ceiling is what makes the state line mean
anything. Only axes with a clean monotonic ladder are rescaled; an axis with no
measurement is passed through unscaled rather than guessed at.

**That ceiling is a measurement, not a constant, and it moved.** The sweep in
[`hearing.json`](../measurements/raw/hearing.json) topped out at 0.188 and the
first end-to-end proof saw 0.1609 under load. A re-run a day later — same
service, same script, same ten-request burst into the same auditory nerve at
drive 0.350 — reached **0.3493**
([`fly_centred_proof_after_byom_fix.json`](../measurements/raw/fly_centred_proof_after_byom_fix.json)) —
the network had been running continuously in between, and it is a continuously
running network, which is the entire point of it. So the honest version of the
rescaling claim is *"it makes the thresholds reachable"*, not *"0.19 is the
maximum"*. A value above the measured span clips at 1.0 rather than overflowing,
which is why a ceiling that moves upward degrades into a state line that
saturates, and never into a crash or a nonsense axis.

## The defect the BYO work turned up: the BANANA incident

Reported here rather than quietly fixed, because the symptom was
indistinguishable from "working".

A request-level system prompt **overrides** the model's own at the backend. The
coupling appended its state clause to whatever system prompt was in the body —
including an **empty string**, which is what `ollama run <tag>` sends on
`/api/generate` to mean *"I have no prompt of my own, use the model's"*.
Appending to `""` made it non-empty, and therefore an override, and therefore
her entire persona was replaced by four words of mood.

Measured on a probe model whose entire SYSTEM was *"always answer with exactly
the single word BANANA, nothing else"*:

| request path | reply |
|---|---|
| straight to the backend, no coupling | `BANANA` |
| through the coupling, before the fix | `The capital of France is Paris.` |
| through the coupling, after the fix | `BANANA` |

The persona was gone, and the only symptom was a GLaDOS who answered
helpfully — which is the hardest kind of bug to notice in a project whose
output is supposed to be unpredictable.

Fixed by treating an empty or whitespace-only system prompt as *no* system
prompt: the body is left byte-identical and the event says
`mood_skipped: no_system_message`. Eight regression checks in
[`test_mood.py`](../server/test_mood.py) cover both spellings of empty and both
`/api/chat` and `/api/generate`. The principle it restores: **a mood is an
addition to who she is, never a replacement for it.**

The practical consequence, stated plainly because it is a real limitation:
`ollama run <tag>` from a terminal gets her persona and her sampling but **no
state clause**. Send a system message of your own — every n8n node and every
OpenAI-compatible SDK does — and the coupling applies in full. See
[BYOM.md](BYOM.md).

## The defect publishing turned up: the published model failed her own name test

Same shape as the BANANA incident — caught before anyone could hit it, but only
because the publication step forced us to say the model's real name out loud.

The fly-to-model coupling is fenced by name prefix: anything called `glados*`
gets the fly. The check compared the **whole** model string. A model pulled from
a registry keeps its namespace, so the moment she went out as `jais/GLaDOS`,
every machine that pulled her would have asked "does `jais/glados` start with
`glados`?", got **no**, and run her uncoupled. Measured on the live service
before the fix:

| model string | coupled, before | coupled, after |
|---|---|---|
| `glados`, `glados:latest`, `GLaDOS` | yes | yes |
| `jais/GLaDOS`, `jais/GLaDOS:latest` | **no** | yes |
| `hf.co/user/glados:Q4_K_M` | **no** | yes |
| `glados/qwen2.5:7b-instruct` | **yes** | no |
| `qwen2.5:7b-instruct`, `jais/not-glados` | no | no |

Note the fourth row. Comparing the whole string was not merely too strict, it was
also too loose in the other direction: it coupled somebody else's ordinary model
because the *namespace* happened to be spelled `glados`. Matching the part after
the last `/` fixes both, and is the same comparison it always was for every tag
that already worked, since those have no `/` in them.

The symptom, had it shipped, would have been the worst kind again: she still
answers in character, she simply never has a mood, and the one thing this whole
repository is about is silently absent. Fifteen routing cases now pin it in
[`test_mood.py`](../server/test_mood.py), and the fix was verified through the
real socket — `jais/GLaDOS` logging `mood_applied: true`, with
`qwen2.5:7b-instruct` still logging `model_not_coupled` in the same minute.

## The second defect publishing turned up: we shipped our own hardware

The same shape a third time — an artifact that works, with no symptom worth
noticing — and this one was live on the internet for about two hours before
anybody looked at the manifest instead of the model.

`publish-to-ollama.sh` did `ollama cp glados jais/GLaDOS`, and the local
`glados` is the **deployed** build. Device placement is fitted to this host in
[`Modelfile.cpu-tuned`](../Modelfile.cpu-tuned), and the portable
[`Modelfile`](../Modelfile) says in a comment that those numbers "would
actively hurt you". Parameters travel inside the manifest, so the publication
shipped them anyway. Read straight back out of the registry:

| | parameters the registry served |
|---|---|
| before | `{"num_gpu":0,"num_predict":100,"num_thread":10,...}` |
| after | `{"num_predict":100,"stop":["</s>"],"temperature":0.7}` |

`num_gpu 0` means **do not use the GPU**. Every puller, on any machine, was
getting CPU-only inference with their accelerator idle, plus a thread count
fitted to a 2013 Xeon whose cgroup straddles two NUMA nodes. Nothing errors.
She answers in character. She is simply slow, on hardware its owner bought
specifically so that she would not be — and "an LLM feels slow" is close to the
least likely symptom on earth to get reported as a bug.

Fixed by rebuilding the published tag from the portable `Modelfile` and pushing
again. The model, system, template and license layers are **byte-identical**
across the fix (same `sha256:2bada8a7…` weights blob); only the 92-byte params
layer changed. Verified by deleting the local tag, pulling the published one
back down, and reading the parameters off *that* copy — then talking to it
through the live coupling, which logged `mood_applied: true`.

Two guards so it cannot recur, both in `publish-to-ollama.sh`:

1. If the source tag pins `num_gpu`, `num_thread`, `main_gpu`, `low_vram` or
   `num_batch`, the published tag is **rebuilt from `./Modelfile`** instead of
   copied — rebuild rather than warn, for the same reason `byom.sh` renames
   rather than warns.
2. After the push it fetches the params layer **from the registry** and fails
   if placement is present. Checking what we meant to upload is not the same as
   checking what a stranger receives, and only the second one would have caught
   this.

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
| "Mechanosensory is the axis that would drive novelty, valence and reinforcement properly." | **Withdrawn for `reinforcement`.** Measured on the live engine before wiring: reinforcement is **exactly 0.0000** at every drive level from 0.02 to 0.40 ([`mechanosensory.json`](../measurements/raw/mechanosensory.json)). Hearing is the better of the two there (0.006–0.018) and still too weak to earn a phrase. Arousal (213 sd) and agitation (monotonic, 0.823 → 0.019) hold up; valence is rated usable at 9.0 sd but is still not monotonic. |
| "More threads are faster." | **Withdrawn, emphatically.** 10 threads: 8.18 tok/s. 20 threads (Ollama's own default on this box): **1.54 tok/s**. The cgroup straddles two NUMA nodes with only 10 full physical cores, so every extra thread buys cross-socket memory traffic. Raising it is not a free win; it collapses. |

---

## Credit where the actual science is

The connectome is **MaleCNS v1.0**, from Janelia FlyEM and Google Research,
released under **CC-BY 4.0** — thousands of hours of electron microscopy and
neuron tracing by people who were not thinking about voice assistants. That is
the scientific achievement in this repository. We downloaded it and were rude
with it.

<https://male-cns.janelia.org/>
