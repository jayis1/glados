#!/usr/bin/env bash
# What is the fly doing right now?
#
# Remember: agitation is INVERTED. 1.0 is undisturbed and coiled, 0.0 is
# maximally driven. A resting 0.85 means she is calm.
set -euo pipefail
MOODD="${MOODD:-localhost:9099}"

curl -s "http://$MOODD/mood" | jq '{
  arousal, novelty, valence, reinforcement,
  agitation: (.agitation | tostring + "  (inverted: 1.0 = calm)")
}'
