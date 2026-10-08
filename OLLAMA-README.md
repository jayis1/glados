# The model page description

An ollama.com model page has its own short readme, and it is edited in the web
UI — nothing in a Modelfile or a `push` carries it. So it lives here, in the
repo, and gets pasted in: open <https://ollama.com/jais/GLaDOS>, click **Edit**
next to *Readme*, paste everything below the line, save.

Kept deliberately short. It is a shop window, not the manual; the manual is the
repo README, and the page links to it.

---

**An AI whose moods come from a fruit fly.**

Not a metaphor, and not a prompt that says "pretend to have feelings". There is
a simulation of a real *Drosophila melanogaster* brain behind her — 164,587
neurons and 24,539,704 synapses, reconstructed from electron micrographs of an
actual fly — running on two second-hand GPUs in a cupboard. Its firing rates are
read out five times a second, and they decide what kind of mood she is in.

Her language model is downstream of an insect. The insect is in charge.

```bash
ollama run jais/GLaDOS
```

That gets you the persona and the sampling band, and she is good company on her
own. The fly is the other half: a small service reads the simulation, turns five
axes — arousal, novelty, valence, reinforcement, agitation — into one sentence
appended to her system prompt and a temperature, and hands the request on. Set
it up from the repo:

**<https://github.com/jayis1/glados>**

Things we did not expect, all written up there with the raw measurements:

- Her **valence drifts downward while she is being talked to**. Nobody wrote
  that. It is what the connectome does when you drive the auditory nerve.
- All 4,102 **photoreceptors at full blast move nothing at all** — the optic
  paths are not in the traced subset. A plausible-sounding sensory input is not
  evidence that it is wired to anything.
- **Agitation is inverted**: 1.0 is a fly at rest, 0.0 is a fly being shouted
  at. Read it the intuitive way round and you invert her entire personality.

Bring your own brain, too — `./byom.sh llama3.1:8b` puts the same fly behind any
Ollama base, local tag or Hugging Face GGUF. A bigger model does not make the fly
less in charge; it renders the fly's state more fluently.

Language by **Qwen2.5**. The connectome is
[**MaleCNS v1.0**](https://male-cns.janelia.org/), by Janelia FlyEM and Google
Research, CC-BY 4.0 — the actual scientific achievement here, thousands of hours
of electron microscopy by people who were not thinking about voice assistants.
GLaDOS is Valve's character, from *Portal*: an affectionate homage, no
affiliation, no cake.
