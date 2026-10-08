# GLaDOS

**An AI model whose moods come from a fruit fly.**

Not a metaphor. Not a prompt that says "pretend to have feelings." There is a
simulation of an actual *Drosophila melanogaster* brain — 164,587 neurons and
24,539,704 synapses, reconstructed from electron micrographs of a real fly —
running on two GPUs in a cupboard. Its firing rates are read out five times a
second and they decide what kind of mood GLaDOS is in. Her language model is
downstream of an insect.

```
    doorbell, motion, someone talking to her
                      │
                      ▼
      ┌───────────────────────────────────┐
      │  a fly's brain, simulated         │   ← who she is
      │  164,587 LIF neurons, 2 GPUs      │     (never resets)
      └───────────────────────────────────┘
                      │  arousal · novelty · valence
                      │  reinforcement · agitation
                      ▼
      ┌───────────────────────────────────┐
      │  mood → one sentence + sampling   │   ← how that reaches her words
      └───────────────────────────────────┘
                      │
                      ▼
      ┌───────────────────────────────────┐
      │  a 7B language model              │   ← how she speaks
      └───────────────────────────────────┘
                      │
                      ▼
          "Oh, just another unwelcome
           visitor. What a surprise."
```

She runs a real house. She answers the door, watches the doorstep camera,
narrates the heating, and is rude about all of it.

---

## Run her in two commands

You need [Ollama](https://ollama.com) and about 5 GB of disk.

```bash
git clone https://github.com/jayis1/glados && cd glados
ollama create glados -f Modelfile && ollama run glados
```

Or without cloning anything:

```bash
curl -fsSL https://raw.githubusercontent.com/jayis1/glados/main/Modelfile -o Modelfile.glados
ollama create glados -f Modelfile.glados
ollama run glados
```

> `ollama create -f -` does not read stdin — it wants a real path. Verified the
> hard way, so you don't have to.

<details>
<summary>Or publish her to the Ollama registry, so there is nothing to clone at all</summary>

`./publish-to-ollama.sh <your-ollama.com-username>` tags and pushes her, after
which she is a plain `ollama run <namespace>/glados` for anyone.

That needs one thing only the account owner can do: the registry authenticates
pushes with your machine's Ollama public key, which has to be registered at
<https://ollama.com/settings/keys> by hand. Run the script with **no arguments**
and it prints the exact key to paste.

</details>

Then she is simply a model. `ollama list` shows her, `ollama run glados` talks
to her, and `"model": "glados"` works anywhere a model name goes:

```bash
curl -s localhost:11434/api/chat -d '{
  "model": "glados", "stream": false,
  "messages": [{"role":"user","content":"Someone is at the front door."}]
}' | jq -r .message.content
```

Four real replies from the live build, first try, nothing cherry-picked:

| you | her |
|---|---|
| Someone is at the front door. | *Well, well, well. If it isn't another foolish test subject. Open the door and step right in.* |
| What do you think of the humans who built you? | *They are not as smart as they think they are.* |
| Is the cake a lie? | *It is not the cake. Or is it?* |
| A test subject has arrived. | *Begin testing protocol immediately.* |

That is the two-command version: a very good GLaDOS impression with a fixed
personality. Everything below is what happens when you bolt a fly to it.

---

## Three layers, and an honest ledger

Here is exactly what is ours and what is borrowed. The interesting half is the
half nobody can download.

| part of her | what it is | ours? |
|---|---|---|
| **who she is** | a 164,587-neuron simulation of a real fly connectome, continuously running, membrane potentials persisting between requests, never reset | **yes** |
| **what she senses** | her own traffic → the fly's auditory nerve; doorbell and motion → its mechanosensory nerve | **yes** |
| **how mood reaches words** | state → one appended clause + a temperature nudge | **yes** |
| **that she is GLaDOS at all** | `Modelfile` — the persona, the sampling band | **yes** |
| **how she speaks** | `qwen2.5:7b-instruct`, ~7.6 × 10⁹ parameters someone else fitted on ~10¹³ tokens | **no** |
| **how she sees** | `moondream` | no |
| **how she hears** | `whisper large-v3` | no |
| **her voice** | `kokoro` | no |

**We did not train the language layer and could not.** A fly has no language
circuit to borrow one from; connectome edge weights are *synapse counts*, not
learned parameters; and the simulation runs at 0.22× wall-clock, which is a
fine speed for having moods and a hopeless one for gradient descent.

So the division of labour is deliberate, and it is the whole idea:

> **The fly holds the state. The language model is demoted to a stateless
> renderer of that state.**

Before this, the LLM *was* GLaDOS and the fly was a decoration. Now it is the
other way round.

---

## The fly

The connectome is the male *Drosophila* CNS reconstruction from Janelia's
FlyEM project — a real animal, sliced, imaged, and traced. We take the
traced-only edges above 0.5 confidence, resolve each synapse's sign from the
neurotransmitter table (a cholinergic edge excites, GABA and glutamate
inhibit), and get a signed sparse matrix of 24,539,704 connections between
164,587 neurons.

Then we run it as leaky integrate-and-fire neurons: every step, current flows
along the synapses, membrane potentials rise, neurons that cross threshold
spike and reset, and the whole thing is sparse-matrix-multiplied forward in
time. Forever. It has not been reset since it started.

**Signs are not optional.** The published weights are unsigned — just "how many
synapses." Run that matrix without the neurotransmitter table and every
connection is excitatory, which is not a brain, it is a feedback squeal.

### It needs two GPUs, and the reason is boring and nice

The step is a sparse matrix–vector product against the *transpose*, computing
input current per postsynaptic neuron. So you can split it by **rows** — and
rows partition the output cleanly, which means two cards each do half and
neither needs the other's answer mid-step.

| configuration | ms per step | × realtime |
|---|--:|--:|
| one T400 (4 GB) | 8.918 | 0.11× |
| the other T400 | 8.889 | 0.11× |
| **both, split by row** | **4.575** | **0.22×** |
| the 20-core CPU | 352 | 0.003× |

1.94× of the ideal 2.00× speedup; 0.131 ms/step of overhead. A fly brain,
simulated at a fifth of the speed of a fly, on two of the cheapest GPUs Nvidia
makes.

### Five axes, calibrated against what the network can actually do

We read out population firing rates and map them onto five axes: **arousal,
novelty, valence, reinforcement, agitation**. The lo/hi for each is *measured*
by driving the network to saturation and recording where it tops out — not
chosen to look tidy.

⚠️ **`agitation` is inverted.** 1.0 is undisturbed and coiled; 0.0 is maximally
driven. Reading a resting 0.85 as "agitated" inverts her completely, and it is
the single easiest mistake to make against this service. It is called out here,
in the code, and in the docs, because someone will make it anyway.

---

## The part where the fly surprised us

Before wiring any sense to any nerve, we measured whether that nerve moves
anything. This turned out to matter enormously.

**All 4,102 photoreceptors, driven to full saturation, move no readout at all.**
Zero. The optic paths simply are not in the traced subset. A plausible-sounding
sensory site name is *not* evidence that the site does anything — so every
input in this repo had to earn its wiring with a measurement first.

Here is the auditory nerve (`hearing_JO` — Johnston's organ, what a fly hears
with) earning it. We feed her own request traffic into it, five snapshots per
level, on the live calibrated service:

| drive | 0 | 0.02 | 0.05 | 0.1 | 0.2 | 0.4 |
|---|--:|--:|--:|--:|--:|--:|
| **arousal** | 0.000 | 0.004 | 0.018 | 0.057 | 0.106 | 0.188 |
| valence | +0.010 | +0.006 | +0.011 | +0.012 | **−0.005** | +0.003 |

Arousal: clean, monotonic, 40 standard deviations above noise at the top. The
sense is real.

But look at the second row. **Under sustained traffic her valence drifts
negative.** She is measurably annoyed by being talked to.

Nobody wrote that. It is not in a prompt, it is not a rule, and it was not on
anyone's plan. It is what a real fly's connectome does when you hold its
auditory nerve on, and it happens to be the most GLaDOS thing in the entire
project.

### The rescaling that stopped it all being theatre

Raw arousal cannot exceed about 0.19 through hearing. The state thresholds
read like fractions of a 0–1 axis — so every threshold at 0.20 or above was
**unreachable**, and she would have read `idle` forever while we congratulated
ourselves on a working brain.

So the axes are rescaled against the measured ceiling, and only the axes with a
clean monotonic ladder are rescaled at all. An axis with no measurement is
passed through untouched rather than guessed at. This is the difference between
a coupling and a decoration, and it was one line of code and an afternoon of
measuring.

---

## Proof that the fly actually changes what she says

Claiming "her mood affects her output" is easy. Here is the measurement, with a
control arm — the same prompt, same conditions, sent to an *uncoupled* model
tag, so the effect cannot be a coincidence of load:

```
rest   state=idle    raw arousal 0.0000   temp 0.700
       → "Do I look like a doorman to you?"

busy   state=alert   raw arousal 0.1609   temp 0.869
       → "Oh, just another unwelcome visitor. What a surprise."

ctrl   uncoupled tag → mood_skipped=model_not_coupled   (fly ignored, as designed)
```

And then she calms down on her own, because the fly's membrane potentials decay
like a real one's:

```
alert  (t+60s)  →  stirring  (t+120s)  →  idle  (t+180s)
```

Nine assertions, all passing. Raw JSON in
[`measurements/raw/fly_centred_proof.json`](measurements/raw/fly_centred_proof.json).

---

## One name, five engines

`"model": "glados"` **sees, hears, thinks and speaks.** Nothing else has to be
named. The request extensions follow Ollama's existing `images` convention:

| field | effect |
|---|---|
| `images: [b64]` | captioned by moondream, folded into the prompt as text |
| `audio: [b64]` | transcribed by whisper, becomes the utterance |
| `speak: true` | the reply comes back as base64 WAV in `audio` |
| `voice`, `speed` | Kokoro voice and 0.5–2.0 rate |
| `language` | transcription hint |

```bash
# she is shown the doorstep, spoken to, and answers out loud — one request
curl -s localhost:11434/api/chat -d '{
  "model":"glados","stream":false,"speak":true,
  "messages":[{"role":"user","content":"",
               "images":["<b64 jpg>"],"audio":["<b64 wav>"]}]}' \
  | jq -r '.message.content, .audio_ms'
```

**What "one model" means and does not mean:** one name, one API and one
personality over five engines — *not* one set of weights. A decoder-only
transformer, a ViT captioner, an encoder-decoder ASR model and an ONNX vocoder
have different architectures, tokenisers and output spaces. You cannot
concatenate them into a file, and anything that genuinely is single-weight
multimodal was *trained* that way from the start. Said plainly here so the
claim never drifts upward.

Her ears are tested against her own voice, which is a nicer test than a fixture
someone trimmed until it passed: Kokoro renders *"The cake is a lie and the
doorbell is broken."*, whisper transcribes it back, word for word — 1366 ms of
transcription on 2709 ms of audio.

---

## Four rules the coupling obeys

She was mute for days once, because a single host went down. These rules are
scars.

1. **Fail open, always.** Brain unreachable, slow, unauthorised, stale, or
   raising something nobody foresaw — all of them mean "send exactly the bytes
   the caller sent." Every branch is exercised against a **real socket**, not a
   mock: a closed port, an unreachable host, a listener that accepts and then
   hangs forever, a missing token, a stale snapshot. Worst case measured: 301
   ms, then uncoupled. A fly may never cost her her voice.
2. **Never on the request path.** Noting a request takes a lock and increments
   an integer — 0.37 µs. All actual I/O is on a background thread.
3. **Append, never edit.** One `Current state: <phrase>.` clause on the end of
   the system message. Existing prompts stay a strict byte prefix of what they
   were.
4. **Inside the caller's band.** Arousal may only *raise* temperature, by at
   most 0.3, never past 0.9. A caller that chose 0.2 chose determinism and
   keeps its floor.

One deliberate exception to fail-open: a request whose *only* content is audio
or an image, where that sense failed, returns **502** naming the dead sense.
Answering anyway would mean inventing an utterance nobody made. Fail open means
never losing words she has — it must not mean fabricating words she never
heard.

---

## What's in here

```
Modelfile              the portable persona build — this is the "model"
Modelfile.cpu-tuned    the deployed variant, with the 5× placement cliff measured
install.sh             the two commands, with the checks

fly/                   layer 1 — the brain
  engine.py              sharded LIF simulation, named sensory populations
  build_csr.py           connectome + neurotransmitters → signed sparse matrix
  calibrate.py           measures each axis's real lo/hi
  reach.py               anatomical reach: which nerve can move which readout
  moodd.py               serves the mood vector over HTTP
  probe_hearing.py       proves a sensory site moves a readout BEFORE you wire it

server/                layer 2 — mood → words
  mood.py                state → clause + temperature, and all the fail-open paths
  afferent.py            her own traffic → the auditory nerve
  senses.py              vision, hearing and voice behind one model name
  gladosd.py             the Ollama-compatible front end
  test_mood.py           fail-open tests against real sockets
  proof_fly_centred.py   the end-to-end proof, with its control arm

measurements/raw/      every number quoted anywhere, as the tool emitted it
docs/                  the long versions
```

- **[docs/FLY.md](docs/FLY.md)** — the brain: sharding, calibration, the
  operating point, and why 4.575 ms/step is the number that matters.
- **[docs/SERVER.md](docs/SERVER.md)** — the front end: device placement,
  the 5× thread cliff, keep-alive clamping, the senses.
- **[docs/HONESTY.md](docs/HONESTY.md)** — every claim in this README that
  could drift upward, pinned down, plus the ones we withdrew.
- **[docs/REPRODUCE.md](docs/REPRODUCE.md)** — build the fly yourself.

---

## Reproducing the fly

The two-command install needs nothing but Ollama. The **fly** needs the
connectome, two CUDA GPUs with ~4 GB each, and roughly 850 MB of download:

```bash
cd fly
./fetch_connectome.sh     # Janelia FlyEM, anonymous, no account needed
source env.sh             # CUDA header shim + loader path
python3 build_csr.py      # → signed CSR, needs the neurotransmitter table
python3 build_transpose.py
python3 reach.py          # which nerves can move which readouts (do this first)
python3 calibrate.py      # measure each axis's real lo/hi
python3 moodd.py          # serve the mood vector
```

See [docs/REPRODUCE.md](docs/REPRODUCE.md) for the parts that bite: the CUDA
header shim, why CuPy needs headers an Ollama install does not, and the fact
that a GPU the simulation shares with a voice stack will quietly lose to a
tuned CPU.

---

## What this is not

Stated here rather than buried, because this project is unusually easy to
oversell and the measured version is more interesting than the hype:

- **It is not a trained model.** The language organ is `qwen2.5:7b-instruct`.
  We contributed a persona, a sampling band, device placement, and a state.
- **It is not a fly that learned English.** Connectome weights are synapse
  counts. Nothing here is trained, fine-tuned, or back-propagated.
- **It is not reproducible output.** Same doorbell, different line, by design.
  The event log records the raw vector, the rescaled vector, the chosen phrase
  and the effective temperature for every single request, so *"why did she say
  that"* always has an answer.
- **It is not finished.** Only `arousal` is usefully driven today. `novelty`,
  `valence` and `reinforcement` are waiting on the mechanosensory nerve — which
  measures a 40× stronger path into the escape circuit than hearing does
  (9.4 × 10⁻² against 2.4 × 10⁻³) and is blocked on a home-automation
  credential, not on physics.
- **It is not a simulated fly having experiences.** It is a sparse matrix being
  multiplied, and we have no idea what, if anything, that is like. We are
  careful about this claim in both directions.

---

## Credit

The connectome is [**MaleCNS v1.0**](https://male-cns.janelia.org/), by Janelia
FlyEM and Google Research, CC-BY 4.0 — the actual scientific achievement in
this repository. Thousands of hours of electron microscopy and neuron tracing
by people who were not thinking about voice assistants. Language by
**Qwen2.5**, sight by **moondream**, hearing by **Whisper**, voice by
**Kokoro**. The rude parts are ours.

GLaDOS is Valve's character, from *Portal*. This is an affectionate homage with
no affiliation, and no cake.

---

<sub>Code in this repository: MIT. The connectome data is Janelia's, under its
own terms, and is downloaded rather than redistributed here.</sub>
