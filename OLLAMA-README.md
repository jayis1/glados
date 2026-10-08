# Putting the readme on the ollama.com model page

**The page text itself is [`ollama-page.md`](ollama-page.md).** This file is only
the instructions for getting it onto <https://ollama.com/jais/GLaDOS>.

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

That is the most likely reason a paste "went in" and the page did not change.
Before anything else: look for your avatar in the top right of
<https://ollama.com/jais/GLaDOS>. If it says **Sign in**, that tab cannot write,
however convincing the edit box looks.

The second most likely reason is the **Preview** tab: it renders the markdown
without saving it, so a page that previews perfectly is still unsaved until
**Save** is clicked.

Route 2 below now reads the page back after writing and says which of these
happened, instead of leaving you to guess.

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
(async()=>{const P=b=>fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams(b)});const t=(await(await fetch('https://raw.githubusercontent.com/jayis1/glados/main/ollama-page.md')).text()).trim();const d=await P({summary:'An AI whose moods come from a fruit fly. A 164,587-neuron Drosophila connectome runs continuously on two GPUs; its firing rates set her mood and sampling temperature. Swappable mouth, Qwen2.5-7B by default. Code and measurements: github.com/jayis1/glados'});const r=await P({readme:t});const h=await(await fetch(location.pathname,{cache:'no-store'})).text();const m=h.match(/<textarea[^>]*id="editor"[^>]*>([\s\S]*?)<\/textarea/);const got=m?new DOMParser().parseFromString(m[1],'text/html').documentElement.textContent.trim():'';const ok=got.includes('## ')&&Math.abs(got.length-t.length)<80;alert(ok?'SAVED and verified: '+got.length+' chars, markdown intact. Reloading.':((d.status===401||r.status===401)?'NOT SAVED (401). This tab is signed out of ollama.com - sign in, then run this again.':'NOT SAVED: description '+d.status+', readme '+r.status+'; the page is still holding '+got.length+' chars'));if(ok)location.reload()})()
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
javascript:(async()=>{const P=b=>fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams(b)});const t=(await(await fetch('https://raw.githubusercontent.com/jayis1/glados/main/ollama-page.md')).text()).trim();const d=await P({summary:'An AI whose moods come from a fruit fly. A 164,587-neuron Drosophila connectome runs continuously on two GPUs; its firing rates set her mood and sampling temperature. Swappable mouth, Qwen2.5-7B by default. Code and measurements: github.com/jayis1/glados'});const r=await P({readme:t});const h=await(await fetch(location.pathname,{cache:'no-store'})).text();const m=h.match(/<textarea[^>]*id="editor"[^>]*>([\s\S]*?)<\/textarea/);const got=m?new DOMParser().parseFromString(m[1],'text/html').documentElement.textContent.trim():'';const ok=got.includes('## ')&&Math.abs(got.length-t.length)<80;alert(ok?'SAVED and verified: '+got.length+' chars, markdown intact. Reloading.':((d.status===401||r.status===401)?'NOT SAVED (401). This tab is signed out of ollama.com - sign in, then run this again.':'NOT SAVED: description '+d.status+', readme '+r.status+'; the page is still holding '+got.length+' chars'));if(ok)location.reload()})()
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
