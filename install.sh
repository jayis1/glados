#!/usr/bin/env bash
# Install GLaDOS as a local Ollama model.
#
#   ./install.sh [model-name]     (default: glados)
#
# This installs the persona layer only — the part that needs nothing but
# Ollama. The fly brain that gives her moods is in ../fly and has its own,
# considerably more demanding, setup. See docs/REPRODUCE.md.
set -euo pipefail

NAME="${1:-glados}"
BASE="qwen2.5:7b-instruct"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODELFILE="${MODELFILE:-$HERE/Modelfile}"

command -v ollama >/dev/null 2>&1 || {
  echo "ollama not found. Install it from https://ollama.com and try again." >&2
  exit 1
}

ollama list >/dev/null 2>&1 || {
  echo "ollama is installed but not answering. Start it with 'ollama serve'." >&2
  exit 1
}

# `FROM` a tag that is already local costs no extra disk — Ollama points the
# new manifest at the same blobs. Pull it first so that stays true.
if ollama list | awk '{print $1}' | grep -qx "$BASE"; then
  echo "base model $BASE already present"
else
  echo "pulling $BASE (~4.7 GB, once)"
  ollama pull "$BASE"
fi

echo "creating $NAME from $(basename "$MODELFILE")"
ollama create "$NAME" -f "$MODELFILE"

echo
echo "Asking her to confirm she is awake..."
echo
ollama run "$NAME" "A test subject has arrived."
echo
echo "She is installed as '$NAME'. Talk to her with:  ollama run $NAME"
echo
echo "Removing her later cannot harm the base model:  ollama rm $NAME"
