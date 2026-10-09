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
response is **not monotonic** (it recovers to +0.003 at drive 0.4). It is a
real finding we declined to build on.

One consequence of IST-280 worth stating here, because it makes this finding
*harder* to see rather than easier: valence now has a rescaling span, fitted to
hygro_thermo, which is the nerve that moves it monotonically. Rescaling clips
at 0, and hearing's valence drift is negative, so the drift no longer appears
in the scaled vector at all. It is still recorded on every single request as
`mood_raw` in the event log — which is where it was always measured — but the
state line will never show it, and `displeased` remains an unreachable phrase.

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
It renders the same five axes more fluently — four of which now have a measured
monotonic ladder, since IST-280 wired the door and the thermometer.

What we have **not** measured: whether any particular large model is *better*
at being her. We own a 2013 Xeon. The claim is that the swap is one command,
that the fly follows it, and that an unmatched name does not get coupled by
accident — which is what the proof covers.

### "It is a simulated fly having experiences"

**We do not claim this, in either direction.** It is a sparse matrix being
multiplied. We have no idea what, if anything, that is like, and this
repository is not evidence either way.

---

## The site we expected nothing from, and the one we expected everything from

IST-280's plan named `mechanosensory` as "the axis that would drive novelty,
valence and reinforcement properly", and `hygro_thermo` as a place to dump
temperature readings, "named in the config as the stand-in it is". Probing both
before wiring either inverted that almost exactly.

| | mechanosensory | hygro_thermo |
|---|---|---|
| arousal | **213 sd**, 0 → 1.000 | **exactly 0.0000** at every level |
| agitation | monotonic, 0.823 → 0.019 | unmoved |
| novelty | 7.4 sd, non-monotonic | monotonic, 143 sd |
| valence | 9.0 sd, non-monotonic | monotonic, 173 sd |
| reinforcement | **exactly 0.0000** at every level | monotonic, **218 sd** |

[`mechanosensory.json`](../measurements/raw/mechanosensory.json),
[`hygro_thermo.json`](../measurements/raw/hygro_thermo.json). The throwaway site
is the strongest coupling in this fly, and it is the only thing that moves the
axis which was dead everywhere else. The predicted site drives the two axes the
plan did not ask it for.

Neither of those is a result we would have had if we had wired first. The
withdrawal is recorded in the table at the bottom of this file; the reason it
was catchable at all is the rule below.

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

## The phantom knock, caught by a test rather than by a doorbell

Worth recording because the bug was invisible from the outside and the test
that found it was written to check something slightly different.

Home Assistant's `/api/history/period` returns, for each entity, the state at
the **start of the window** as the first element, then the changes inside it. A
real response makes that obvious — the first point's `last_changed` is exactly
the `since` you asked for — but the ingest counted every `on` it saw, including
that first one. An indoor occupancy sensor is `on` for most of the day, so the
sense would have invented a knock on every service restart, and then a second
one on the next poll whose window happened to open while it was still on.

The symptom would have been a GLaDOS who is mildly alarmed for no reason, which
in this project reads as working correctly. Fixed by treating the first point of
each series as the baseline and never as an event; six counting cases in
[`test_ha_senses.py`](../server/test_ha_senses.py) pin it, including
off→on→off within one window (one event, not two) and already-on-and-reported-
repeatedly (no event).

**That fix was right about the hazard and wrong as written, and it cost the
door sense its entire first day.** See the next section.

## The opposite failure, which the phantom-knock fix caused

The hazard above is real. Skipping the first point to avoid it is not, because
it assumes the first point is always a baseline. A late one is not.

Measured on this install: Home Assistant's `/api/history/period` admits that a
change happened anywhere from 0 to **14.6 seconds** after it did — every sample
in a six-sample run longer than the 5-second poll interval
([`ha_touch_visibility.json`](../measurements/raw/ha_touch_visibility.json)).
The ingest advanced its cursor to *now* on every poll, so a change that
committed after the cursor passed it was never inside a window again. It
reappeared only as the next window's first point, and the first point was being
skipped by design.

The result, read off the live service:

| | |
|---|---|
| polls | 11,631 |
| poll errors | 0 |
| posts to the fly | 1,248, 0 failed |
| thermal change ingested | 562 °C |
| **door events counted** | **0** |
| **transitions in HA's own recorder over the same 12 h** | **6** |

Nothing errored. The thermometer channel was unaffected, because it compares
each reading against the last *remembered* value instead of trusting a
position, which is exactly the property the touch channel lacked.

Replayed against those twelve hours of real transitions, through the deployed
`_count` rather than a paraphrase of it, at the measured 14.6 s lag:

| rule | counted |
|---|--:|
| before (no overlap, skip the first point) | **0 of 6** |
| after (overlap, dedupe by timestamp, remembered state) | **6 of 6** |

Three changes, and all three are needed: an overlap wider than the worst
measured lag; a dedupe keyed on each point's own timestamp, without which the
overlap would re-ring every doorbell and re-sum every degree for a minute; and
a remembered per-entity state, so a baseline point can be informative without
being a knock on its own — which is what keeps the phantom knock fixed. Eleven
new counting cases in `test_ha_senses.py`, including the re-read window
(1, 0, 0 events across three reads) and the first sighting of an already-`on`
sensor (0 events).

**What this cost in honesty:** the comment that closed this issue said *"she can
feel the front door"* and *"live on your actual house right now: 223 polls, 0
errors"*. The polls were real and the thermal half was real. The door half was
proven by injected drive and by a fake Home Assistant, and never by a real
transition — and a real transition would have failed. The counter that would
have shown it was in `/health` the whole time, reading `touch_events: 0`.

## What one door event does, and the wrong explanation we reached for first

The config comment originally claimed a single door sensor firing "reads
`stirring`". Measured, the same 0.02 drive into mechanosensory gave raw arousal
**0.0629** and then **0.0275** — either side of the threshold once rescaled, so
the state came out `stirring` once and `idle` once.

The first explanation was the obvious one: it is a continuously running network,
so the same stimulus lands differently depending on its trajectory, like the
drift that moved the arousal ceiling from 0.19 to 0.3493. **That explanation was
wrong, and measuring instead of believing it changed the answer.** Driving one
site at a constant level and sampling every 6 s for 72 s
([`rise_time.json`](../measurements/raw/rise_time.json)):

| | 6 s | 12 s | 24 s | 30 s | 72 s |
|---|--:|--:|--:|--:|--:|
| mechanosensory 0.06 → arousal | 0.000 | 0.353 | 0.543 | 0.567 | **0.582** |
| hygro_thermo 0.20 → reinforcement | 0.138 | **0.177** | 0.014 | **0.000** | 0.000 |

Both of the earlier readings were taken **mid-rise.** mechanosensory takes about
30 s to reach a plateau, and at that plateau one door event is raw arousal
0.2238 — comfortably `alert`, no coin-flip. So the honest claim is: *any* door
event reaches `alert` within about half a minute and fades over a couple of
minutes, and a flurry at the cap goes further, to `disturbed`.

The second row is the better finding. **hygro_thermo is phasic.** Held at a
constant drive it peaks near 12 s and is back at *exactly* 0.0000 by 30 s with
the stimulus still applied, and stays there for the rest of the sweep — it
answers the onset of a change and then habituates completely, which is what a
real thermoreceptor does and which nobody wrote. mechanosensory is tonic and
holds its plateau. It is also why the thermal integrator's time constant is 60 s
and not the 180 s it started at: a long tau keeps the drive high long after the
nerve has stopped listening, which makes the *next* temperature change a smaller
step and therefore a weaker signal.

The phrase that fires on that axis reads "an unfamiliar pattern, briefly
interesting". That turns out to be the literal truth.

## `agitation` is inverted

1.0 is undisturbed and coiled. 0.0 is maximally driven. A resting value of 0.85
means *calm*. Reading it as "agitated" inverts her entire emotional state, and
it is the easiest mistake to make against this service — so it is flagged in
the README, in `mood.py`, and here.

---

## The entity ids in `server/gladosd.py` are placeholders

`ha_touch_entities` and `ha_thermo_entities` in the published `DEFAULTS` are
**not** the deployment's list. A Home Assistant entity id names the rooms, doors
and devices of a particular house, so at the owner's request the real list was
moved into that install's local `config.json` — which is not in this repository
— and the code default was genericised. `load_config()` does a flat dict update,
so those two keys are replaced wholesale.

The placeholders are checked to resolve to **nothing**: HA returns `404` from
`/api/states` for all seven, with two real entity ids as positive controls
returning `200`
([`entity_genericise.json`](../measurements/raw/entity_genericise.json)). Those
controls matter — the first run of the check returned `000` for every entity
*including* the controls, because of a wrong environment variable name, and
without them it would have read as a clean result.

So a fresh clone polls entities that do not exist and the sense is simply
**deaf**, not wired to something unexpected. `/health` reports how many entities
each site ended up with, so a missing override is visible rather than silent.
This and the host addresses in `gladosd.py`'s docstring are the only two
differences between `server/` and the deployed code.

---

## The knock that has not happened yet

The counting bug above is fixed and the fix is proved — but it is proved by
**replay**, against twelve hours of transitions Home Assistant had already
recorded. What has still never been observed is the thing the sense exists for:
a real person opening a real door, counted by the live service as it happens.

That gap cannot be closed by looking once. Here is the live service, nineteen
minutes after the fix was deployed, cross-checked against Home Assistant's own
recorder over the identical window:

| | |
|---|--:|
| polls | 168 |
| poll errors | 0 |
| **door events counted** | **0** |
| **transitions in HA's recorder** | **0** |

Those two zeros agree, which is the best that can be said for them. **It is also
exactly what the sixteen-hour outage looked like**, right up until somebody
thought to ask the recorder a second question. Agreement at zero is not
evidence; it is the absence of evidence, and the failure mode of this whole
subsystem is that the two are indistinguishable by inspection.

So the proof is left to arrive on its own.
[`tools/xcheck_touch.py`](../tools/xcheck_touch.py) runs every ten minutes under
a systemd timer and compares the cumulative live counter against HA's recorder,
reporting one of five outcomes:

| outcome | meaning |
|---|---|
| `AGREE_FIRED` | HA recorded transitions and the counter matched. **This is the proof. Nothing else in this section is.** |
| `AGREE_QUIET` | Both zero. Consistent, and evidence of nothing. |
| `UNDERCOUNT` | The counter is behind, and the missing presses were clustered inside HA's visibility lag — the accepted limit below. Not a defect. |
| `MISMATCH` | A solitary knock went missing, or the counter is persistently ahead. The original bug, back. |
| `COULD_NOT_MEASURE` | HA or `/health` was unreachable, or the service restarted and the counter re-anchored. |

Three details that are the difference between an instrument and a decoration.
It compares **cumulative** totals since the service started, not per-window
deltas, because HA admits a change up to ~15 s late and a straggler at a window
edge otherwise shows up as a phantom mismatch in one direction and then the
other. It requires a gap to **survive two consecutive checks** before calling
`MISMATCH`, for the same reason. And `COULD_NOT_MEASURE` exists at all because a
checker that cannot report *"I failed to look"* reports *"broken"* instead, and
is then ignored — which is how the original counter got trusted.

All five outcomes are driven in
[`tools/test_xcheck_touch.py`](../tools/test_xcheck_touch.py) against a real
HTTP server: **13 passed, 0 failed**, including `AGREE_FIRED` itself. A verdict
that has never been seen to fire is not evidence of anything, which is precisely
the mistake that let the door sense ship dead.

### The accepted limit, stated plainly

Because Home Assistant can take up to ~15 seconds to admit a change, several
presses inside that gap arrive in one window and read as **one** event. Single
knocks, doorbell presses and motion all count correctly; only a rapid flurry
under-counts. Closing that would mean subscribing to HA's websocket event stream
instead of polling history — a different piece of work. The decision on record
is that one knock is one knock, so the cross-check classifies a clustered
shortfall as `UNDERCOUNT` and does not raise it as a defect.

---

## Withdrawn claims

Kept rather than deleted, because a project that only publishes its wins is not
publishing measurements.

| claim | status |
|---|---|
| "The two T400s do 13.5 tok/s on the 7B, against 0.5 on the CPU." | **Withdrawn.** Does not reproduce. Three reps, variance under 0.03, full 29/29 offload confirmed in the loader log: **7.06 tok/s**, which is within noise of the tuned CPU's 7.39. The 0.5 figure was the *untuned* CPU default, since measured at 1.35–1.54. |
| "Full GPU offload is the fast path for the language model." | **Withdrawn.** A T400 is a 64-bit-bus card and decode is bandwidth-bound. Flash attention changes nothing at these context lengths (7.42 vs 7.06, i.e. a no-op). The text model stays on the CPU, which also leaves both cards free for the fly. |
| "Mechanosensory is the axis that would drive novelty, valence and reinforcement properly." | **Withdrawn for `reinforcement`.** Measured on the live engine before wiring: reinforcement is **exactly 0.0000** at every drive level from 0.02 to 0.40 ([`mechanosensory.json`](../measurements/raw/mechanosensory.json)). Hearing is the better of the two there (0.006–0.018) and still too weak to earn a phrase. Arousal (213 sd) and agitation (monotonic, 0.823 → 0.019) hold up; valence is rated usable at 9.0 sd but is still not monotonic. |
| "She can feel the front door" (as of 8 October 2026) | **Was false for sixteen hours. Fixed, and proved by replay rather than by a door.** The wiring, the drive, the rescaling and the phrase table were all correct; the event counting dropped every real transition, so the nerve was never driven by the house. Proven by injected drive and a fake Home Assistant, which both passed. 0 of 6 real transitions counted, against 6 of 6 after the fix. The thermal half was unaffected throughout. The part that is **still not proved** is a real transition landing in the live counter — see "The knock that has not happened yet" below. |
| "More threads are faster." | **Withdrawn, emphatically.** 10 threads: 8.18 tok/s. 20 threads (Ollama's own default on this box): **1.54 tok/s**. The cgroup straddles two NUMA nodes with only 10 full physical cores, so every extra thread buys cross-socket memory traffic. Raising it is not a free win; it collapses. |

---

## Credit where the actual science is

The connectome is **MaleCNS v1.0**, from Janelia FlyEM and Google Research,
released under **CC-BY 4.0** — thousands of hours of electron microscopy and
neuron tracing by people who were not thinking about voice assistants. That is
the scientific achievement in this repository. We downloaded it and were rude
with it.

<https://male-cns.janelia.org/>
