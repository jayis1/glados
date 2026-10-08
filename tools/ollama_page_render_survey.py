#!/usr/bin/env python3
"""Which markdown features does ollama.com actually render in a model readme?

We cannot preview our own page (the preview route is cookie-gated too, see
below), so the only way to know whether a table or a blockquote will survive is
to look at pages that already have one. This fetches a sample of model pages,
pulls the rendered readme out of the #display element, and counts the HTML
elements that came out of it.

A count of 0 across the whole sample is NOT proof the feature is unsupported -
it can equally mean nobody in the sample used it. The output says which of the
two it cannot distinguish, per feature, rather than rounding to a verdict.

    python3 tools/ollama_page_render_survey.py > measurements/raw/<name>.json
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request

PAGES = [
    "jais/GLaDOS",
    "library/llama3.2", "library/llama3.3", "library/qwen2.5", "library/qwen3",
    "library/deepseek-r1", "library/gemma3", "library/mistral", "library/phi4",
    "library/llava", "library/nomic-embed-text", "library/codellama",
    "library/granite3.3", "library/smollm2", "library/command-r",
    "library/dolphin3", "library/minicpm-v", "library/wizardlm2",
]

FEATURES = [
    ("heading", r"<h[1-6]\b"),
    ("table", r"<table\b"),
    ("strong", r"<strong\b"),
    ("em", r"<em\b"),
    ("list", r"<[uo]l\b"),
    ("list_item", r"<li\b"),
    ("code_block", r"<pre\b"),
    ("code_span", r"<code\b"),
    ("blockquote", r"<blockquote\b"),
    ("link", r"<a\s[^>]*href"),
    ("image", r"<img\b"),
    ("rule", r"<hr\b"),
]


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    with urllib.request.urlopen(req, timeout=30) as fh:
        return fh.read().decode("utf-8", "replace")


def readme_html(page):
    """The rendered readme only. Everything outside #display is chrome."""
    i = page.find('id="display"')
    if i < 0:
        return None
    start = page.rindex("<", 0, i)
    depth, j = 0, start
    while True:
        m = re.compile(r"</?div\b").search(page, j)
        if not m:
            return None
        depth += -1 if page[m.start():m.start() + 2] == "</" else 1
        j = m.end()
        if depth == 0:
            return page[start:j + 6]


def main():
    out = {"measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "pages": {}, "support": {}, "errors": {}}

    for name in PAGES:
        try:
            html = fetch("https://ollama.com/" + name)
        except (urllib.error.URLError, OSError) as exc:
            out["errors"][name] = str(exc)
            continue
        seg = readme_html(html)
        if seg is None:
            out["errors"][name] = "no #display element"
            continue
        text = re.sub(r"<[^>]+>", "", seg).strip()
        out["pages"][name] = dict(
            readme_html_bytes=len(seg), readme_text_chars=len(text),
            **{f: len(re.findall(p, seg, re.I)) for f, p in FEATURES})

    for feature, _ in FEATURES:
        seen = [p for p, c in out["pages"].items() if c[feature]]
        out["support"][feature] = {
            "pages_with_it": len(seen),
            "example": seen[0] if seen else None,
            # The honest third outcome. 0/N cannot tell "stripped" from "unused".
            "verdict": "renders" if seen else "unmeasured: absent from the whole"
                                              " sample, which may mean nobody"
                                              " used it",
        }

    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
