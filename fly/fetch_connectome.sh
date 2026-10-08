#!/usr/bin/env bash
# Fetch the Janelia MaleCNS v1.0 flat connectome.
#
# ~565 MB over three files, anonymous, no account and no API key. The data is
# CC-BY 4.0 (Janelia FlyEM + Google Research), which is why this script
# downloads it rather than this repository redistributing it.
#
#   ./fetch_connectome.sh [target-dir]      (default: ./data)
#
# Why these three files and not just the weights table:
#
#   weights            the graph. The *traced-only* variant, deliberately: the
#                      full minconf-0.5 table (1.1 GB) also carries untraced
#                      fragments, which are partial reconstructions and add
#                      noise with no signal.
#   neurotransmitters  the weights table is UNSIGNED. Without a per-neuron
#                      transmitter you cannot tell excitation from inhibition,
#                      every synapse ends up excitatory, and the network
#                      saturates. That is the one way to build something that
#                      runs and is completely worthless. Not optional.
#   annotations        names the populations, so a doorbell can be injected
#                      into real mechanosensory neurons rather than into an
#                      arbitrary row index.
set -euo pipefail

BASE="${FLYEM_BASE_URL:-https://storage.googleapis.com/flyem-male-cns/v1.0/connectome-data/flat-connectome}"
DEST="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/data}"

# name:expected-bytes — verified against the live bucket 2026-10-08.
FILES=(
  "connectome-weights-male-cns-v1.0-minconf-0.5-traced-only.feather:508025642"
  "body-neurotransmitters-male-cns-v1.0.feather:43282834"
  "body-annotations-male-cns-v1.0-minconf-0.5.feather:14483314"
)

mkdir -p "$DEST"
echo "destination: $DEST"
echo "source:      $BASE"
echo

for entry in "${FILES[@]}"; do
  name="${entry%%:*}"
  want="${entry##*:}"
  path="$DEST/$name"

  if [[ -f "$path" ]]; then
    have=$(stat -c%s "$path" 2>/dev/null || stat -f%z "$path")
    if [[ "$have" == "$want" ]]; then
      echo "have  $name ($have bytes)"
      continue
    fi
    echo "resuming $name (have $have of $want)"
  fi

  echo "get   $name ($want bytes)"
  curl -fL --retry 3 --retry-delay 2 -C - -o "$path" "$BASE/$name"

  have=$(stat -c%s "$path" 2>/dev/null || stat -f%z "$path")
  if [[ "$have" != "$want" ]]; then
    echo "FAIL  $name: got $have bytes, expected $want" >&2
    echo "      A short file here does not crash the build — it silently" >&2
    echo "      produces a smaller, wrong brain. Refusing to continue." >&2
    exit 1
  fi
  echo "ok    $name"
done

echo
echo "All three files present and the right size."
echo "Next:  source env.sh  &&  python3 build_csr.py"
