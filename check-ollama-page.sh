#!/usr/bin/env bash
# Is the readme on https://ollama.com/jais/GLaDOS, and did its markdown survive?
#
# Three outcomes, not two. A checker that can only say "good" or "bad" will say
# "bad" when it simply failed to look, and that is how a page gets re-pasted for
# no reason. Exit 0 = matches, 1 = on the page but wrong, 2 = no readme,
# 3 = could not measure.
set -uo pipefail

MODEL="${1:-jais/GLaDOS}"
SRC="$(dirname "$0")/ollama-page.md"

html="$(curl -fsS --max-time 30 "https://ollama.com/${MODEL}" 2>/dev/null)" || {
  echo "COULD NOT MEASURE: fetch of https://ollama.com/${MODEL} failed"; exit 3; }
[ -f "$SRC" ] || { echo "COULD NOT MEASURE: $SRC not found"; exit 3; }

printf '%s' "$html" | python3 -c '
import html as H, re, sys

page = sys.stdin.read()
src  = open(sys.argv[1], encoding="utf-8").read()

# The rendered readme lives in the #display div. Do not grep the whole page for
# "No readme" - that string is also in the save() handler s JavaScript, in the
# branch that runs when someone saves an empty box, and it is there whether or
# not a readme exists.
i = page.find("id=\"display\"")
if i < 0:
    print("COULD NOT MEASURE: no #display element; the page layout changed")
    sys.exit(3)
j = min((x for x in (page.find("id=\"editorContainer\"", i),
                     page.find("id=\"editor\"", i)) if x > 0), default=len(page))
seg = page[page.find(">", i) + 1 : j]

rendered = H.unescape(re.sub(r"<[^>]+>", "", seg)).strip()
if len(rendered) < 40:
    print("NO README: the page has no readme text")
    sys.exit(2)

# Structure the markdown must have produced. Counted on the rendered HTML,
# because stripped markdown still yields plenty of readable text.
want = {"<h2": src.count("\n## "), "<table": src.count("\n|---"),
        "<strong": src.count("**") // 2, "<pre": src.count("\n```") // 2,
        "<li": src.count("\n- ")}
got  = {t: seg.count(t) for t in want}
missing = [t for t in want if want[t] and not got[t]]

# Is it the current text? Compare on words, since the renderer rewrites
# punctuation (-- becomes an em dash, quotes become curly).
def words(s):
    return re.findall(r"[a-z0-9]+", s.lower())
body = set(words(src))
live = set(words(rendered))
drift = sorted(body - live)

for t in sorted(want):
    print(f"  {t+chr(62):10} page {got[t]:3}   source expects {want[t]:3}")
print(f"  text       page {len(rendered):5} chars, source {len(src)} chars")

if missing:
    print()
    print("DEGRADED: the markdown did not survive the paste - no "
          + ", ".join(t.lstrip(chr(60)) for t in sorted(missing)))
    print("Fix: copy the RAW file, not a rendered view. See OLLAMA-README.md.")
    sys.exit(1)
if len(drift) > 25:
    print()
    print(f"STALE: {len(drift)} words in ollama-page.md are missing from the "
          f"page, e.g. {drift[:8]}")
    sys.exit(1)
print()
print("OK: readme present, markdown intact, text current")
' "$SRC"
