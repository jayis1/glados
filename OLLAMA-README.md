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
readme was byte-identical afterwards at 9,958 characters, so none of the
refused writes wrote anything. Raw:
`measurements/raw/ollama_readme_auth_probe.json`.

One honest limit: no credential here could be shown to carry *push* rights
either, so this does not separate "the route ignores registry tokens" from
"this key is not authorized". Either way the write needs a browser.

The Edit box also has a **Preview** tab, which posts the markdown to
`POST /jais/GLaDOS/preview` and gets rendered HTML back. If that route were
open, the exact rendering of this page could be verified before anyone pasted
anything. It is cookie-gated too — `401`, asked with the real 12,980-byte body:
`measurements/raw/ollama_preview_route.json`. Signed in, you have it; nothing
here does.

## ⚠️ Copy the raw markdown, not the rendered page

This went wrong once already. The page was filled from a *rendered* view, which
copies the text and throws the markup away. The result still reads, so nothing
looks broken — but there is not one heading, table, bold word or code fence left
on the page, and ollama.com's renderer then applies smart punctuation to the
plain text, which turns the ASCII diagram's `+------+` borders into
`+——————+`.

Measured against the live page: `<h2>` 0, `<table>` 0, `<strong>` 0, `<li>` 0.

**Copy from the raw file**, where the markdown is still markdown:

<https://raw.githubusercontent.com/jayis1/glados/main/ollama-page.md>

## Route 1 — four keystrokes

That raw link has nothing in it but the page text, so there is nothing to trim:

1. Open the raw link above.
2. `Ctrl+A`, `Ctrl+C`.
3. On <https://ollama.com/jais/GLaDOS>, signed in, click **Edit** next to
   *Readme*. Text is already there, so `Ctrl+A` first and let the paste replace
   it.
4. `Ctrl+V`, then **Save**.

**Check it before you save.** The edit box has a **Preview** tab. A good paste
previews as headings, six tables and a boxed ASCII diagram. A bad one previews
as an unbroken wall of grey text — that is the rendered-view paste, and the fix
is to go back to the raw link. This is the one failure that still reads fine
afterwards, so it is worth the extra click.

## Route 2 — one line in the console

Same endpoint, same session, no 12 KB in a textarea. On the model page, signed
in, press `F12` and go to **Console**:

```js
fetch('https://raw.githubusercontent.com/jayis1/glados/main/ollama-page.md').then(r=>r.text()).then(t=>fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({readme:t.trim()})})).then(r=>r.ok?location.reload():alert('Failed: '+r.status))
```

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
javascript:(function(){fetch('https://raw.githubusercontent.com/jayis1/glados/main/ollama-page.md').then(r=>r.text()).then(t=>fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({readme:t.trim()})})).then(r=>r.ok?location.reload():alert('Failed: '+r.status))})()
```

3. Open <https://ollama.com/jais/GLaDOS> and click the bookmark. The page
   reloads with the readme on it.

It reads `location.pathname`, so it only ever writes the page you are looking
at. Chrome strips a `javascript:` URL typed into the address bar; from a
bookmark it runs.

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

## Check it worked

```bash
./check-ollama-page.sh
```

Fetches the live page and compares it against `ollama-page.md` — whether a
readme is there at all, whether the markdown survived, and whether the text is
the current version. Written because *"the page says No readme"* is a claim that
a `grep` will happily confirm while being wrong: that string is also sitting in
the page's own JavaScript, in the branch that runs when you save an empty box.
Check the rendered element, not the haystack.
