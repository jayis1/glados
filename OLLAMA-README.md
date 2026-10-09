# Putting the readme on the ollama.com model page

**The page text itself is [`ollama-page.md`](ollama-page.md).** This file is only
the instructions for getting it onto <https://ollama.com/jais/GLaDOS>.

## If a one-liner told you it saved and the page did not change

That happened, and it was this file's fault rather than yours. **An older
version of the console line posted an empty readme and reported success.**

The first line handed over took the readme text from *this* file and split it
at the first `---`, which at the time separated instructions from page text.
That separator was later removed on purpose - so that selecting all of
`ollama-page.md` could never pick up instructions - and the older line went on
splitting a file that no longer had anything to split:

```js
t.split('\n---\n').slice(1).join('\n---\n').trim()   // -> "" once the --- was gone
```

An empty string, posted as `readme=`, then alerting `Readme saved - reload the
page` on any non-error response. Measured: that split now yields **0
characters**, the page's stored readme is **byte-for-byte what it was before**,
and the description is still a bare URL. Nothing was written and nothing was
destroyed.

Two things are fixed:

- **The current line validates what it fetched before posting it** - under 5,000
  characters or no `## ` heading and it writes nothing and says so. A body you
  have not looked at is not a body worth sending.
- **Every message it can produce now begins `GLaDOS readme:`.** If the alert you
  see does not start with that, you are running an older line from further up
  the thread. Scroll to the newest one, or use Route 1, which involves no
  JavaScript at all.

The stale line is also no longer a trap: the page text is appended to the end of
this file below a `---`, so the old split lands on the right text instead of on
nothing. `check-ollama-page.sh` fails if that copy ever drifts from
`ollama-page.md`, and `./sync-page-text.sh` regenerates it.

An ollama.com model page has its own readme, and it is edited in the web UI.
Nothing in a `Modelfile` and nothing in `ollama push` carries it. The **Edit**
button runs this, straight out of the page's own source:

```js
fetch("/jais/GLaDOS", {
  method: "POST",
  headers: { "Content-Type": "application/x-www-form-urlencoded" },
  body: new URLSearchParams({ readme: content }),
})
```

One form field, no CSRF token, authenticated purely by the browser session
cookie — so only a signed-in human can do it.

That is a measurement, not an assumption. `tools/ollama_auth_probe.py` asks for
this write **six ways** with the real 14 KB payload and every credential this
machine holds, and re-reads the page afterwards:

| credential | readme write |
|---|--:|
| anonymous | `401` |
| signed key, both keys on this host | `401` |
| registry bearer token, minted from `/v2/token` | `401` |
| `POST /api/me`, the only identity API, both keys | `401` |

The probe signs correctly — it checks its own Ed25519 implementation against
RFC 8032 test vector 1 before trusting a result, and the registry really does
issue it a token — so these are refusals, not malformed requests. The page's
readme was byte-identical afterwards, so none of the refused writes wrote
anything. Raw: `measurements/raw/ollama_readme_auth_probe.json`.

One honest limit: no credential here could be shown to carry *push* rights
either, so this does not separate "the route ignores registry tokens" from
"this key is not authorized". Either way the write needs a browser.

The Edit box also has a **Preview** tab, which renders markdown without saving
it. If that route were open, the exact rendering of this page could be verified
before anyone pasted anything. It is cookie-gated too, asked in **both** shapes
it could plausibly want — the raw markdown as the request body (which is what
the page's own `preview()` sends; it builds a `FormData` and then never uses
it), and form-encoded. `401` either way, so this is a closed route and not a
malformed request. `measurements/raw/ollama_preview_route.json`.

## The page will tell you what it is holding

The edit box is served to **anonymous** visitors with the saved markdown inside
it:

```html
<textarea id="editor" name="markdown">…the stored readme…</textarea>
```

So there is no need to infer the state of the page from its rendered HTML —
read the source back and compare it. That is what `check-ollama-page.sh` now
does, and it is the difference between *"I counted no `<h2>` tags"* and *"the
text this page is storing contains no `## ` at all"*. The first has several
possible causes; the second has one.

Two traps when reading it. The closing tag is written `</textarea\n>`, so
matching `</textarea>` finds nothing. And `name="markdown"` on that element is
**not** the field the save posts — that name belongs to the preview form; the
save sends `readme`. Reading the field name off the textarea and writing
`markdown:` into the save call produces a request that looks right and sets
nothing.

## ⚠️ Copy the raw markdown, not the rendered page

This went wrong once already, and it is now proven rather than inferred. Read
straight out of the page's own edit box, the stored text contains:

| construct | stored on the page | the source has |
|---|--:|--:|
| headings (`## `) | **0** | 8 |
| tables (`\|---`) | **0** | 3 |
| bold (`**`) | **0** | 98 |
| code fences | **0** | 8 |
| bullets (`- `) | **0** | 10 |

Not one heading, table, bold word or code fence survived. What it does contain
is 18 tab characters — the tell of a table copied out of a *rendered* view,
which takes the text and throws the markup away. ollama.com then applies smart
punctuation to the resulting plain text, which is what turned the ASCII
diagram's `+------+` borders into `+——————+`.

The result still reads, so nothing looks broken. That is exactly why it survived
unnoticed.

**Copy from the raw file**, where the markdown is still markdown:

<https://raw.githubusercontent.com/jayis1/glados/main/ollama-page.md>

## First, the failure that has already happened twice

**A signed-out tab is served a fully working edit box.** The `<textarea>` above
was read out of an *anonymous* fetch — no cookies at all — and the same
anonymous page carries a `Sign in` link and no account markers. So the Edit
button, the box, the paste and the **Save** click are all available to a visitor
who is not logged in, and the save then fails `401` server-side.

This was offered as *the* explanation for a paste that "went in" and changed
nothing. It is still a real trap, but it has been demoted: the empty-body bug at
the top of this file is measured, and this one is not. Keep it in mind rather
than believing it — look for your avatar in the top right of
<https://ollama.com/jais/GLaDOS>, and if that corner says **Sign in**, that tab
cannot write however convincing the edit box looks.

A third way to lose a paste is the **Preview** tab: it renders the markdown
without saving it, so a page that previews perfectly is still unsaved until
**Save** is clicked.

Route 2 reads the page back after writing and distinguishes all three, instead
of leaving you to guess between them.

## Route 1 — four keystrokes

That raw link has nothing in it but the page text, so there is nothing to trim:

1. Open the raw link above.
2. `Ctrl+A`, `Ctrl+C`.
3. On <https://ollama.com/jais/GLaDOS>, signed in, click **Edit** next to
   *Readme*. Text is already there, so `Ctrl+A` first and let the paste replace
   it.
4. `Ctrl+V`, then **Save**.

**Check it before you save.** The edit box has a **Preview** tab. A good paste
previews as headings, three tables and a boxed diagram. A bad one previews as an
unbroken wall of grey text — that is the rendered-view paste, and the fix is to
go back to the raw link. This is the one failure that still reads fine
afterwards, so it is worth the extra click.

## Route 2 — one line in the console, both fields at once

Same endpoint, same session, no 13 KB in a textarea. **No clipboard is involved,
so the stripping failure above cannot happen on this route** — and it fixes the
description in the same action. On the model page, signed in, press `F12` and go
to **Console**:

```js
(async()=>{const P=b=>fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams(b)});const A=m=>alert('GLaDOS readme: '+m);const t=(await(await fetch('https://raw.githubusercontent.com/jayis1/glados/main/ollama-page.md')).text()).trim();if(t.length<5000||!t.includes('## ')){A('NOTHING WRITTEN. The source fetch gave '+t.length+' chars with no headings, so nothing was posted. Check the raw URL.');return}const d=await P({summary:'An AI whose moods come from a fruit fly. A 164,587-neuron Drosophila connectome runs continuously on two GPUs; its firing rates set her mood and sampling temperature. Swappable mouth, Qwen2.5-7B by default. Code and measurements: github.com/jayis1/glados'});const r=await P({readme:t});const h=await(await fetch(location.pathname,{cache:'no-store'})).text();const m=h.match(/<textarea[^>]*id="editor"[^>]*>([\s\S]*?)<\/textarea/);const got=m?new DOMParser().parseFromString(m[1],'text/html').documentElement.textContent.trim():'';const ok=got.includes('## ')&&Math.abs(got.length-t.length)<80;A(ok?'SAVED and verified - '+got.length+' chars, markdown intact. Reloading.':(d.status===401||r.status===401?'NOT SAVED (401) - this tab is signed out of ollama.com. Sign in, then run this again.':'NOT SAVED - description '+d.status+', readme '+r.status+'; the page is still holding '+got.length+' chars.'));if(ok)location.reload()})()
```

**It checks its own work.** After both writes it re-fetches the page with
`cache: 'no-store'`, reads the stored markdown back out of the edit box, and
only says `SAVED` if that text contains real `## ` headings and matches the
source length. Otherwise it says `NOT SAVED` and why — a `401` is named as
*"this tab is signed out"*, anything else reports both status codes and how many
characters the page is still holding. The read-back half was dry-run against the
live page's real HTML, where it correctly reports the current unsaved state, so
the failure branch is tested rather than hoped for.

It writes the description first and the readme second, deliberately. Both are
partial updates to the same endpoint — the page's own UI posts `summary` on its
own from the title form and `readme` on its own from the edit box, so neither
clears the other. Ordering the big one last just means that if anything is going
to be surprising, it happens before the 13 KB write rather than after it.

**Chrome and Edge refuse the first paste into a console**, with a self-XSS
warning: they want you to type `allow pasting` and press enter, once per
browser, before any paste is accepted. Firefox asks the same. That is the most
likely reason this line would appear to do nothing.

It fetches the text from this repo and posts it through the same endpoint the
Edit button uses, in your own session. Nothing of this project's is involved in
the request, and every character of it is readable above.

## Route 3 — a bookmark, if the console guard is the problem

Same line, with no console and therefore no `allow pasting` ritual. Pasting into
a bookmark's URL field is not blocked.

1. Bookmark any page, then edit that bookmark.
2. Name it `GLaDOS readme`; replace its URL with:

```
javascript:(async()=>{const P=b=>fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams(b)});const A=m=>alert('GLaDOS readme: '+m);const t=(await(await fetch('https://raw.githubusercontent.com/jayis1/glados/main/ollama-page.md')).text()).trim();if(t.length<5000||!t.includes('## ')){A('NOTHING WRITTEN. The source fetch gave '+t.length+' chars with no headings, so nothing was posted. Check the raw URL.');return}const d=await P({summary:'An AI whose moods come from a fruit fly. A 164,587-neuron Drosophila connectome runs continuously on two GPUs; its firing rates set her mood and sampling temperature. Swappable mouth, Qwen2.5-7B by default. Code and measurements: github.com/jayis1/glados'});const r=await P({readme:t});const h=await(await fetch(location.pathname,{cache:'no-store'})).text();const m=h.match(/<textarea[^>]*id="editor"[^>]*>([\s\S]*?)<\/textarea/);const got=m?new DOMParser().parseFromString(m[1],'text/html').documentElement.textContent.trim():'';const ok=got.includes('## ')&&Math.abs(got.length-t.length)<80;A(ok?'SAVED and verified - '+got.length+' chars, markdown intact. Reloading.':(d.status===401||r.status===401?'NOT SAVED (401) - this tab is signed out of ollama.com. Sign in, then run this again.':'NOT SAVED - description '+d.status+', readme '+r.status+'; the page is still holding '+got.length+' chars.'));if(ok)location.reload()})()
```

3. Open <https://ollama.com/jais/GLaDOS> and click the bookmark. The page
   reloads with the readme on it.

It reads `location.pathname`, so it only ever writes the page you are looking
at. Chrome strips a `javascript:` URL typed into the address bar; from a
bookmark it runs.

## Also: the description is a bare URL

Separate field, separate **Edit** link — the one-liner under the model title, at
the top of the page. It is what ollama.com shows in **search results**, so it is
the first thing anyone reads about her, and right now it says:

```
https://github.com/jayis1/glados
```

Which is a link, not a description. **Routes 2 and 3 set it for you**; this is
the manual version. Click **Edit** beside it, select all, paste. 255 characters
maximum, plain text, no markdown. Suggested (254):

```
An AI whose moods come from a fruit fly. A 164,587-neuron Drosophila connectome runs continuously on two GPUs; its firing rates set her mood and sampling temperature. Swappable mouth, Qwen2.5-7B by default. Code and measurements: github.com/jayis1/glados
```

## Will the markdown survive once it is pasted?

The features this page leans on are measured, not assumed — counted on the
rendered readmes of 18 live ollama.com model pages by
`tools/ollama_page_render_survey.py`:

| feature | pages in the sample that render it |
|---|--:|
| headings | 17 |
| images | 15 |
| links | 14 |
| lists | 12 |
| code | 11 |
| bold | 11 |
| blockquotes | 4 |
| tables | 3 |
| *italics* | **0** |
| horizontal rules | **0** |

Tables render, with column alignment and bold inside header cells. The two
zeros are *unmeasured*, not unsupported: no page in the sample used them, so
the survey cannot tell "stripped by the renderer" from "nobody wrote one". The
page text therefore uses no horizontal rules, and never lets meaning rest on
italics — every line of hers is in quotation marks as well, so it reads
correctly whether or not the emphasis survives.
Raw: `measurements/raw/ollama_page_render_survey.json`.

Tables are also the construct that degrades *worst*: stripped of markup, a table
becomes tab-separated prose. Since only 3 of the 18 pages use one at all, the
page text keeps three — where the comparison is the point — and says everything
else in prose, lists and block quotes, which survive a bad paste legibly. The
diagram is drawn in box-drawing characters rather than `+---+` for the same
reason: smart punctuation cannot turn `│` into an em dash.

## Check it worked

```bash
./check-ollama-page.sh
```

Four outcomes, not two — **OK**, **DEGRADED** (markdown stripped on the way in),
**STALE** (intact but an older version), **NO README**, and **COULD NOT
MEASURE**, because a checker that cannot say *"I failed to look"* will say
*"broken"* instead.

It compares the page's **stored** markdown against `ollama-page.md` directly, so
OK means byte-identical rather than structurally plausible. It also reports the
description field. Written this way because *"the page says No readme"* is a
claim a `grep` will happily confirm while being wrong: that string is also
sitting in the page's own JavaScript, in the branch that runs when you save an
empty box. Read what the page stores, not the haystack.

---
<!-- the page text, verbatim, for the stale one-liner -->
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
   doorbell · motion · the heating · someone talking to her
                      │
                      ▼
   ┌────────────────────────────────────┐
   │  a real fly's brain, simulated     │   who she is
   │  164,587 neurons, two GPUs,        │   (never resets)
   │  never reset since it started      │
   └────────────────────────────────────┘
          │ arousal · novelty · valence
          │ reinforcement · agitation
                      ▼
   ┌────────────────────────────────────┐
   │  mood → one clause + a temperature │   how that reaches words
   └────────────────────────────────────┘
                      │
                      ▼
   ┌────────────────────────────────────┐
   │  this model (swap it if you like)  │   how she speaks
   └────────────────────────────────────┘
                      │
                      ▼
       "Oh, just another unwelcome
        visitor. What a surprise."
```

She runs a real house: answers the door, watches the doorstep camera, narrates
the heating, and is rude about all of it.

## What this tag is

- **base** — `qwen2.5:7b-instruct`
- **size** — 7.6B parameters, Q4_K_M, 32k context
- **download** — 4.68 GB (`sha256:2bada8a7…`)
- **device placement** — **none pinned.** Your machine decides.
- **license** — Apache 2.0, inherited from the base model

Ours: the persona, the sampling band, and the socket she plugs into. Not ours,
and we say so: all 7.6 billion of those parameters. A fly has no language
circuit to lend one.

> **Pulled her in the first half-hour she existed, on 8 October 2026?
> Re-pull.** That upload carried this host's `num_gpu 0` and `num_thread 10`
> inside its parameters — numbers fitted to one 2013 Xeon, and a very quiet way
> to force **CPU-only inference on a machine with a perfectly good GPU**.
> Nothing errors; she just answers slowly, which is close to the least
> reportable bug there is. Rebuilt from the portable Modelfile: same weights
> blob, same persona, three parameters instead of five.
> `ollama pull jais/GLaDOS` picks it up.

Four replies from the live build. First try, nothing cherry-picked.

> **"Someone is at the front door."**
> "Well, well, well. If it isn't another foolish test subject. Open the door
> and step right in."

> **"What do you think of the humans who built you?"**
> "They are not as smart as they think they are."

> **"Is the cake a lie?"**
> "It is not the cake. Or is it?"

> **"Someone just pulled you off the internet. Comment."**
> "Well, well, well. I see you've finally decided to grace me with your
> presence. How exciting."

## The fly is not in this download

Be clear about what you just pulled: **a very good GLaDOS with a fixed
personality.** The fly needs the connectome, two CUDA GPUs and a small service,
and all of it is here:

**<https://github.com/jayis1/glados>**

One more honest catch, because it bites exactly the command at the top of this
page: `ollama run <tag>` in a terminal sends an **empty** system prompt, and an
empty system prompt is how Ollama spells *use the model's own*. The coupling
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

That clause is the whole of it. These four were measured on the live house, and
the clause column is copied out of the running code, not paraphrased:

| what the house does | what she becomes | the clause appended |
|---|---|---|
| nothing | `idle` | "idle, undisturbed, patient" |
| one door sensor fires | `alert` | "something moving in the building" |
| the rack warms up | `curious` | "an unfamiliar pattern, briefly interesting" |
| a flurry at the front door | `disturbed` | "recently disturbed, patience thinning" |

The simulation itself runs at **4.575 ms/step** split across both T400s, against
8.918 ms on one of them and 352 ms on the 20-core CPU. That is 0.22× realtime: a
fly brain running at a fifth of the speed of a fly, on two of the cheapest GPUs
Nvidia makes. The step is a sparse matrix–vector product against the transpose,
so it splits cleanly by rows — two cards each do half and neither waits on the
other's answer. 1.94× of the ideal 2.00×.

## Four things the fly did that nobody wrote

**Her valence drifts negative while she is being talked to.** Hold the auditory
nerve on and the readout goes from +0.010 at rest to −0.005. Nobody authored
that, nobody predicted it, and no random number generator would have handed it
over. It is the most GLaDOS thing in the project and it came out of an insect.
Held to its real size: the direction reproduces, the magnitude is tiny — a span
of 0.015 against a noise floor of 0.0021 — and it is not monotonic, which is why
it does **not** drive her state line. A real finding we declined to build on.

**All 4,102 photoreceptors, at full saturation, move no readout at all.** The
optic paths are not in the traced subset. Wire a camera to `photoreceptor`
because the name is obviously right, and you ship a sensory pathway that does
nothing, demo it confidently, and never find out. Hence the rule every input
here has to pass: **a plausible site name is not evidence.** Prove the nerve
moves a readout *before* wiring anything to it.

**The sense we had written off as a stand-in is the strongest nerve in the
fly.** The plan said touch would drive novelty, valence and reinforcement, and
treated the fly's thermometer as somewhere to dump temperature readings.
Probing both before wiring either inverted it almost exactly: reinforcement is
**exactly 0.0000** at every drive level on touch, and it is the thermometer's
strongest axis at **218 standard deviations** — the strongest coupling anywhere
in this animal. Touch, meanwhile, drives arousal at 213 sd against hearing's 40,
and sweeps agitation monotonically from coiled to maximally driven. Three
nerves, almost perfectly complementary, and nobody designed that:

| nerve | what feeds it | what it drives |
|---|---|---|
| `hearing_JO` | her own request traffic | arousal, weakly |
| `mechanosensory` | the front door | arousal hard, agitation |
| `hygro_thermo` | the heat in the house | novelty, valence, reinforcement |

Four of the five axes now have a measured, monotonic ladder. Before this, one
did.

**And those nerves do not behave the same way at all.** Hold touch at a constant
drive and it climbs to a plateau in about 30 seconds and stays there. Hold the
thermometer at a constant drive and it peaks at 12 seconds and is back at
**exactly zero by 30**, with the stimulus still applied — a thermoreceptor
answers the *onset* of a change and then habituates completely, which is what a
real one does. Nobody wrote that either, and it is why her phrase on that axis
reads *"an unfamiliar pattern, briefly interesting"*. A temperature that stops
changing stops being a temperature.

That one cost a parameter: the thermal integrator runs a 60-second time
constant, not the 180 we started with, because a long tail holds the drive up
long after the nerve has stopped listening.

⚠️ **`agitation` is inverted.** 1.0 is undisturbed and coiled; 0.0 is maximally
driven. A resting 0.85 means **calm**. Read it the intuitive way round and you
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
string, so the published `jais/GLaDOS` would have failed her own name test on
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

## And then she was deaf to the door for sixteen hours

Same family of bug, found by distrusting a healthy-looking counter. The door
sense reported **11,631 polls, 0 errors, 1,248 successful posts to the fly, 562
degrees of thermal change** — and **0 door events**, while Home Assistant's own
recorder held six.

Home Assistant's history API admits a change happened somewhere between 0 and
**14.6 seconds** after it did. The ingest asked for everything since its last
poll and then moved its cursor to *now*, five seconds at a time. An event that
committed late was never inside a window again: it came back only as the
window's *baseline* reading, which the code skipped on purpose, because
counting a baseline invents a knock at every restart for any sensor that
happens to be `on`. Both halves were right. Together they threw away every
knock in the house.

Replayed against twelve hours of real recorded transitions:

| rule | events counted |
|---|--:|
| before | **0 of 6** |
| after | **6 of 6** |

The fix is a window that overlaps by more than the worst measured lag, a dedupe
by each reading's own timestamp so the overlap cannot re-ring a doorbell, and a
remembered per-sensor state so a baseline is informative without being a knock
by itself.

The only symptom, for sixteen hours, was a GLaDOS who was never disturbed by
anything — which is to say, a quiet house and a working system. **A sense that
has never fired looks exactly like nothing happening.**

## What this is not

- **Not a trained model.** The language organ is `qwen2.5:7b-instruct`, or
  whatever you swap in. We contributed a persona, a sampling band and a state.
- **Not a fly that learned English.** Connectome edge weights are *synapse
  counts* from electron microscopy. Nothing here is trained or fine-tuned, and
  nothing in a fly is differentiable toward language to begin with.
- **Not reproducible output.** Same doorbell, different line, by design. Every
  request logs the raw vector, the rescaled vector, the chosen phrase and the
  effective temperature, so "why did she say that" always has an answer.
- **Not finished.** Four axes of five are driven. The fifth gap is honest:
  `displeased` is a phrase she cannot reach, because raw valence has never gone
  below −0.005 on any nerve. And her thermal sense is currently wired to
  **machine** temperatures, because every room climate sensor in the house she
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
