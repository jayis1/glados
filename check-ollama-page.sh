#!/usr/bin/env bash
# Is the readme on https://ollama.com/jais/GLaDOS, and is it the right text?
#
# Four outcomes, not two. A checker that can only say "good" or "bad" will say
# "bad" when it simply failed to look, and that is how a page gets re-pasted for
# no reason. Exit 0 = matches, 1 = on the page but wrong, 2 = no readme,
# 3 = could not measure.
#
# This reads the STORED markdown, not the rendered HTML. The edit box
# (<textarea id="editor">) is served to anonymous visitors with the saved source
# inside it, so the page will tell you exactly what it is holding. That beats
# inferring from rendered output: counting <h2> tags can only guess at why they
# are missing, whereas the stored source settles it - if there is no "## " in
# what the page is holding, the markdown was lost on the way in, full stop.
set -uo pipefail

MODEL="${1:-jais/GLaDOS}"
SRC="$(dirname "$0")/ollama-page.md"

[ -f "$SRC" ] || { echo "COULD NOT MEASURE: $SRC not found"; exit 3; }

# Pre-flight: the duplicate copy of the page text that disarms the stale
# one-liner (see sync-page-text.sh) must still match this file. A drifted copy
# would quietly hand an old readme to anyone who runs the line from the issue
# thread, which is the exact failure that copy exists to prevent.
HOWTO="$(dirname "$0")/OLLAMA-README.md"
if [ -f "$HOWTO" ]; then
  python3 - "$SRC" "$HOWTO" <<'PY' || exit 3
import sys
src = open(sys.argv[1], encoding="utf-8").read().strip()
howto = open(sys.argv[2], encoding="utf-8").read()
parts = howto.split("\n---\n", 1)
if len(parts) != 2:
    sys.exit("COULD NOT MEASURE: OLLAMA-README.md has no `---` separator, so "
             "the stale one-liner from the issue thread would post an empty "
             "readme again. Run ./sync-page-text.sh")
tail = parts[1].strip()
if tail.startswith("<!--") and "\n" in tail:
    tail = tail.split("\n", 1)[1].strip()
if tail != src:
    sys.exit("COULD NOT MEASURE: the copy of the page text inside "
             "OLLAMA-README.md has drifted from ollama-page.md (%d vs %d "
             "chars). Run ./sync-page-text.sh" % (len(tail), len(src)))
PY
fi

html="$(curl -fsS --max-time 30 "https://ollama.com/${MODEL}" 2>/dev/null)" || {
  echo "COULD NOT MEASURE: fetch of https://ollama.com/${MODEL} failed"; exit 3; }

printf '%s' "$html" | python3 -c '
import html as H, re, sys

page = sys.stdin.read()
src  = open(sys.argv[1], encoding="utf-8").read()

def textarea(page, tid):
    """Contents of <textarea id=tid>. The closing tag is written </textarea\n>,
    so match the tag name only and never the whole ">"-terminated token."""
    m = re.search(r"<textarea\b[^>]*\bid=\"" + tid + r"\"", page)
    if not m:
        return None
    start = page.index(">", m.end()) + 1
    try:
        end = page.index("</textarea", start)
    except ValueError:
        return None
    return H.unescape(page[start:end])

stored = textarea(page, "editor")
if stored is None:
    print("COULD NOT MEASURE: no edit box on the page; the layout changed")
    sys.exit(3)

def norm(s):
    return s.replace("\r\n", "\n").strip()

stored_n, src_n = norm(stored), norm(src)

# The description under the model name. It is what search results show, so an
# empty one or a bare URL is a real defect even when the readme is perfect.
summary = (textarea(page, "summary-textarea") or "").strip()
if not summary:
    desc = "EMPTY"
elif re.fullmatch(r"https?://\S+", summary):
    desc = "a bare URL, not a description"
else:
    desc = "set"
print(f"  description  {desc}: {summary[:70]!r}")

if len(stored_n) < 40:
    print("NO README: the page is holding no readme text")
    sys.exit(2)

if stored_n == src_n:
    print(f"  readme       {len(stored_n)} chars, byte-identical to ollama-page.md")
    print()
    print("OK: readme present, markdown intact, text current")
    sys.exit(0)

# It differs. Say how, because "stripped on paste" and "an older version" need
# opposite fixes and look identical from a distance.
marks = {"headings (## )": ("\n## ", src_n.count("\n## "), stored_n.count("\n## ")),
         "tables (|---)":  ("\n|---", src_n.count("\n|---"), stored_n.count("\n|---")),
         "bold (**)":      ("**",     src_n.count("**"),     stored_n.count("**")),
         "code fences":    ("\n```",  src_n.count("\n```"),  stored_n.count("\n```")),
         "bullets (- )":   ("\n- ",   src_n.count("\n- "),   stored_n.count("\n- "))}
for name, (_, want, got) in marks.items():
    print(f"  {name:16} page {got:3}   source expects {want:3}")
print(f"  readme           {len(stored_n)} chars, source {len(src_n)} chars")

lost = [n for n, (_, want, got) in marks.items() if want and not got]
print()
if lost:
    print("DEGRADED: the markdown was stripped on the way in - no " + ", ".join(lost))
    print("The page is holding plain text. That happens when the text was copied")
    print("from a RENDERED view; copy the raw file instead. See OLLAMA-README.md.")
else:
    def words(s):
        return re.findall(r"[a-z0-9]+", s.lower())
    drift = sorted(set(words(src_n)) - set(words(stored_n)))
    print(f"STALE: markdown is intact but the text is an older version "
          f"({len(drift)} words missing, e.g. {drift[:8]})")
sys.exit(1)
' "$SRC"
