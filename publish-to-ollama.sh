#!/usr/bin/env bash
# Publish her to the Ollama registry, so that anyone can do:
#
#     ollama run <namespace>/glados
#
# instead of cloning this repo first. Optional — the two-command install in
# the README needs none of this.
#
#   ./publish-to-ollama.sh <your-ollama.com-username>
#
# ONE-TIME SETUP, which only you can do: the registry authenticates pushes
# with your machine's Ollama public key, and that key has to be registered to
# your ollama.com account by hand. Run this script with no arguments and it
# will print the exact key to paste.
set -euo pipefail

NAME="${NAME:-glados}"
KEY=""
for candidate in "$HOME/.ollama/id_ed25519.pub" /usr/share/ollama/.ollama/id_ed25519.pub; do
  [[ -r "$candidate" ]] && { KEY="$candidate"; break; }
done

if [[ $# -lt 1 ]]; then
  cat <<'TXT'
Usage: ./publish-to-ollama.sh <your-ollama.com-username>

One-time setup:

  1. Sign in (or sign up) at https://ollama.com — the username you pick there
     becomes the namespace people pull from.
  2. Open https://ollama.com/settings/keys
  3. Add the public key printed below.

TXT
  if [[ -n "$KEY" ]]; then
    echo "Your Ollama public key ($KEY):"
    echo
    cat "$KEY"
    echo
    echo "A public key is safe to paste anywhere. The matching private key"
    echo "stays on this machine and is what actually signs the push."
  else
    echo "No Ollama key found yet. Run 'ollama list' once to generate one," >&2
    echo "then re-run this script." >&2
  fi
  exit 0
fi

USERNAME="$1"
TARGET="$USERNAME/$NAME"

command -v ollama >/dev/null || { echo "ollama not found." >&2; exit 1; }
ollama list | awk '{print $1}' | grep -q "^$NAME" || {
  echo "Model '$NAME' does not exist locally yet. Run ./install.sh first." >&2
  exit 1
}

echo "tagging  $NAME -> $TARGET"
ollama cp "$NAME" "$TARGET"

echo "pushing  $TARGET (~4.7 GB on a first push; subsequent pushes reuse blobs)"
if ollama push "$TARGET"; then
  echo
  echo "Done. Anyone can now run her with:"
  echo
  echo "    ollama run $TARGET"
else
  rc=$?
  echo
  echo "Push failed (exit $rc). The usual cause is the one-time key step:" >&2
  echo "run this script with no arguments to print the key to register at" >&2
  echo "https://ollama.com/settings/keys — then try again." >&2
  exit $rc
fi
