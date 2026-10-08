#!/usr/bin/env bash
# Talk to her. The plainest possible call: she is just a model.
#
#   ./chat.sh "Someone is at the front door."
set -euo pipefail
HOST="${OLLAMA_HOST:-localhost:11434}"
MODEL="${MODEL:-glados}"
PROMPT="${1:-Someone is at the front door.}"

curl -s "http://$HOST/api/chat" -d "$(jq -n --arg m "$MODEL" --arg p "$PROMPT" \
  '{model:$m, stream:false, messages:[{role:"user", content:$p}]}')" \
  | jq -r .message.content
