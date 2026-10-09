#!/usr/bin/env bash
# Re-append ollama-page.md to the end of OLLAMA-README.md, below a `---`.
#
# Why a second copy exists at all: the first console one-liner handed out for
# this page took the readme text from OLLAMA-README.md and split it at the first
# `\n---\n`. That separator was later removed, so the line silently started
# posting an empty readme while still reporting success. The line itself lives
# in an issue comment and cannot be edited now - but what it FETCHES can be, so
# the separator comes back with the real page text under it. A stale instruction
# that does the right thing beats a warning nobody reads.
#
# The cost is a duplicate, so check-ollama-page.sh refuses to pass while the two
# disagree, and this script is the one way to resolve that.
set -euo pipefail
cd "$(dirname "$0")"

MARK='<!-- the page text, verbatim, for the stale one-liner -->'

[ -f ollama-page.md ] || { echo "ollama-page.md is missing"; exit 1; }

# The appended copy must contain no horizontal rule of its own, or the stale
# line's split would cut the page text in half.
if grep -qxE -- '(---|\*\*\*|___)' ollama-page.md; then
  echo "ollama-page.md contains a horizontal rule; the stale one-liner would" \
       "split the page text at it. Use a heading instead." >&2
  exit 1
fi

python3 - "$MARK" <<'PY'
import sys
mark = sys.argv[1]
readme = open("OLLAMA-README.md", encoding="utf-8").read()
page = open("ollama-page.md", encoding="utf-8").read().strip()
head = readme.split("\n---\n" + mark, 1)[0].rstrip("\n")
with open("OLLAMA-README.md", "w", encoding="utf-8") as fh:
    fh.write(head + "\n\n---\n" + mark + "\n" + page + "\n")
PY

echo "OLLAMA-README.md tail synced from ollama-page.md"
