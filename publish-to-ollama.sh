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

# DO NOT publish the local tag blindly. The deployed `glados` pins device
# placement to the host it runs on, and those parameters travel inside the
# manifest: the first upload of jais/GLaDOS shipped `num_gpu 0` and
# `num_thread 10` to the whole internet, which silently forced CPU-only
# inference on every puller's machine, however good their GPU was. The symptom
# is "it works, but slowly", i.e. no symptom at all.
#
# So: if the source tag carries placement, build the published tag from the
# portable ./Modelfile instead of copying it. Rebuild rather than warn, for the
# same reason byom.sh renames rather than warns — a printed caution scrolls past
# and the bad artifact still ships.
PLACEMENT_RE='^(num_gpu|num_thread|main_gpu|low_vram|num_batch)[[:space:]]'
offenders="$(ollama show "$SOURCE" --parameters 2>/dev/null \
             | grep -E "$PLACEMENT_RE" | awk '{print $1}' | paste -sd, -)"

if [[ -n "$offenders" ]]; then
  if [[ -r Modelfile ]]; then
    echo "note     '$SOURCE' pins host-specific placement ($offenders)"
    echo "building $TARGET from ./Modelfile instead of copying '$SOURCE'"
    ollama create "$TARGET" -f Modelfile
  else
    echo "'$SOURCE' pins host-specific placement ($offenders) and ./Modelfile" >&2
    echo "is not readable, so there is no portable build to publish instead." >&2
    echo "Run this from a checkout of the repository." >&2
    exit 1
  fi
else
  echo "tagging  $SOURCE -> $TARGET"
  ollama cp "$SOURCE" "$TARGET"
fi

# Belt and braces: whatever route we took, the thing about to be uploaded must
# not carry placement.
still="$(ollama show "$TARGET" --parameters 2>/dev/null \
         | grep -E "$PLACEMENT_RE" | awk '{print $1}' | paste -sd, -)"
if [[ -n "$still" ]]; then
  echo "refusing to push: $TARGET still pins $still" >&2
  exit 1
fi

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
MANIFEST="$(curl -fsS "https://registry.ollama.ai/v2/$USERNAME/$PUBLISHED/manifests/latest" 2>/dev/null || true)"
if [[ -n "$MANIFEST" ]]; then
  # And read back the parameters the registry will hand to strangers, rather
  # than the ones we believe we uploaded. This is the check that would have
  # caught the num_gpu 0 publication.
  blob="$(printf '%s' "$MANIFEST" | python3 -c '
import json, sys
try:
    m = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for l in m.get("layers", []):
    if l.get("mediaType", "").endswith("params"):
        print(l["digest"])
        break
')"
  if [[ -n "$blob" ]]; then
    served="$(curl -fsSL "https://registry.ollama.ai/v2/$USERNAME/$PUBLISHED/blobs/$blob" 2>/dev/null || true)"
    echo
    echo "registry serves these parameters: $served"
    if grep -qE '"(num_gpu|num_thread|main_gpu|low_vram)"' <<<"$served"; then
      echo >&2
      echo "WARNING: the published model pins device placement. Every puller" >&2
      echo "inherits it. Rebuild from ./Modelfile and push again." >&2
      exit 1
    fi
  fi
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
