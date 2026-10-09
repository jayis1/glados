#!/usr/bin/env bash
# Is the readme on https://ollama.com/jais/GLaDOS, and is it the right text?
#
# Five outcomes, not two. A checker that can only say "good" or "bad" will say
# "bad" when it simply failed to look, and that is how a page gets re-pasted for
# no reason. Exit 0 = both fields right, 1 = readme on the page but wrong,
# 2 = no readme, 3 = could not measure, 4 = readme right, description not.
#
# 4 is its own outcome rather than folded into 1 because the two fields are
# written by separate actions. "Re-paste the readme" and "set the description"
# are different jobs, and reporting a correct readme as wrong is how you get
# someone to redo work that already succeeded.
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
DESC="$(dirname "$0")/ollama-description.txt"

[ -f "$SRC" ]  || { echo "COULD NOT MEASURE: $SRC not found";  exit 3; }
[ -f "$DESC" ] || { echo "COULD NOT MEASURE: $DESC not found"; exit 3; }

# ollama.com caps the description at 255 characters. A source file over the cap
# can never match what the page stores, so this would otherwise report
# INCOMPLETE forever and blame the human for not pasting it.
desc_len=$(python3 -c 'import sys;print(len(open(sys.argv[1],encoding="utf-8").read().strip()))' "$DESC")
if [ "$desc_len" -gt 255 ] || [ "$desc_len" -lt 40 ]; then
  echo "COULD NOT MEASURE: ollama-description.txt is ${desc_len} characters;" \
       "ollama.com allows 40-255, so it could never match the page."
  exit 3
fi

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

# The console routes used to bake the description into the one-liner itself.
# Two copies of the same string is how the readme got out of step in the first
# place, so they now fetch ollama-description.txt and this refuses to pass if a
# literal creeps back - otherwise the page could hold a perfectly good
# description that this checker reports as wrong, forever.
import re
baked = re.findall(r"summary:'[^']*'", howto)
if baked:
    sys.exit("COULD NOT MEASURE: %d one-liner(s) in OLLAMA-README.md hardcode a "
             "description instead of fetching ollama-description.txt, so the two "
             "can drift. Replace the literal with summary:D." % len(baked))
PY
fi

html="$(curl -fsS --max-time 30 "https://ollama.com/${MODEL}" 2>/dev/null)" || {
  echo "COULD NOT MEASURE: fetch of https://ollama.com/${MODEL} failed"; exit 3; }

printf '%s' "$html" | python3 -c '
import html as H, re, sys

page = sys.stdin.read()
src  = open(sys.argv[1], encoding="utf-8").read()
want_desc = open(sys.argv[2], encoding="utf-8").read().strip()

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
elif summary == want_desc:
    desc = "matches ollama-description.txt"
else:
    desc = "set, but not the text in ollama-description.txt"
desc_ok = summary == want_desc
print(f"  description  {desc}: {summary[:70]!r}")

if len(stored_n) < 40:
    print("NO README: the page is holding no readme text")
    sys.exit(2)

if stored_n == src_n:
    print(f"  readme       {len(stored_n)} chars, byte-identical to ollama-page.md")
    print()
    if desc_ok:
        print("OK: readme and description both present and current")
        sys.exit(0)
    print("INCOMPLETE: the readme is right; the description is not.")
    print("The description is the one line ollama.com shows in search results,")
    print("so it is read far more often than the readme. Set it with the Edit")
    print("link beside the model name - the text is in ollama-description.txt.")
    sys.exit(4)

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
' "$SRC" "$DESC"
