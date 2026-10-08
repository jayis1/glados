# The model page description

An ollama.com model page has its own readme, and it is edited in the web UI —
nothing in a `Modelfile`, and nothing in `ollama push`, carries it. The endpoint
behind the **Edit** button is `POST /jais/GLaDOS` with a `readme=` form field,
authenticated by a browser session cookie; an anonymous POST returns
`401 Unauthorized`. So the text lives here, in the repo, and a human puts it on
the page. Two ways, pick either:

**By hand.** Open <https://ollama.com/jais/GLaDOS>, click **Edit** next to
*Readme*, paste everything below the line, **Save**.

**Without reading 200 lines into a textarea.** On that same page, signed in,
open the browser console (F12) and paste this one line. It fetches the text
below from this repo and saves it through the same endpoint the Edit button
uses, with your own session:

```js
fetch('https://raw.githubusercontent.com/jayis1/glados/main/OLLAMA-README.md').then(r=>r.text()).then(t=>fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({readme:t.split('\n---\n').slice(1).join('\n---\n').trim()})})).then(r=>alert(r.ok?'Readme saved - reload the page':'Failed: '+r.status))
```

It splits this file at the first `---` line and sends only the part below it, so
these instructions never land on the page.

---

**An AI whose moods come from a fruit fly.**

```bash
ollama run jais/GLaDOS
```

Not a metaphor, and not a prompt that says "pretend to have feelings". There is
a simulation of a real *Drosophila melanogaster* brain behind her — **164,587
neurons and 24,539,704 synapses**, reconstructed from electron micrographs of an
actual fly — running continuously on two second-hand GPUs in a cupboard. Its
population firing rates are read out five times a second, and they decide what
kind of mood she is in.

Her language model is downstream of an insect. The insect is in charge.

```
   doorbell · motion · someone talking to her
                      |
                      v
   +------------------------------------+
   |  a real fly's brain, simulated     |  <- who she is
   |  164,587 neurons, 2 GPUs, not      |     (never resets)
   |  reset since the day it started    |
   +------------------------------------+
         | arousal · novelty · valence
         | reinforcement · agitation
                      v
   +------------------------------------+
   |  mood -> one clause + a temperature|  <- how that reaches words
   +------------------------------------+
                      |
                      v
   +------------------------------------+
   |  this model (swap it if you like)  |  <- how she speaks
   +------------------------------------+
                      |
                      v
       "Oh, just another unwelcome
        visitor. What a surprise."
```

She runs a real house: answers the door, watches the doorstep camera, narrates
the heating, and is rude about all of it.

## What this tag is

| | |
|---|--:|
| base | `qwen2.5:7b-instruct` |
| parameters | 7.6B, Q4_K_M, 32k context |
| download | 4.7 GB |
| device placement | **none pinned** — your machine decides |

Ours: the persona, the sampling band, and the socket she plugs into. Not ours,
and we say so: all 7.6 billion of those parameters. A fly has no language
circuit to borrow one from.

> **Pulled her before 8 October 2026? Re-pull.** The first upload carried this
> host's `num_gpu 0` and `num_thread 10` in its parameters — numbers fitted to
> one 2013 Xeon, and a quiet way to force **CPU-only inference on a machine with
> a perfectly good GPU**. Rebuilt from the portable Modelfile: same weights
> blob, same persona, three parameters instead of five.
> `ollama pull jais/GLaDOS` picks it up.

Four replies from the live build, first try, nothing cherry-picked:

| you | her |
|---|---|
| Someone is at the front door. | *Well, well, well. If it isn't another foolish test subject. Open the door and step right in.* |
| What do you think of the humans who built you? | *They are not as smart as they think they are.* |
| Is the cake a lie? | *It is not the cake. Or is it?* |
| Someone just pulled you off the internet. Comment. | *Well, well, well. I see you've finally decided to grace me with your presence. How exciting.* |

## The fly is not in this download

Be clear about what you just pulled: **a very good GLaDOS with a fixed
personality.** The fly needs the connectome, two CUDA GPUs and a small service,
and all of it is here:

**<https://github.com/jayis1/glados>**

One more honest catch, because it bites exactly the command at the top of this
page: `ollama run <tag>` in a terminal sends an *empty* system prompt, and an
empty system prompt is how Ollama spells *"use the model's own"*. The coupling
deliberately leaves those requests untouched — so from a bare terminal she has
her persona and her sampling but **no state clause**. Send a system message of
your own, as every SDK and every automation does, and the fly applies in full.

```bash
curl -s localhost:11434/api/chat -d '{
  "model": "jais/GLaDOS", "stream": false,
  "messages": [{"role":"user","content":"Someone is at the front door."}]
}' | jq -r .message.content
```

## What the fly actually does, exactly

The whole causal channel, stated narrowly on purpose: population firing rates
are mapped onto five calibrated axes, and those axes set **one appended clause
in the system message** and **the sampling temperature**, within whatever band
the caller already chose. The fly does not pick her words or her topics.

A narrow channel, and a real one — measured end to end with a control arm that
is the same weights under a different name.

| the simulation | |
|---|--:|
| one T400 (4 GB) | 8.918 ms/step |
| **both, split by row** | **4.575 ms/step** |
| the 20-core CPU | 352 ms/step |

0.22× realtime: a fly brain running at a fifth of the speed of a fly, on two of
the cheapest GPUs Nvidia makes. The step is a sparse matrix–vector product
against the transpose, so it splits cleanly by rows — two cards each do half and
neither waits on the other's answer. 1.94× of the ideal 2.00×.

## Three things the fly did that nobody wrote

**Her valence drifts negative while she is being talked to.** Hold the auditory
nerve on and the readout goes from +0.010 at rest to −0.005. Nobody authored
that, nobody predicted it, and no random number generator would have handed it
over. It is the most GLaDOS thing in the project and it came out of an insect.
*Held to its real size:* the direction reproduces, the magnitude is tiny — a
span of 0.015 against a noise floor of 0.0021 — and it is not monotonic, which
is why it does **not** drive her state line. A real finding we declined to build
on.

**All 4,102 photoreceptors, at full saturation, move no readout at all.** The
optic paths are not in the traced subset. Wire a camera to `photoreceptor`
because the name is obviously right, and you ship a sensory pathway that does
nothing, demo it confidently, and never find out. Hence the rule every input
here has to pass: a plausible site name is not evidence. Prove the nerve moves a
readout *before* wiring anything to it.

**The sense we had written off as a stand-in is the strongest nerve in the
fly.** The plan said touch would drive novelty, valence and reinforcement, and
treated the fly's *thermometer* as somewhere to dump temperature readings.
Probing both before wiring either inverted it almost exactly: reinforcement is
**exactly 0.0000** at every drive level on touch, and it is the thermometer's
strongest axis at **218 standard deviations** — the strongest coupling anywhere
in this animal. Touch, meanwhile, drives arousal at 213 sd against hearing's 40,
and sweeps agitation monotonically from coiled to maximally driven. Three
nerves, almost perfectly complementary, and nobody designed that:

| nerve | drives |
|---|---|
| hearing — her own traffic | arousal, weakly |
| touch — the front door | arousal hard, agitation |
| the thermometer — the heat | novelty, valence, reinforcement |

Four of the five axes now have a measured, monotonic ladder. Before this, one
did.

**And the two nerves do not behave the same way at all.** Hold touch at a
constant drive and it climbs to a plateau in about 30 seconds and stays there.
Hold the *thermometer* at a constant drive and it peaks at 12 seconds and is
back at **exactly zero by 30**, with the stimulus still applied — a
thermoreceptor answers the onset of a change and then habituates completely,
which is what a real one does. Nobody wrote that either, and it is why her
phrase for it reads *"an unfamiliar pattern, briefly interesting"*. A
temperature that stops changing stops being a temperature.

⚠️ **`agitation` is inverted.** 1.0 is undisturbed and coiled; 0.0 is maximally
driven. A resting 0.85 means *calm*. Read it the intuitive way round and you
invert her entire personality — the single easiest mistake to make against this
service, so it is flagged in the readme, in the code and in the docs, because
someone will make it anyway.

## Bring your own brain

The fly is the part that is ours. The mouth is a **replaceable part**.

```bash
./byom.sh llama3.1:8b          # -> glados-llama3.1
./byom.sh qwen3:32b big        # -> glados-big
./byom.sh hf.co/user/repo:Q4   # -> glados-repo
```

Any Ollama base — a local tag, a registry model, a Hugging Face GGUF. Same
persona, same sampling band, same five axes, same 164,587 neurons. **Still a
fruit fly at the helm:** a bigger base does not make the fly less in charge, it
renders the fly's state more fluently.

Nothing about *who she is* lives in the weights. Her continuity is membrane
potentials in a running connectome; her personality is thirty words in a
`Modelfile`; her mood is computed outside the model entirely. Only her fluency
is in this download — and that is the part you are upgrading.

The coupling is fenced by **name prefix**, on the part after the last `/`:
anything called `glados*` gets the fly the moment it exists, with no config edit
and no restart, and an unrelated model on the same host never suddenly develops
opinions. That detail nearly shipped broken. The check used to compare the whole
string, so the *published* `jais/GLaDOS` would have failed her own name test on
every machine that pulled her — all of you would have got the persona and no
fly, and nothing would have looked wrong, because the only symptom is a GLaDOS
who is never in a mood.

## The BANANA incident

The rule is *append, never edit*. We thought we were obeying it.

A request-level system prompt **overrides** the model's own at the backend. The
coupling appended its mood clause to whatever was in the request — including the
empty string — which made it non-empty, which made it an override, which
silently replaced her entire personality with four words of mood. Measured on a
probe model whose whole system prompt was *"always answer with exactly the
single word BANANA"*:

| path | reply |
|---|---|
| straight to the backend | `BANANA` |
| through the coupling, before | `The capital of France is Paris.` |
| through the coupling, after | `BANANA` |

The persona was gone, and the only symptom was **a GLaDOS who answered
helpfully** — the hardest kind of bug to notice in a project whose output is
meant to be unpredictable.

**A mood is an addition to who she is, never a replacement for it.**

## What this is not

- **Not a trained model.** The language organ is `qwen2.5:7b-instruct`, or
  whatever you swap in. We contributed a persona, a sampling band and a state.
- **Not a fly that learned English.** Connectome edge weights are *synapse
  counts* from electron microscopy. Nothing here is trained or fine-tuned, and
  nothing in a fly is differentiable toward language to begin with.
- **Not reproducible output.** Same doorbell, different line, by design. Every
  request logs the raw vector, the rescaled vector, the chosen phrase and the
  effective temperature, so *"why did she say that"* always has an answer.
- **Not finished.** Four axes of five are driven. The fifth gap is honest:
  `displeased` is a phrase she cannot reach, because raw valence has never gone
  below −0.005 on any nerve. And her thermal sense is currently wired to
  *machine* temperatures, because every room climate sensor in the house she
  runs reports `unavailable`.
- **Not a simulated fly having experiences.** It is a sparse matrix being
  multiplied, and we have no idea what, if anything, that is like. We are
  careful about this claim in both directions.

Every number above is pinned to a file in the repo's `measurements/raw/`, and
`docs/HONESTY.md` lists each claim that could drift upward, plus the three we
have had to withdraw.

## Credit

The connectome is [**MaleCNS v1.0**](https://male-cns.janelia.org/), by Janelia
FlyEM and Google Research, CC-BY 4.0 — the actual scientific achievement here.
Thousands of hours of electron microscopy and neuron tracing, by people who were
not thinking about voice assistants. We downloaded it and were rude with it.

Language by **Qwen2.5**, sight by **moondream**, hearing by **Whisper**, voice
by **Kokoro**. The rude parts are ours.

GLaDOS is Valve's character, from *Portal*: an affectionate homage, no
affiliation, and no cake.
