#!/usr/bin/env bash
# Publish her to the Ollama registry, so that anyone can do:
#
#     ollama run <namespace>/GLaDOS
#
# instead of cloning this repo first. Optional — the two-command install in
# the README needs none of this.
#
#   ./publish-to-ollama.sh <your-ollama.com-username> [published-name]
#
# ONE-TIME SETUP, which only you can do: the registry authenticates pushes with
# this machine's Ollama key, and a signed-in human has to authorise that key
# against the account. Modern Ollama does this with a single click — run the
# script and it prints the exact link. Older builds want the public key pasted
# at https://ollama.com/settings/keys instead; the script prints that too, so
# whichever your build does, the next step is on screen.
set -euo pipefail

SOURCE="${SOURCE:-glados}"      # the local tag to publish, as ./install.sh builds it
PUBLISHED="${2:-GLaDOS}"        # the name it goes out under; case is preserved

KEY=""
for candidate in "$HOME/.ollama/id_ed25519.pub" /usr/share/ollama/.ollama/id_ed25519.pub; do
  [[ -r "$candidate" ]] && { KEY="$candidate"; break; }
done

print_key() {
  if [[ -n "$KEY" ]]; then
    echo "This machine's Ollama public key ($KEY):"
    echo
    cat "$KEY"
    echo
    echo "A public key is safe to paste anywhere. The matching private key"
    echo "stays on this machine and is what actually signs the push."
  else
    echo "No Ollama key found yet. Run 'ollama list' once to generate one," >&2
    echo "then re-run this script." >&2
  fi
}

if [[ $# -lt 1 ]]; then
  cat <<'TXT'
Usage: ./publish-to-ollama.sh <your-ollama.com-username> [published-name]

One-time setup:

  1. Sign in (or sign up) at https://ollama.com — the username you pick there
     becomes the namespace people pull from.
  2. Run this script with your username. If the machine is not authorised yet,
     the push prints a https://ollama.com/connect?... link: open it while
     signed in and click once. That registers this machine as a named device,
     and you can revoke it later under Settings -> Keys.
  3. Re-run the same command. That is the whole flow.

TXT
  print_key
  exit 0
fi

USERNAME="$1"
TARGET="$USERNAME/$PUBLISHED"

command -v ollama >/dev/null || { echo "ollama not found." >&2; exit 1; }
ollama list | awk '{print $1}' | grep -qi "^$SOURCE" || {
  echo "Model '$SOURCE' does not exist locally yet. Run ./install.sh first." >&2
  exit 1
}

echo "tagging  $SOURCE -> $TARGET"
ollama cp "$SOURCE" "$TARGET"

echo "pushing  $TARGET (~4.7 GB on a first push; later pushes reuse blobs)"
# `ollama push` prints the connect link and still exits 0 when this machine is
# not authorised yet, so exit status alone cannot be read as "uploaded" — the
# output has to be inspected. Getting this wrong reports a publication that
# never happened, which is worse than failing loudly.
out="$(ollama push "$TARGET" 2>&1 | tee /dev/stderr)" || true

if grep -qi "ollama.com/connect" <<<"$out"; then
  cat <<TXT

Not published yet — this machine is not authorised for the $USERNAME account.

Open the https://ollama.com/connect?... link printed above while signed in as
'$USERNAME', click once, then re-run:

    ./publish-to-ollama.sh $USERNAME $PUBLISHED

TXT
  print_key
  exit 1
fi

if grep -qiE "unauthorized|not authorized|403|denied" <<<"$out"; then
  echo >&2
  echo "Push refused. Check that '$USERNAME' is the account this machine is" >&2
  echo "authorised against, then re-run." >&2
  print_key >&2
  exit 1
fi

# Trust the registry rather than our own stdout: ask it whether the manifest
# actually landed.
if curl -fsS -o /dev/null "https://registry.ollama.ai/v2/$USERNAME/$PUBLISHED/manifests/latest" 2>/dev/null; then
  cat <<TXT

Done, and verified against the registry. Anyone can now run her with:

    ollama run $TARGET

TXT
else
  echo >&2
  echo "Push reported no error, but the registry does not serve a manifest for" >&2
  echo "$TARGET yet. Give it a moment, then check https://ollama.com/$TARGET" >&2
  exit 1
fi
