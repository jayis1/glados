#!/usr/bin/env python3
"""Layer 2 of the fly-centred GLaDOS: mood coupling (Paperclip IST-270).

This is the seam that answers "can we build our own model with the fruit fly
brain at the center?". The honest version of that is an inversion of what we
have, not a new language model:

  layer 1  paperclip-moodd :9099   164,587-neuron LIF connectome on two T400s,
                                   five calibrated axes.  Holds her state
                                   CONTINUOUSLY - membrane potentials persist
                                   between requests and never reset.
  layer 2  this file                mood vector -> her system clause + sampling
  layer 3  paperclip-gladosd        qwen2.5:7b-instruct, a stateless renderer

Before this, the LLM *was* GLaDOS and the fly was a decoration. After it, the
fly holds who she is and the 7B's only job is putting that state into one
English sentence. The connectome cannot produce the words - there is no
language circuit in a fly, the weights are synapse counts rather than learned
parameters, and the sim runs at 0.21x wall clock - so language stays with qwen.
See the IST-270 plan addendum.

Three constraints shape every line here:

  1. FAIL OPEN, ALWAYS.  GLaDOS was mute for days because one host went down.
     The fly must never be able to do that again.  moodd 503, slow, absent,
     unauthorised, serving a stale snapshot, or raising something unforeseen
     all mean "proceed with exactly the bytes n8n sent".  Every path through
     this module is wrapped, and the caller treats a {} note as "unchanged".

  2. APPEND, NEVER EDIT.  The user's IST-266 ruling is that GLaDOS stays pure
     Portal and the 22 live n8n system prompts stay byte-for-byte as they are.
     So we add one `\nCurrent state: <phrase>.` clause to the END of the system
     message and touch nothing else.  n8n keeps POSTing identical bytes; the
     mood is applied on this side of the wire, so no workflow is edited.

  3. INSIDE THE NODE'S OWN BAND.  The live nodes choose temperature 0.2-0.9.
     Arousal may only raise temperature, by at most `mood_temp_headroom`, never
     past `mood_temp_max`, and never above a value the node did not pick for
     itself.  A node that chose 0.2 chose determinism and keeps its floor.

Reading the axes (from mood_calibration.json, and MOOD.md section 3):

  arousal        whole-network rate, log-scaled.  rest ~0.00
  novelty        kenyon_cell (mushroom body input).  rest ~0.04
  valence        MBON, SIGNED about rest.  0.0 = no opinion, not minimum
  reinforcement  DAN (dopaminergic).  rest ~0.02
  agitation      escape_GF, and it is INVERTED: no stimulus raises the giant
                 fibre, mechanosensory drive halves it.  1.0 = undisturbed and
                 coiled, 0.0 = maximally driven.  Rest reads ~0.85.  Read it as
                 startle readiness that is SPENT by being touched - measured,
                 mechanosensory drive takes it 0.889 -> 0.233.  Treating a
                 resting 0.85 as "agitated" would invert her entirely, which is
                 the single easiest mistake to make against this service.
"""

import json
import os
import threading
import time
from http.client import HTTPConnection

# ---------------------------------------------------------------------------
# The phrase table.
#
# Deliberately small, fixed and ordered: first match wins. These are states a
# building can be in, phrased as GLaDOS would note them to herself - <=6 words,
# no emoji, no meta, no first person, so the clause reads as a continuation of
# the existing persona prompt rather than a second instruction fighting it.
#
# Thresholds are set against measured rest (arousal 0.00, novelty 0.04,
# valence 0.00, reinforcement 0.02, agitation 0.85) rather than against 0.5 by
# default, because every excitatory axis sits near the floor at rest and only a
# real stimulus moves it. See probe_moodd.py's sweep for what each drive level
# actually produces.
# ---------------------------------------------------------------------------
STATES = (
    # name,            phrase,                              test
    ("disturbed",      "recently disturbed, patience thinning",
     lambda m: m["agitation"] < 0.45),
    ("alert",          "something moving in the building",
     lambda m: m["arousal"] >= 0.50),
    ("curious",        "an unfamiliar pattern, briefly interesting",
     lambda m: m["novelty"] >= 0.50),
    ("rewarded",       "things going suspiciously well",
     lambda m: m["reinforcement"] >= 0.50),
    ("displeased",     "mildly displeased",
     lambda m: m["valence"] <= -0.25),
    ("pleased",        "quietly pleased with herself",
     lambda m: m["valence"] >= 0.40),
    ("stirring",       "something stirring, barely worth noting",
     lambda m: m["arousal"] >= 0.20 or m["novelty"] >= 0.20
     or m["reinforcement"] >= 0.20 or m["agitation"] < 0.70),
    ("idle",           "idle, undisturbed, patient",
     lambda m: True),
)

AXES = ("arousal", "novelty", "valence", "reinforcement", "agitation")

# ---------------------------------------------------------------------------
# Measured achievable span, and why the raw vector cannot be used directly.
#
# The STATES thresholds above read like fractions of a 0-1 axis, and the axes
# ARE normalised to 0-1 - but against the calibration's lo/hi, which is the
# range the network can reach when a sensory site is driven to saturation
# directly. Through the only sense that is actually wired up today (request
# traffic -> hearing_JO; see afferent.py), the reachable range is much smaller:
#
#   drive   0     0.02    0.05    0.1     0.2     0.4
#   arousal 0.000 0.004   0.018   0.057   0.106   0.188
#
# measurements/hearing.json, 5 snapshots per level on the live calibrated
# service. So raw arousal tops out near 0.19 and every threshold at 0.20 or
# above is unreachable: she would read `idle` forever and the coupling would be
# exactly the theatre the plan warned about. Rescaling against the measured
# ceiling is what makes the state line mean something.
#
# Only arousal is listed, deliberately. It is the one axis with a clean
# monotonic ladder (40 sd at the top level). novelty, reinforcement and valence
# move under hearing_JO too, but weakly and non-monotonically - valence drifts
# NEGATIVE under sustained traffic, which is a real and very GLaDOS finding but
# a span of ~0.015 is too close to its 0.0021 noise floor to scale against. The
# axis that would drive them properly is mechanosensory (reach 9.4e-2 into
# escape_GF against hearing's 2.4e-3), and that needs the Home Assistant token.
# When it lands, re-run probe_hearing.py --site mechanosensory and add rows.
#
# An axis with no row here is passed through unscaled.
AXIS_SPAN = {"arousal": (0.0, 0.19)}

_cache_lock = threading.Lock()
_cache = {"at": 0.0, "mood": None, "age_s": None, "error": "never_fetched"}
_token_cache = {"path": None, "token": None}


def _token(path):
    """Read the moodd bearer once and keep it; it is a 0600 file on this host."""
    if _token_cache["path"] == path and _token_cache["token"]:
        return _token_cache["token"]
    try:
        with open(path, "r", encoding="utf-8") as fh:
            tok = fh.read().strip() or None
    except OSError:
        tok = None
    _token_cache["path"], _token_cache["token"] = path, tok
    return tok


def fetch_mood(cfg):
    """GET :9099/mood, cached. Returns (mood_dict_or_None, meta).

    Never raises. `meta` always carries why there is no mood, so a request that
    went out uncoupled says so in the event log instead of looking identical to
    one that was never meant to be coupled.
    """
    now = time.time()
    ttl = float(cfg.get("mood_cache_ms", 1000)) / 1000.0
    with _cache_lock:
        if _cache["mood"] is not None and (now - _cache["at"]) < ttl:
            return _cache["mood"], {"mood_source": "cache",
                                    "mood_age_s": _cache["age_s"]}
        if _cache["mood"] is None and _cache["error"] and (now - _cache["at"]) < ttl:
            # Negative caching too: if moodd is down, one failed connect per
            # second is plenty. Without this, a moodd that is refusing
            # connections slowly would add its timeout to EVERY request.
            return None, {"mood_error": _cache["error"], "mood_source": "cache"}

    mood, err, age = None, None, None
    try:
        conn = HTTPConnection(cfg.get("mood_url", "127.0.0.1:9099"),
                              timeout=float(cfg.get("mood_timeout_s", 0.75)))
        headers = {}
        tok = _token(cfg.get("mood_token_path", "/opt/paperclip-glados/token"))
        if tok:
            headers["Authorization"] = "Bearer " + tok
        conn.request("GET", "/mood", headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        if resp.status != 200:
            err = "moodd_http_%d" % resp.status
        else:
            out = json.loads(raw or b"{}")
            vec = out.get("mood") or {}
            missing = [a for a in AXES if not isinstance(vec.get(a), (int, float))]
            if missing:
                err = "moodd_missing_axes:" + ",".join(missing)
            else:
                age = out.get("age_s")
                max_age = cfg.get("mood_max_age_s", 10)
                if max_age is not None and isinstance(age, (int, float)) \
                        and age > float(max_age):
                    # A frozen snapshot is a mood from whenever the sim thread
                    # died. moodd 503s on a dead thread, but it can only do
                    # that once it notices; this is the consumer-side guard.
                    err = "moodd_stale:%.1fs" % age
                else:
                    mood = {a: float(vec[a]) for a in AXES}
    except Exception as exc:  # noqa: BLE001 - coupling must never raise upward
        err = "%s: %s" % (type(exc).__name__, exc)

    with _cache_lock:
        _cache.update(at=time.time(), mood=mood, age_s=age, error=err)
    if mood is None:
        return None, {"mood_error": err, "mood_source": "live"}
    return mood, {"mood_source": "live", "mood_age_s": age}


def rescale(vec, cfg):
    """Raw axes -> axes scaled against their measured achievable span.

    Monotonic and clipped to [0, 1], so a synthetic vector at 1.0 stays at 1.0
    and the state table keeps reading as fractions of "as far as this sense can
    push her". Axes with no configured span pass through untouched.
    """
    spans = cfg.get("mood_axis_span")
    spans = AXIS_SPAN if spans is None else spans
    out = dict(vec)
    for axis, pair in (spans or {}).items():
        try:
            lo, hi = float(pair[0]), float(pair[1])
            raw = float(vec[axis])
        except (TypeError, ValueError, KeyError, IndexError):
            continue
        if hi <= lo:
            continue
        out[axis] = max(0.0, min(1.0, (raw - lo) / (hi - lo)))
    return out


def model_coupled(model, cfg):
    """Does this model get the fly, or the bytes n8n sent?

    This is what lets both answers be true at once. `glados` is the fly-centred
    model and is coupled; `qwen2.5:7b-instruct` - the tag all 22 live n8n nodes
    name - is NOT, so the re-point on IST-271 lands a byte-identical host swap
    and the "no behaviour change" acceptance from IST-266 holds. Pointing any
    single node at the fly later is then a one-word edit to that node, and
    reversible by the same edit.

    An empty prefix list means "couple nothing", not "couple everything": the
    failure mode of a mis-typed config should be today's behaviour.

    The prefix is matched against the *bare* model name - everything after the
    last `/` - because a registry pull keeps its namespace: `ollama pull
    jais/GLaDOS` lands locally as `jais/GLaDOS:latest`, and a Hugging Face GGUF
    as `hf.co/user/repo:Q4`. Matching the literal string would uncouple the
    published model for every single person who pulls it, and the symptom is a
    GLaDOS who still answers in character and merely never has a mood again -
    the hardest kind of failure to notice here.

    Stripping is the conservative direction, not the broad one. Matching the
    whole string would also couple `glados/qwen2.5:7b-instruct` - the n8n tag,
    sitting in a namespace that happens to be spelled `glados` - which IST-280
    forbids outright. Every tag coupled today is bare (`glados`, `glados:latest`,
    `glados-llama3.1`), so for those this is the same comparison it always was.
    The tag is deliberately NOT stripped: `glados:v2` still matches `glados`.
    """
    prefixes = cfg.get("mood_models")
    if prefixes is None:
        prefixes = ("glados",)
    bare = (model or "").lower().rsplit("/", 1)[-1]
    return any(bare.startswith(str(p).lower()) for p in prefixes)


def classify(mood, cfg):
    """Vector -> (state_name, phrase). First matching row of STATES wins."""
    overrides = cfg.get("mood_phrases") or {}
    for name, phrase, test in STATES:
        try:
            hit = bool(test(mood))
        except Exception:  # noqa: BLE001 - a bad axis must not mute her
            hit = False
        if hit:
            return name, str(overrides.get(name, phrase))
    return "idle", str(overrides.get("idle", "idle, undisturbed, patient"))


def couple_temperature(mood, opts, cfg):
    """arousal -> temperature, strictly upward and strictly inside the band.

    Returns (new_temperature_or_None, before). None means "leave it alone",
    which is what happens when the node did not set a temperature at all - we
    do not invent sampling for a node that chose the backend default.
    """
    before = opts.get("temperature")
    if not isinstance(before, (int, float)) or isinstance(before, bool):
        return None, before
    before = float(before)
    headroom = float(cfg.get("mood_temp_headroom", 0.3))
    ceiling = float(cfg.get("mood_temp_max", 0.9))
    if before >= ceiling:
        return None, before
    span = min(headroom, ceiling - before)
    arousal = max(0.0, min(1.0, float(mood["arousal"])))
    after = round(before + arousal * span, 3)
    if after <= before:
        return None, before
    return after, before


def _append_clause(body, phrase):
    """Append one state clause to the system message. Never edits what is there.

    Returns the key we touched, or None if there was no system message to
    append to - in which case we leave the body alone rather than inventing
    one. /api/generate nodes that pass no `system` keep their exact prompt.

    An EMPTY system string counts as "none", and that distinction is not
    pedantry - it is a measured bug this guard exists to stop. `ollama run
    <tag>` sends /api/generate with `"system": ""`, meaning "I have no prompt
    of my own, use the model's". A request-level system prompt OVERRIDES the
    Modelfile's at the backend, so appending to "" turned her whole persona
    into the four words of the clause. Measured on a probe model whose SYSTEM
    was "always answer with exactly the single word BANANA": direct to the
    backend, "BANANA"; through a coupling that appended to the empty string,
    "The capital of France is Paris." The persona was gone and the only
    symptom was a GLaDOS who answered helpfully.

    So a terminal `ollama run` gets her persona and no state clause, which is
    the right way round: a mood is an addition to who she is, never a
    replacement for it. Clients that send their own system message - every
    n8n node, every OpenAI-compatible SDK - are unaffected.
    """
    clause = "\nCurrent state: %s." % phrase
    msgs = body.get("messages")
    if isinstance(msgs, list):
        for i in range(len(msgs) - 1, -1, -1):
            m = msgs[i]
            if isinstance(m, dict) and m.get("role") == "system" \
                    and isinstance(m.get("content"), str) and m["content"].strip():
                m["content"] = m["content"] + clause
                return "messages[%d].content" % i
        return None
    sys_prompt = body.get("system")
    if isinstance(sys_prompt, str) and sys_prompt.strip():
        body["system"] = sys_prompt + clause
        return "system"
    return None


def apply_mood_policy(body, cfg, vision=False):
    """Couple the fly's state into one inference body. Returns a log note.

    An empty note means the body is untouched - which is the correct and safe
    outcome for: coupling disabled, a vision request, moodd unavailable, or a
    body with no system message to append to.
    """
    try:
        if not cfg.get("mood_coupling"):
            return {}
        if vision:
            # moondream is asked to describe a doorbell photo. A mood clause
            # there would corrupt a factual caption, and the caption is then
            # fed to a text node that IS coupled.
            return {"mood_applied": False, "mood_skipped": "vision_route"}
        if not model_coupled(body.get("model"), cfg):
            # The whole point of the split: n8n's tag goes through untouched.
            return {"mood_applied": False, "mood_skipped": "model_not_coupled",
                    "model": body.get("model")}

        raw, meta = fetch_mood(cfg)
        if raw is None:
            return dict(mood_applied=False, mood_skipped="unavailable", **meta)
        mood = rescale(raw, cfg)

        state, phrase = classify(mood, cfg)
        where = _append_clause(body, phrase)
        if where is None:
            return dict(mood_applied=False, mood_skipped="no_system_message",
                        mood_state=state, mood=mood, **meta)

        # Both vectors in the note: `mood` is what the thresholds saw, and
        # `mood_raw` is what the network actually published. With only one of
        # them an odd reply is untraceable - you cannot tell a network that
        # moved from a rescaling that exaggerated.
        note = dict(mood_applied=True, mood_state=state, mood_phrase=phrase,
                    mood_clause_at=where,
                    mood={k: round(v, 4) for k, v in mood.items()},
                    mood_raw={k: round(v, 4) for k, v in raw.items()}, **meta)

        opts = dict(body.get("options") or {})
        after, before = couple_temperature(mood, opts, cfg)
        if after is not None:
            opts["temperature"] = after
            body["options"] = opts
            note["temperature_before"] = before
            note["temperature"] = after
        elif before is not None:
            note["temperature"] = before
        return note
    except Exception as exc:  # noqa: BLE001 - the last line of fail-open
        return {"mood_applied": False, "mood_skipped": "policy_error",
                "mood_error": "%s: %s" % (type(exc).__name__, exc)}


def health(cfg):
    """The mood block for gladosd's /health, so coupling is inspectable live."""
    out = {"coupling": bool(cfg.get("mood_coupling")),
           "url": cfg.get("mood_url", "127.0.0.1:9099"),
           "coupled_models": cfg.get("mood_models", ["glados"]),
           "temp_headroom": cfg.get("mood_temp_headroom", 0.3),
           "temp_max": cfg.get("mood_temp_max", 0.9)}
    if not cfg.get("mood_coupling"):
        return out
    raw, meta = fetch_mood(cfg)
    out.update(meta)
    if raw:
        vec = rescale(raw, cfg)
        state, phrase = classify(vec, cfg)
        out.update(mood={k: round(v, 4) for k, v in vec.items()},
                   mood_raw={k: round(v, 4) for k, v in raw.items()},
                   state=state, phrase=phrase)
    return out
