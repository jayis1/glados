#!/usr/bin/env bash
# She answers out loud. Needs the full server layer, not just the Modelfile.
#
#   ./speak.sh "The test chamber is ready." out.wav
set -euo pipefail
HOST="${OLLAMA_HOST:-localhost:11434}"
PROMPT="${1:-The test chamber is ready.}"
OUT="${2:-glados.wav}"

resp=$(curl -s "http://$HOST/api/chat" -d "$(jq -n --arg p "$PROMPT" \
  '{model:"glados", stream:false, speak:true,
    messages:[{role:"user", content:$p}]}')")

echo "$resp" | jq -r .message.content
echo "$resp" | jq -r '.audio // empty' | base64 -d > "$OUT"

if [[ -s "$OUT" ]]; then
  echo "wrote $OUT ($(echo "$resp" | jq -r '.audio_ms // "?"') ms, voice $(echo "$resp" | jq -r '.audio_voice // "?"'))"
else
  # Kokoro failing sets audio_error on an otherwise normal reply: silent,
  # never mute. That is the designed behaviour, not a crash.
  rm -f "$OUT"
  echo "no audio: $(echo "$resp" | jq -r '.audio_error // "speak not enabled on this server"')" >&2
fi
