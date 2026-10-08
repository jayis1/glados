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
