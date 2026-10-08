# Bring Your Own Model

**The fly is the part that is ours. The mouth is a replaceable part.**

```bash
./byom.sh llama3.1:8b          # -> glados-llama3.1
./byom.sh qwen3:32b big        # -> glados-big
./byom.sh hf.co/user/repo:Q4   # -> glados-repo
```

Same persona, same sampling band, same five axes, same 164,587 neurons.
Different weights doing the talking.

---

## Why this exists at all

`qwen2.5:7b-instruct` is not here because it is the best language model. It is
here because it fits in the RAM of one 2013 Xeon that also has to run a fly.

That is an embarrassing reason for a permanent decision, and the architecture
was already built to make it a temporary one:

> The fly holds the state. The language model is a **stateless renderer** of
> that state. Renderers are swappable.

Nothing about who she is lives in the weights. Her continuity is membrane
potentials in a simulated connectome; her personality is 30 words in a
`Modelfile`; her mood is a clause and a temperature computed outside the model
entirely. The language model is handed a situation and asked to phrase it. You
can hand that job to a better model tomorrow and she will not have changed —
she will just be more articulate about it.

**Still a fruit fly at the helm.** A bigger base does not make the fly less in
charge; it makes the fly's state better rendered. The causal direction never
moves.

---

## The one rule: the name is the fence

gladosd decides what the fly drives by **name prefix**
([`model_coupled`](../server/mood.py)). Everything whose name starts with an
entry in `mood_models` — shipped as `["glados"]` — gets the state clause and
the temperature nudge. Everything else goes through byte-identical.

So `glados-llama3.1`, `glados-big` and `glados-anything-you-like` are coupled
**the moment they exist**: no config edit, no restart, no server change. And
`mistral-glados` is not coupled, ever, which is why `byom.sh` renames it for
you instead of printing a warning you would scroll past.

The fence is a feature, not an accident. It is what lets a host run a
fly-driven GLaDOS *and* serve a plain, untouched `qwen2.5:7b-instruct` to
software that must not suddenly develop opinions. If you want a non-`glados-*`
name coupled anyway, name it in `config.json` and restart:

```json
"mood_models": ["glados", "my-weird-tag"]
```

An **empty** list couples nothing. A mistyped config should degrade to today's
behaviour, never to coupling everything you own.

---

## Proof, because "it is coupled" is exactly the kind of claim that quietly isn't

[`server/proof_byom.py`](../server/proof_byom.py) builds **two tags from the
same base that differ only in their name**, sends each one identical request,
and reads gladosd's own event log:

| | `glados-byom-proof` | `byom-proof-control` |
|---|---|---|
| base weights | `llama3.2:1b`, blob `74701a8c35f6c8d9` | *the same blob* |
| named in any config | no | no |
| `mood_applied` | **true**, clause at `messages[0].content` | **false** |
| `mood_skipped` | — | `model_not_coupled` |
| system message as sent | `…One sentence.\nCurrent state: idle, undisturbed, patient.` | `…One sentence.` (untouched) |
| she said | *"Another insignificant creature thinking it can disturb me? How quaint."* | *"Oh, great. Another human who thinks they can just drop by without an invitation."* |

10/10 assertions, raw output in
[`measurements/raw/byom_proof.json`](../measurements/raw/byom_proof.json).
The base is deliberately **not** the deployed `qwen2.5:7b-instruct` — a proof
run against the language organ she already has would prove nothing about
bringing your own. Both proof tags are removed afterwards and the live tag list
is re-read to show it survived.

Run it yourself:

```bash
python3 server/proof_byom.py --base llama3.2:1b --json /tmp/byom.json
```

---

## What your base needs to have

Almost nothing. It must be something `ollama create ... FROM` accepts: a local
tag, a registry model, or `hf.co/<user>/<repo>:<quant>`. Two details decide
whether the coupling actually reaches it, and both are easy to drop when
hand-rolling a Modelfile:

**1. A `temperature` parameter, and a client that sends one.**
Arousal may only *raise* a temperature the caller already chose, by at most
0.3, never past 0.9. A request with no temperature at all gets no nudge —
inventing sampling for a caller that deliberately took the backend default is
not our business. `byom.sh` writes `PARAMETER temperature 0.7` so there is
something to push against.

**2. A real system message in the request.**
The clause is **appended** to an existing system prompt and never authored
from nothing. Clients that send one — every n8n node, every OpenAI-compatible
SDK, anything using `/api/chat` properly — get the full coupling.

`ollama run <tag>` from a terminal does **not** send one. It sends
`/api/generate` with `"system": ""`, which means *"I have no prompt of my own,
use the model's"*. So from the terminal you get her persona, her sampling and
her voice, and no state clause.

That is the right way round, and it took a bug to find out why.

> **The BANANA incident.** A request-level system prompt *overrides* the
> Modelfile's at the backend. The coupling used to append its clause to that
> empty string — making it non-empty, and therefore an override — which
> silently replaced her entire persona with four words of mood. Measured on a
> probe model whose SYSTEM was *"always answer with exactly the single word
> BANANA"*: straight to the backend, **"BANANA"**; through the coupling,
> **"The capital of France is Paris."** The persona was gone and the only
> symptom was a GLaDOS who answered helpfully.
>
> Fixed: an empty or whitespace-only system prompt now counts as *no* system
> prompt, the body is left byte-identical, and the event says
> `mood_skipped: no_system_message`. Eight regression checks in
> [`server/test_mood.py`](../server/test_mood.py), both spellings of empty,
> both `/api/chat` and `/api/generate`. **A mood is an addition to who she is,
> never a replacement for it.**

If you want moods *and* a terminal, send your own system message — `/set
system "You are GLaDOS…"` inside `ollama run`, or use `/api/chat` with a
system role.

---

## Choosing a bigger brain, honestly

The fly does not care how big the model is. Your hardware will.

- **Her mouth runs on CPU here**, because this box's GPUs are busy being a fly
  and a voice, and because a T400 is bandwidth-bound enough that full offload
  of a 7B measured *slower* than a tuned CPU (7.06 vs 7.39 tok/s). Your box is
  probably not shaped like that. `byom.sh` deliberately pins **no** device
  placement; see [`Modelfile.cpu-tuned`](../Modelfile.cpu-tuned) for what the
  deployed numbers are and why they are local superstition.
- **The fly needs ~4 GB on each of two CUDA cards** and is entirely separate
  from the language model. A bigger mouth does not slow her down; it competes
  for RAM, not for her.
- **She is a one-liner.** `PARAMETER num_predict 100` is load-bearing. Without
  it, a 32B will write you a beautifully in-character essay, which is worse
  than it sounds.
- **Smaller works too, and is funnier.** The proof above runs on a 1B, and a
  1B GLaDOS is a perfectly adequate GLaDOS, because the personality is in the
  prompt and the state is in the fly.

---

## What this does not do

- **It does not transfer her state into the new model.** There is no state in
  any model to transfer. The fly keeps it; that is the entire architecture.
- **It does not fine-tune anything.** No weights are trained, here or ever.
  See [HONESTY.md](HONESTY.md).
- **It does not make the fly smarter.** A better mouth renders the same five
  axes more fluently. `arousal` is still the only one usefully driven today,
  and the one that would fix that is blocked on a home-automation credential
  rather than on model size.
