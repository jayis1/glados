> **These are the original engineering notes** for layer 2 — the
> Ollama-compatible front end that carries the mood coupling and the senses.
> Published as written. `IST-nnn` are internal ticket numbers; host names and
> addresses have been genericised, and references to the n8n home-automation
> workflows describe the deployment this was built for. Raw logs for every
> number are in [`../measurements/raw/`](../measurements/raw/).
>
> **`server/` is published as deployed, with two named exceptions**, which
> means its paths are absolute to that host's `/opt` prefix
> (`/opt/paperclip-gladosd`, `/opt/paperclip-glados/token`, and so on). That is
> deliberate: this is the reference implementation that the measurements in this
> repository were taken against, not a package to install. The exceptions are
> the host addresses in `gladosd.py`'s docstring, and
> `ha_touch_entities` / `ha_thermo_entities`, whose defaults here are
> **placeholders that resolve to nothing** (404 from HA's `/api/states` for
> every one of them, checked) — a Home Assistant entity id names the rooms,
> doors and devices of a specific house, so the deployment's real list was
> moved into its local `config.json` and the code default was genericised. That
> is the *only* functional difference, it is a `DEFAULTS` key like any other,
> and `/health` reports how many entities each site ended up with so a missing
> override is visible rather than silent. Every path is likewise a key in
> `DEFAULTS` with a `config.json` override — see
> [`../server/config.example.json`](../server/config.example.json). The
> `fly/` layer, by contrast, *has* been made repo-relative, because
> reproducing the brain is something you are meant to be able to do.

# paperclip-gladosd — GLaDOS's language brain on the GPU host

Paperclip **IST-270**. Drop-in replacement for the dead Ollama host
`the old host`, which took GLaDOS mute along with it: 18 n8n workflows
(22 LLM nodes) call that host and every execution reaching an LLM node failed
with *"The host is unreachable, perhaps the server is offline"*.

```
 n8n (the automation host)                 the GPU host
   22 httpRequest/code nodes  --->   paperclip-gladosd   --->  ollama
   :11434 / :11435 / :11436          (same three ports)        127.0.0.1:11501
                                                               CUDA_VISIBLE_DEVICES=0
```

`ollama` itself is **not** the public service. It is moved to loopback
`:11501` and gladosd owns the three LAN ports, so re-pointing n8n is a pure
host substitution — `the old host` → `the GPU host`, nothing else changes.

> Re-pointing the live workflows is custody-gated and tracked separately.
> This service only has to answer the request shape they already send.

## Why a front end instead of a bare `ollama serve`

1. **Three ports.** The nodes are spread across `:11435` (18 nodes,
   `/api/chat`), `:11436` (moondream, `/api/generate` + `/api/chat`) and
   `:11434` (1 node, `/api/chat`). A bare ollama binds one.
2. **Device placement.** The Ollama API has no per-request device selector,
   only a layer count, so placement has to be rewritten server-side per model.
   See below — this is where all the performance is.
3. **`keep_alive` clamping.** The live *Front door → Discord (GLaDOS)* code
   node sends `keep_alive: -1`, which would pin moondream's VRAM forever and
   starve the voice stack (`paperclip-sttd` + `paperclip-ttsd`) that owns
   GPU 0. Vision requests are clamped to `vision_keep_alive_max_s` (300).
4. `/health` plus a structured JSONL event log, like the other services here.

## Placement, and the one setting that matters

Host is a 2013 Xeon E5-2690 v2 — AVX1, no FMA/AVX2 — with **two NUMA nodes**.
Our cgroup holds 20 CPUs, but they are split 11 on node0 / 9 on node1 and only
**10 are full physical cores** (the rest are hyperthread siblings whose primary
we do not own).

Letting ollama pick its own thread count is therefore pathological: all 20
CPUs, spanning both sockets, so every decoded token pays cross-socket memory
traffic. Measured on `qwen2.5:7b-instruct` Q4_K_M, decode tok/s
(`eval_count / eval_duration`, ollama's own accounting):

| threads | tok/s |
|--:|--:|
| 4 | 3.88 |
| 6 | 5.93 |
| 8 | 7.71 |
| **10** | **8.18** |
| 12 | 7.80 |
| 20 (ollama's default) | **1.54** |
| default, unset | 1.35 |

`text_num_thread: 10` is the knee and the deployed value. **Raising it is not a
free win — it collapses.**

### The GPUs do not help, and that is measured

Tempting, since the box has three cards. It does not pay off for a 7B:

| placement | tok/s (120-token decode) | |
|---|--:|---|
| CPU, 10 threads | **7.39** | deployed |
| both T400s, 29/29 layers offloaded | 7.06 | verified full offload from the loader log |
| both T400s + flash attention | 7.42 | FA is a no-op here |

A T400 is a 64-bit-bus card and decode is **bandwidth-bound**, not
compute-bound — which is also why flash attention changes nothing at these
context lengths. Full GPU offload is within noise of the tuned CPU.

So text stays on the CPU, and that is the better answer for a second reason:
it leaves **both T400s entirely free** for IST-269's fly-connectome
simulation, which really does run them at ~98%. Co-locating the 7B there
would have cost both workloads and gained neither.

> An earlier revision of this file's docstring claimed the T400 pair measured
> 13.5 tok/s and the CPU 0.5 tok/s. The 0.5 was the *untuned* default (now
> 1.35–1.54 measured). The 13.5 does not reproduce: 7.06 tok/s across three
> reps with variance under 0.03, full 29/29 offload confirmed in the loader
> log. Treat 13.5 as withdrawn.

GPU 0 (GTX 1660 SUPER, 336 GB/s) is the one card that *would* be substantially
faster, but its spare ~3.4 GiB cannot hold a 4.7 GiB model, and taking VRAM
from a live human-facing voice service to find out is a decision for the board,
not a default. Only moondream runs there (1.24 GiB, keep_alive capped).

## Verification

`replay.mjs` is the real proof, and it does not retype any request body. Each
`httpRequest` node's `jsonBody` is an n8n expression
(`={{ JSON.stringify({...}) }}`); the harness evaluates that exact expression
with a mocked `$json`, so what goes on the wire is byte-identical to n8n apart
from fixture values. The two requests the *Front door* **code** node makes are
copied verbatim from its `jsCode`, including its `keep_alive: -1`.

```sh
node /opt/paperclip-gladosd/replay.mjs                      # all 22 nodes
node /opt/paperclip-gladosd/replay.mjs --only doorbell      # filter
node /opt/paperclip-gladosd/replay.mjs --json /tmp/out.json
```

Last full run: **22 replayed · 0 failed · 0 over the node's own n8n timeout.**
Text 7.6–8.6 tok/s, vision 153–157 tok/s, worst case 35.6 s against that
node's own 180 s timeout.

That margin is the point. At the *old* default rate, replaying the same bodies,
**3 of 20** text nodes would have exceeded their own configured n8n timeout on
the favourable 1.35 tok/s figure — and **13 of 20** at the 0.44 tok/s the live
service was actually producing. The service was "up" and still broken for most
of the workflows; the thread setting is what closed that.

## Operating

```sh
systemctl status paperclip-gladosd
curl -s localhost:11434/health | jq
journalctl -u paperclip-gladosd -f          # one JSON object per request
tail -f /var/log/paperclip-gladosd/events.jsonl
```

`/health` reports `degraded` + HTTP 503 if the ollama backend is unreachable,
and echoes live placement so the running config is never a guess:

```
"placement": { "text": "cpu (options.num_gpu=0, num_thread=10, keep_alive=-1)", ... }
```

Useful log fields per inference: `route` (`text`|`vision`), `tok_per_s`,
`eval_count`, `keep_alive_clamped_to`, `latency_ms`.

### Files

| path | |
|---|---|
| `gladosd.py` | the service |
| `config.json` | deployed overrides; every key also has a safe default in `DEFAULTS` |
| `replay.mjs` | real-n8n-body replay harness |
| `bench_placement.py` | per-placement decode benchmark, samples neighbour GPU load |
| `fixtures/` | doorstep image for the vision nodes |
| `/opt/paperclip-glados/bench_text.py` | thread/placement sweep used for the tables above |
| `/opt/paperclip-glados/results/` | raw JSON for every measurement quoted here |

### Gotchas

- **`text_num_thread` is the whole performance story.** Anything that resets it
  to ollama's default costs ~5x.
- Weights are held resident (`text_keep_alive: -1`) because a cold load of the
  7B measured ~63 s on this disk, which alone blows most node timeouts.
  `qwen2.5:7b-instruct` shows `size_vram: 0` in `/api/ps` — that is correct,
  it confirms the CPU placement.
- The ollama drop-in pins `CUDA_VISIBLE_DEVICES=0`. That fence is what keeps
  ollama's scheduler from discovering the T400s and splitting a model onto
  IST-269's cards.
- n8n reaches this host as `a second interface` as well as `the GPU host`; gladosd
  binds `0.0.0.0`, so either works.

---

## `glados` — our own model, with the fly's brain at the centre

There is now a model literally called `glados` on this host. It is in
`/api/tags`, `ollama run glados` talks to her, and `"model": "glados"` works
anywhere `qwen2.5:7b-instruct` did. It is built by `Modelfile.glados` and
costs no extra disk (`FROM qwen2.5:7b-instruct` reuses the same blobs).

What is ours about it, and what is borrowed, stated plainly:

| | | |
|---|---|---|
| **who she is** | the fly | `paperclip-moodd` :9099 — 164,587-neuron LIF connectome on two T400s, five calibrated axes, **continuous**: membrane potentials persist between requests and never reset (IST-269) |
| **what she senses** | `afferent.py` | her own request traffic → `hearing_JO`, the Johnston's organ |
| **how that reaches her words** | `mood.py` | state → one appended clause + temperature |
| **how she speaks** | borrowed | `qwen2.5:7b-instruct`, ~7.6e9 parameters someone else fitted on ~1e13 tokens |
| **that she is GLaDOS at all** | `Modelfile.glados` | the Portal persona, byte-for-byte the most common of the 22 live n8n prompts |

We did **not** train the language layer and cannot: a fly has no language
circuit, connectome weights are synapse counts rather than learned parameters
(they ship unsigned), and the sim runs at 0.21x wall clock. So the fly holds
the state and qwen is demoted to a stateless renderer of it. Before this, the
LLM *was* GLaDOS and the fly was a decoration.

### The split that lets both rulings hold

`mood_models: ["glados"]`. The fly drives `glados`; `qwen2.5:7b-instruct` —
the tag **all 22 live n8n nodes name** — is deliberately not coupled, so the
IST-271 re-point stays the byte-identical host swap IST-266 accepted. Moving
one node onto the fly later is a one-word edit to that node, reversible by the
same edit. An empty `mood_models` couples *nothing*, so a mis-typed config
degrades to today's behaviour rather than to coupling everything.

### Layer 2a — the sense that needed no credential

The plan's ingest (doorbell/motion → `mechanosensory`, HA deltas →
`hygro_thermo`) is blocked on a Home Assistant token. gladosd's own inbound
traffic is not: every request is an event, and `hearing_JO` is what a fly
hears with. **Verified before being wired** (`probe_hearing.py`), because this
service already has one dead input — all 4,102 photoreceptors at full drive
move no readout, the optic paths are not in the traced subset, so a plausible
site name is not evidence:

```
drive   0      0.02   0.05   0.1    0.2    0.4
arousal 0.000  0.004  0.018  0.057  0.106  0.188    40 sd at the top
valence +0.010 +0.006 +0.011 +0.012 -0.005 +0.003   drifts NEGATIVE
```

Monotonic in arousal. The valence drift is worth keeping: under sustained
traffic her valence goes slightly negative — she is measurably annoyed by being
talked to. Nobody authored that; it is what the connectome does when its
auditory nerve is held on.

Traffic accumulates into a leaky level (`tau` 90 s, `kick` 0.06, capped 0.4 —
~0.3 saturates a site) rather than setting a drive per request, because a
request is an instant and a mood is not.

**The rescaling is not cosmetic.** Raw arousal cannot exceed ~0.19 through this
sense, so every threshold at 0.20+ was unreachable and she would have read
`idle` forever — the coupling would have been exactly the theatre the plan
warned about. `mood.AXIS_SPAN` rescales against the measured ceiling. Only
`arousal` is listed: it is the one axis with a clean monotonic ladder. The axis
that would drive the others properly is `mechanosensory` (reach 9.4e-2 into
`escape_GF` against hearing's 2.4e-3), and that needs the HA token.

### Measured end to end

`proof_fly_centred.py`, with a control arm — the same prompt to the uncoupled
tag under the same conditions, so the effect cannot be a coincidence of load:

```
rest   state=idle    raw arousal 0.0000  temp 0.7    "Do I look like a doorman to you?"
busy   state=alert   raw arousal 0.1609  temp 0.869  "Oh, just another unwelcome visitor. What a surprise."
ctrl   qwen2.5:7b-instruct -> mood_skipped=model_not_coupled
```

Decay back to rest after the burst: `alert` (t+60s) → `stirring` (t+120s) →
`idle` (t+180s). 9 checks pass; raw JSON in
`/opt/paperclip-glados/results/fly_centred_proof.json`.

### Rules the coupling obeys

1. **Fail open, always.** GLaDOS was mute for days because one host went down;
   the fly must never do that again. moodd down, slow, unauthorised, stale or
   raising anything unforeseen all mean "send exactly the bytes n8n sent".
   Every branch is exercised against a real socket in `test_mood.py` — a closed
   port, an unreachable host, a listener that accepts and then hangs, a missing
   token, a stale snapshot. Worst case measured: 301 ms, then uncoupled.
2. **Append, never edit.** One `\nCurrent state: <phrase>.` clause on the end
   of the system message. The 22 live prompts stay a strict byte prefix.
3. **Inside the node's own band.** Arousal may only *raise* temperature, by at
   most 0.3, never past 0.9. A node that chose 0.2 chose determinism and keeps
   its floor. `num_predict` is untouched — n8n owns 80–120.
4. **Never on the request path.** `note_request()` takes a lock and increments
   an int (measured 0.37 µs); all I/O is on a background thread, so moodd can
   never add latency to a reply.
5. **Never write a site we do not own.** `set_drive()` updates only the names
   given, so the HA ingest can land later without the two fighting.
6. **Always hand her back at rest.** On start the ingest posts `hearing_JO: 0`
   before anything else — which is what actually covers a restart, since
   systemd's SIGTERM does *not* run `atexit`. Verified: after
   `systemctl restart`, moodd's drive vector is all zeros.

### Reverting

`mood_coupling: false` in `config.json` plus a restart returns to exactly the
pre-coupling behaviour. `mood_ingest: false` stops driving the fly but leaves
the coupling readable. Neither touches n8n, and `ollama rm glados` cannot harm
the base model the live workflows call.

### Known limits

- **Non-reproducible by design.** Same doorbell, different line. No workflow
  parses her output (they pass it to Discord/HA/TTS as text), and the event log
  records the raw vector, the rescaled vector, the phrase and the effective
  temperature per request, so "why did she say that" stays answerable.
- Only `arousal` is usefully driven today. `novelty`, `reinforcement` and
  `agitation` wait on the mechanosensory ingest, i.e. on the HA token.
- `agitation` is **inverted** — 1.0 is undisturbed and coiled, 0.0 is maximally
  driven, and mechanosensory drive takes it 0.889 → 0.233. Reading a resting
  0.85 as "agitated" would invert her entirely; it is the single easiest
  mistake to make against this service.

---

## One model called `glados` — every engine on this host behind one name

> *"can we cobble every model we have running in to the GLaDOS model, like the
> mondream the kokoro and wispher all that in one model called GLaDOS"*

Done, at the level a caller sees. `"model": "glados"` now **sees, hears, thinks
and speaks**. Nothing else has to be named:

| part of her | engine | where | device | ours? |
|---|---|---|---|---|
| state | drosophila connectome, 164,587 neurons | `:9099` moodd | 2× T400 | **yes** |
| words | `qwen2.5:7b-instruct` | `:11501` ollama | CPU, 10 threads | no |
| sight | `moondream` | `:11501` ollama | GPU 0 | no |
| hearing | `whisper large-v3` | `:9097` sttd | GPU 0 | no |
| voice | `kokoro` | `:9098` ttsd | GPU 0 | no |

`GET /capabilities` returns that table live, including the `ours` column.

### What "one model" does and does not mean

It is **one name, one API and one personality over five engines — not one set
of weights.** A 7B decoder-only transformer, a ViT captioner, an
encoder-decoder ASR model and an ONNX vocoder have different architectures,
tokenisers and output spaces; you cannot concatenate them into a file. Models
that genuinely are single-weight multimodal are *trained* that way from the
start, which needs training headroom this host does not have (see the IST-270
plan addendum, and `senses.py`'s docstring). Said plainly in both places so the
claim never drifts upward.

### The request extensions

Additive to the Ollama API, following the existing `images` convention:

| field | effect |
|---|---|
| `images: [b64]` | captioned by moondream, folded into the prompt as text, key removed |
| `audio: [b64]` | transcribed by whisper, becomes the utterance. Top level on `/api/generate`, on a message for `/api/chat` |
| `speak: true` | the reply is rendered and returned as `audio` (base64 WAV) plus `audio_ms` / `audio_voice` / `audio_rate`. Needs `stream: false` |
| `voice`, `speed` | Kokoro voice name and 0.5–2.0 rate; defaults `af_jessica` @ 0.9 |
| `language` | whisper hint, default `en` |

```bash
# she is shown the doorstep, spoken to, and answers out loud - one request
curl -s the GPU host:11434/api/chat -d '{
  "model":"glados","stream":false,"speak":true,
  "messages":[{"role":"user","content":"","images":["<b64 jpg>"],
               "audio":["<b64 wav>"]}]}' | jq -r '.message.content, .audio_ms'
```

All five extension keys are **stripped before the body reaches ollama** — a 400
from the backend on a field *we* invented would be our bug wearing her face.
`sense_keys_stripped` in the event log records what was removed.

### Fenced to `glados`, which is what keeps the n8n ruling true

`multimodal_models: ["glados"]` — the same fence `mood_models` uses.
`qwen2.5:7b-instruct`, the tag all 22 live n8n nodes name, gets **none** of
this, so IST-271's re-point stays the byte-identical host swap IST-266
accepted. `proof_one_model.py` carries a control arm that sends all four
extension fields to the qwen tag and asserts they do nothing; without it, "the
senses work" and "the re-point is unchanged" would both be assertions rather
than measurements.

Unlike `mood_coupling`, `multimodal` ships **on**. That is not an
inconsistency: coupling changes what an existing caller gets back, so it had to
be opt-in, whereas the senses do nothing unless a request actually carries
`audio` / `images` / `speak`, and the only model they apply to did not exist
before today.

### No sense can take her words away

She was mute for days because one host went down. Every added engine is tested
dead, refusing, unauthorised and **hung** (a socket that accepts and never
answers — the branch a closed port does not cover), and in each case a request
that has text still gets its answer. Kokoro failing sets `audio_error` on an
otherwise normal reply: silent, never mute.

**One deliberate exception.** A request whose *only* content is audio or an
image, where that sense failed, returns **502** naming the dead sense.
Answering anyway would mean inventing an utterance nobody made. Fail open means
never losing words she has; it must not mean fabricating words she never heard.

### Verification

```
python3 /opt/paperclip-gladosd/test_senses.py        # 71 passed, 0 failed
python3 /opt/paperclip-gladosd/proof_one_model.py    # 20 passed, 0 failed
```

`test_senses.py` proves each sense against the **real** services, not mocks —
and the hearing test renders its own audio with Kokoro and feeds it back to
whisper, so her ears are proved against her own voice rather than a fixture
someone trimmed until it passed. Measured: `"The cake is a lie and the doorbell
is broken."` out and back, word for word, 1366 ms of STT on 2709 ms of audio.

`proof_one_model.py` goes over the wire to the live service only — no imports,
no internals — because "one model" is a claim about the API. Raw results in
`/opt/paperclip-glados/results/one_model_proof.json`.

### GPU 0 hygiene

GPU 0 holds the whole voice stack, and moondream now joins it on demand. The
vision delegate goes through `apply_placement_policy` rather than hand-rolling
its body, specifically so it inherits the `keep_alive` clamp and the GPU pin
that already protect that card. Measured during a vision request: GPU 0 peaks
at **4962 of 6144 MiB (1182 MiB headroom)**, moondream fully resident
(`size_vram` 1.15 GiB, no CPU spill) and expiring on the 300 s clamp rather
than pinning the card. A vision request answers in ~4–6 s; an audio + image +
speak request in ~7 s.

### Known limits

- `speak` requires `stream: false`. An ndjson stream has nowhere to put a WAV,
  so the combination is a clear 400 rather than audio silently dropped.
- Audio in must be **RIFF/WAVE**. There is no `ffmpeg` or `sox` on this host,
  so an mp3 is refused by name here rather than failing inside sttd with a less
  useful message.
- Sight is a *caption*, not vision in the language model: moondream describes
  the image and qwen reads the sentence. She cannot be asked to read small text
  in a photo or count things reliably, because she never sees the pixels.
- The three GPU senses share GPU 0 with each other. Concurrency is fine at the
  measured headroom, but a long moondream keep_alive plus a live phone call is
  the one combination to watch; the clamp is what stops it.

### The spelling, and a trap it hides

`"model": "GLaDOS"` works exactly as typed — ollama resolves model names
**case-insensitively**, so the single lowercase `glados` manifest answers to
`GLaDOS`, `GLADOS` and `GlaDos`, and both fences (`mood_models`,
`multimodal_models`) lower-case their comparison so every spelling gets the
same model, the same senses and the same fly. Verified:

```
"model": "GLaDOS"  ->  "Hmm, urns, caps, dresses... typical. And a Portal Gun
                        inquiry. How original."   + 4608 ms of audio
```
(one request: the doorstep image, a spoken line, and `speak: true`.)

**The trap:** because resolution is case-insensitive, `ollama rm GLaDOS`
deletes `glados`. There is no separate uppercase tag to remove — don't make
one, and don't "clean up" a spelling variant. Rebuild with
`OLLAMA_HOST=127.0.0.1:11501 ollama create glados -f Modelfile.glados`; it is
byte-identical (same digest `b26d031a9a6a`) and costs no disk, since `FROM` a
local tag reuses the same blobs.
