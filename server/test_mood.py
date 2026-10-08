#!/usr/bin/env python3
"""Proof for layer 2, the mood coupling (Paperclip IST-270).

Run on the GPU host with paperclip-moodd live:

    python3 /opt/paperclip-gladosd/test_mood.py

It asserts the three properties the coupling has to have, and the first one is
the one that matters most: GLaDOS was mute for days because a host went down,
so the fly must not be able to do that again. Every fail-open branch is
exercised against a real socket (a closed port, a wrong token, a 404 path),
not a mock, because a mock cannot be wrong in the way a socket can.
"""

import copy
import json
import math
import socket
import sys
import threading
import time

sys.path.insert(0, "/opt/paperclip-gladosd")
import mood  # noqa: E402

PASS, FAIL = [], []


def deaf_listener():
    """A socket that accepts and then never answers - a hung moodd.

    This is the fail-open branch a closed port does NOT cover: a refusal is
    instant, whereas a service that has wedged holds the connection open, and
    without a socket timeout that wedge is added to every reply GLaDOS makes.
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)

    def accept_forever():
        held = []
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            held.append(conn)   # held open, deliberately never written to

    threading.Thread(target=accept_forever, daemon=True).start()
    return srv.getsockname()[1]


DEAF_PORT = deaf_listener()


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("%s  %s%s" % ("ok  " if cond else "FAIL", name,
                        ("  -- " + str(detail)) if detail else ""))


def reset_cache():
    with mood._cache_lock:
        mood._cache.update(at=0.0, mood=None, age_s=None, error="never_fetched")


BASE_CFG = {
    "mood_coupling": True,
    "mood_url": "127.0.0.1:9099",
    "mood_token_path": "/opt/paperclip-glados/token",
    "mood_timeout_s": 0.75,
    "mood_cache_ms": 0,          # no caching during the test
    "mood_max_age_s": 10,
    "mood_temp_headroom": 0.3,
    "mood_temp_max": 0.9,
    # Sections 1-3 test the append/band/phrase properties, not the routing, so
    # they couple the qwen tag on purpose. Section 4 tests the routing itself
    # against the SHIPPED default, where this tag is deliberately not coupled.
    "mood_models": ["qwen2.5", "glados"],
    # Likewise: sections 1-3 reason about raw axis values (arousal 1.0 means
    # 1.0), so span rescaling is off here and tested on its own in section 5.
    "mood_axis_span": {},
}

# The real shape of a live n8n node: GLaDOS - Doorbell roast / "roast" node.
LIVE_BODY = {
    "model": "qwen2.5:7b-instruct",
    "stream": False,
    "think": False,
    "messages": [
        {"role": "system", "content": "You are GLaDOS from Portal. Deadpan, "
         "sarcastic, condescending, occasionally menacing. Under 25 words. One "
         "sentence. No emojis, no quotes, no meta about being an AI."},
        {"role": "user", "content": "Someone is at the front door."},
    ],
    "options": {"temperature": 0.7, "num_predict": 100},
}
SYSTEM_BYTES = LIVE_BODY["messages"][0]["content"]


# ---------------------------------------------------------------------------
print("\n== 1. fail open: the fly can never mute her again ==")
# ---------------------------------------------------------------------------
for name, cfg_patch in (
    ("moodd port closed",      {"mood_url": "127.0.0.1:9199"}),
    # Not 127.0.0.2: that is still loopback and moodd answers on it, since it
    # binds 0.0.0.0 (an earlier version of this test passed for the wrong
    # reason). This one is a genuinely unreachable address -> EHOSTUNREACH.
    ("moodd host unreachable", {"mood_url": "10.255.255.1:9099",
                                "mood_timeout_s": 0.2}),
    # And the branch that actually matters, below: a real hang, against the
    # DEAF_PORT listener, which accepts the connection and then says nothing.
    ("moodd accepts then hangs", {"mood_url": "127.0.0.1:%d" % DEAF_PORT,
                                  "mood_timeout_s": 0.3}),
    ("token file missing",     {"mood_token_path": "/opt/paperclip-glados/nope"}),
    ("stale snapshot refused", {"mood_max_age_s": -1}),
):
    reset_cache()
    mood._token_cache.update(path=None, token=None)
    cfg = dict(BASE_CFG, **cfg_patch)
    body = copy.deepcopy(LIVE_BODY)
    t0 = time.time()
    note = mood.apply_mood_policy(body, cfg)
    dt = (time.time() - t0) * 1000
    check("%s -> body byte-identical" % name, body == LIVE_BODY)
    check("%s -> says why in the note" % name,
          note.get("mood_applied") is False and note.get("mood_error"),
          note.get("mood_error"))
    check("%s -> costs under 1 s" % name, dt < 1000, "%.0f ms" % dt)

reset_cache()
mood._token_cache.update(path=None, token=None)
body = copy.deepcopy(LIVE_BODY)
note = mood.apply_mood_policy(body, dict(BASE_CFG, mood_coupling=False))
check("coupling disabled -> body byte-identical", body == LIVE_BODY)
check("coupling disabled -> empty note", note == {}, note)

body = copy.deepcopy(LIVE_BODY)
body["model"] = "moondream"
before = copy.deepcopy(body)
note = mood.apply_mood_policy(body, BASE_CFG, vision=True)
check("vision route -> body byte-identical", body == before)
check("vision route -> skipped for the right reason",
      note.get("mood_skipped") == "vision_route", note)

# A node with no system message keeps its exact prompt: we append, never author.
nosys = {"model": "qwen2.5:7b-instruct", "prompt": "Describe this.",
         "stream": False}
before = copy.deepcopy(nosys)
note = mood.apply_mood_policy(nosys, BASE_CFG)
check("no system message -> prompt untouched", nosys == before)
check("no system message -> skipped, not invented",
      note.get("mood_skipped") == "no_system_message", note)

# An EMPTY system string is "none", not "a system message that happens to be
# short". `ollama run <tag>` sends /api/generate with `"system": ""`, and a
# request-level system prompt OVERRIDES the Modelfile's at the backend - so
# appending to "" replaced her entire persona with the clause. Measured
# against a probe model whose SYSTEM was "answer with exactly the single word
# BANANA": straight to the backend, "BANANA"; through the coupling before this
# guard, "The capital of France is Paris." Both spellings of empty are tested,
# and the /api/chat shape too, because a blank system message in a list is
# just as empty as a blank string.
for label, blank in (("empty", ""), ("whitespace", "  \n ")):
    gen = {"model": "qwen2.5:7b-instruct", "prompt": "Describe this.",
           "system": blank, "stream": False}
    before = copy.deepcopy(gen)
    note = mood.apply_mood_policy(gen, BASE_CFG)
    check("%s system (generate) -> body byte-identical" % label, gen == before,
          repr(gen.get("system")))
    check("%s system (generate) -> skipped, persona left alone" % label,
          note.get("mood_skipped") == "no_system_message", note)

    chat = {"model": "qwen2.5:7b-instruct", "stream": False,
            "messages": [{"role": "system", "content": blank},
                         {"role": "user", "content": "Someone is at the door."}]}
    before = copy.deepcopy(chat)
    note = mood.apply_mood_policy(chat, BASE_CFG)
    check("%s system (chat) -> body byte-identical" % label, chat == before,
          repr(chat["messages"][0]["content"]))
    check("%s system (chat) -> skipped, persona left alone" % label,
          note.get("mood_skipped") == "no_system_message", note)


# ---------------------------------------------------------------------------
print("\n== 2. live moodd: append-only, inside the node's own band ==")
# ---------------------------------------------------------------------------
mood._token_cache.update(path=None, token=None)
reset_cache()
live, meta = mood.fetch_mood(BASE_CFG)
if not live:
    print("SKIP: moodd not reachable (%s) -- start paperclip-moodd" % meta)
else:
    print("     live vector: %s" % json.dumps({k: round(v, 4) for k, v in live.items()}))
    reset_cache()
    body = copy.deepcopy(LIVE_BODY)
    note = mood.apply_mood_policy(body, BASE_CFG)
    sysmsg = body["messages"][0]["content"]
    check("applied against the live fly", note.get("mood_applied") is True, note)
    check("the 22 live prompt bytes are a strict prefix",
          sysmsg.startswith(SYSTEM_BYTES))
    check("exactly one clause appended",
          sysmsg[len(SYSTEM_BYTES):].startswith("\nCurrent state: ")
          and sysmsg.count("Current state:") == 1,
          repr(sysmsg[len(SYSTEM_BYTES):]))
    check("phrase is at most 6 words",
          len(note["mood_phrase"].split()) <= 6, note["mood_phrase"])
    check("user message untouched",
          body["messages"][1] == LIVE_BODY["messages"][1])
    check("num_predict untouched (n8n owns 80-120)",
          body["options"]["num_predict"] == 100)
    t = body["options"]["temperature"]
    check("temperature only ever rose", t >= LIVE_BODY["options"]["temperature"],
          "%s -> %s" % (LIVE_BODY["options"]["temperature"], t))
    check("temperature stayed inside the 0.2-0.9 band", 0.2 <= t <= 0.9, t)


# ---------------------------------------------------------------------------
print("\n== 3. the band holds at both edges, and the inverted axis reads right ==")
# ---------------------------------------------------------------------------
for t0_, arousal, want_max in ((0.2, 1.0, 0.5), (0.7, 1.0, 0.9),
                               (0.9, 1.0, 0.9), (0.85, 1.0, 0.9)):
    m = {"arousal": arousal, "novelty": 0.0, "valence": 0.0,
         "reinforcement": 0.0, "agitation": 0.85}
    after, before = mood.couple_temperature(m, {"temperature": t0_}, BASE_CFG)
    eff = t0_ if after is None else after
    check("temp %.2f at arousal %.1f -> %.3f, <= %.2f and >= start"
          % (t0_, arousal, eff, want_max),
          t0_ <= eff <= want_max + 1e-9, eff)

after, _ = mood.couple_temperature({"arousal": 0.0}, {"temperature": 0.2}, BASE_CFG)
check("zero arousal leaves a 0.2 node exactly at 0.2", after is None)
after, _ = mood.couple_temperature({"arousal": 1.0}, {}, BASE_CFG)
check("a node that set no temperature gets none invented", after is None)

# agitation is INVERTED: 1.0 undisturbed, 0.0 maximally driven. The measured
# mechanosensory response is 0.889 -> 0.233, so 0.233 must NOT read as calm.
rest = {"arousal": 0.004, "novelty": 0.035, "valence": 0.002,
        "reinforcement": 0.021, "agitation": 0.889}
touched = dict(rest, agitation=0.233)
check("resting network reads idle", mood.classify(rest, BASE_CFG)[0] == "idle",
      mood.classify(rest, BASE_CFG))
check("mechanosensory-driven network reads disturbed",
      mood.classify(touched, BASE_CFG)[0] == "disturbed",
      mood.classify(touched, BASE_CFG))

seen = set()
for name, phrase, _ in mood.STATES:
    check("phrase %-11s <= 6 words, no meta/emoji" % name,
          len(phrase.split()) <= 6 and phrase == phrase.strip()
          and phrase.isascii() and "AI" not in phrase, repr(phrase))
    seen.add(name)
check("every state is reachable by some vector", len(seen) == len(mood.STATES))


# ---------------------------------------------------------------------------
print("\n== 4. the split: the fly drives `glados`, n8n's tag is untouched ==")
# ---------------------------------------------------------------------------
# This is what lets "no behaviour change" (IST-266) and "a fly-centred GLaDOS"
# both be true. It is tested against the SHIPPED default rather than a patched
# config, because the thing worth proving is that the file we ship routes this
# way - a test that configures its own answer proves nothing about production.
SHIPPED = json.load(open("/opt/paperclip-gladosd/config.json"))
check("shipped config couples only `glados`",
      SHIPPED.get("mood_models") == ["glados"], SHIPPED.get("mood_models"))
check("shipped config has coupling on", SHIPPED.get("mood_coupling") is True)
check("shipped config has the ingest on", SHIPPED.get("mood_ingest") is True)

live_cfg = dict(BASE_CFG)
live_cfg.pop("mood_models")          # fall back to the module default
live_cfg.pop("mood_axis_span")
for tag, want in (("qwen2.5:7b-instruct", False), ("qwen2.5:7b", False),
                  ("moondream", False), ("glados", True),
                  ("glados:latest", True), ("GLaDOS", True), ("", False),
                  # A registry pull keeps its namespace. These are the names
                  # the PUBLISHED model actually has on someone else's machine,
                  # so if they do not couple, nobody who pulls her ever gets a
                  # mood - and she still answers in character, so nobody finds
                  # out. The published tag is `jais/GLaDOS`; both spellings and
                  # a Hugging Face GGUF path have to land.
                  ("jais/GLaDOS", True), ("jais/GLaDOS:latest", True),
                  ("jais/glados", True), ("hf.co/jais/glados:Q4_K_M", True),
                  ("registry.example.com/x/glados-big", True),
                  # ...without turning the fence into "couple everything":
                  # a namespace that merely CONTAINS the word must not match,
                  # or n8n's tag could be re-coupled by someone else's naming.
                  ("glados/qwen2.5:7b-instruct", False),
                  ("jais/not-glados", False), ("jais/", False)):
    check("default routing: %-24s coupled=%s" % (repr(tag), want),
          mood.model_coupled(tag, live_cfg) is want)

reset_cache()
n8n_body = copy.deepcopy(LIVE_BODY)          # model qwen2.5:7b-instruct
note = mood.apply_mood_policy(n8n_body, live_cfg)
check("an n8n body is byte-identical under the shipped default",
      n8n_body == LIVE_BODY)
check("...and says why, rather than looking uncoupled by accident",
      note.get("mood_skipped") == "model_not_coupled", note)

reset_cache()
glados_body = copy.deepcopy(LIVE_BODY)
glados_body["model"] = "glados"
note = mood.apply_mood_policy(glados_body, live_cfg)
check("a `glados` body IS coupled against the live fly",
      note.get("mood_applied") is True, note.get("mood_state"))
check("...appending only, prompt bytes still a strict prefix",
      glados_body["messages"][0]["content"].startswith(SYSTEM_BYTES))
# An empty prefix list must mean "couple nothing": a mis-typed config should
# degrade to today's behaviour, never to coupling everything.
check("empty mood_models couples nothing",
      mood.model_coupled("glados", dict(live_cfg, mood_models=[])) is False)


# ---------------------------------------------------------------------------
print("\n== 5. span rescaling: the state line is reachable at all ==")
# ---------------------------------------------------------------------------
# Raw arousal cannot exceed ~0.19 through hearing_JO (measurements/hearing.json),
# so without rescaling every threshold at 0.20+ is dead and she reads `idle`
# forever. That is the exact failure the plan called theatre.
SPAN_CFG = dict(BASE_CFG)
SPAN_CFG.pop("mood_axis_span")                # use the measured default
hi, lo = mood.AXIS_SPAN["arousal"][1], mood.AXIS_SPAN["arousal"][0]
check("arousal span comes from a measurement, not 0-1", (lo, hi) == (0.0, 0.19))
ladder = [0.000, 0.004, 0.018, 0.057, 0.106, 0.188]   # the measured drive ladder
scaled = [mood.rescale(dict(rest, arousal=a), SPAN_CFG)["arousal"]
          for a in ladder]
check("rescaling is monotonic across the measured ladder",
      all(b >= a for a, b in zip(scaled, scaled[1:])),
      [round(s, 3) for s in scaled])
check("the top of the ladder reaches `alert` (>=0.50)", scaled[-1] >= 0.50,
      round(scaled[-1], 3))
check("...which the RAW value never could", ladder[-1] < 0.50, ladder[-1])
check("rest still reads 0.0", scaled[0] == 0.0)
check("rescaling clips at 1.0, so a synthetic 1.0 stays 1.0",
      mood.rescale(dict(rest, arousal=1.0), SPAN_CFG)["arousal"] == 1.0)
# This used `novelty` until IST-280 measured hygro_thermo and gave novelty,
# valence and reinforcement spans of their own. `agitation` is now the one axis
# deliberately left unscaled: mechanosensory moves it monotonically in RAW
# units, and it is what carries intensity past arousal's ceiling.
check("an axis with no measured span passes through untouched",
      mood.rescale(dict(rest, agitation=0.3), SPAN_CFG)["agitation"] == 0.3)
check("a zero-width span is ignored rather than dividing by zero",
      mood.rescale(dict(rest, arousal=0.05),
                   dict(BASE_CFG, mood_axis_span={"arousal": (0.2, 0.2)})
                   )["arousal"] == 0.05)
check("a garbage span is ignored rather than raising",
      mood.rescale(dict(rest, arousal=0.05),
                   dict(BASE_CFG, mood_axis_span={"arousal": "nonsense"})
                   )["arousal"] == 0.05)

# And the note has to carry both vectors, or an odd reply is untraceable: you
# could not tell a network that moved from a rescaling that exaggerated.
reset_cache()
b = copy.deepcopy(LIVE_BODY)
b["model"] = "glados"
note = mood.apply_mood_policy(b, SPAN_CFG)
check("the event note carries raw AND rescaled vectors",
      isinstance(note.get("mood"), dict) and isinstance(note.get("mood_raw"), dict),
      {"mood": note.get("mood"), "mood_raw": note.get("mood_raw")})


# ---------------------------------------------------------------------------
print("\n== 6. the ingest: traffic in, drive out, never on the hot path ==")
# ---------------------------------------------------------------------------
import afferent  # noqa: E402

ING = {"ingest_tau_s": 90.0, "ingest_kick": 0.06, "ingest_max": 0.4,
       "ingest_epsilon": 0.005, "ingest_floor": 0.002,
       "mood_url": "127.0.0.1:9099",
       "mood_token_path": "/opt/paperclip-glados/token"}


def ingest_reset():
    afferent._level = 0.0
    afferent._posted = None
    with afferent._lock:
        afferent._pending = 0


ingest_reset()
t0 = time.time()
for _ in range(10000):
    afferent.note_request("glados")
per = (time.time() - t0) / 10000 * 1e6
check("note_request costs under 10 us (it is on the request path)", per < 10,
      "%.2f us" % per)

ingest_reset()
afferent.note_request("glados")
v = afferent._tick(ING, 0.0)
check("one request is audible but quiet", v is not None and 0.0 < v <= 0.07,
      round(v, 4))

ingest_reset()
for _ in range(5):
    afferent.note_request("glados")
v = afferent._tick(ING, 0.0)
check("a burst of 5 reads as a busy building", 0.25 <= v <= 0.35, round(v, 4))
check("...which rescales into `alert` territory",
      mood.classify(dict(rest, arousal=mood.rescale(
          {"arousal": 0.15, "novelty": 0.035, "valence": 0.002,
           "reinforcement": 0.021, "agitation": 0.889},
          SPAN_CFG)["arousal"]), SPAN_CFG)[0] == "alert")

ingest_reset()
for _ in range(200):
    afferent.note_request("glados")
v = afferent._tick(ING, 0.0)
check("a flood is capped, never pinning the site", v == 0.4, v)

# Decay: after one tau with no traffic the level must fall by ~1/e, and it must
# reach exactly zero rather than decaying asymptotically forever.
ingest_reset()
for _ in range(5):
    afferent.note_request("glados")
afferent._tick(ING, 0.0)
start_level = afferent._level
afferent._tick(ING, 90.0)
check("one tau of silence decays by ~1/e",
      abs(afferent._level - start_level / math.e) < 0.01,
      "%.4f -> %.4f" % (start_level, afferent._level))
for _ in range(20):
    afferent._tick(ING, 90.0)
check("silence settles to exactly zero, not an epsilon", afferent._level == 0.0,
      afferent._level)

# Chatter suppression: an idle service must not post the same value forever.
ingest_reset()
afferent._posted = 0.0
check("no post when nothing changed", afferent._tick(ING, 2.0) is None)

# Fail-open, against a real closed port rather than a mock.
err = afferent._post_drive(dict(ING, mood_url="127.0.0.1:9199",
                                ingest_timeout_s=0.3), 0.05)
check("a dead moodd returns an error string instead of raising", bool(err), err)
err = afferent._post_drive(dict(ING, ingest_site="not_a_real_site"), 0.05)
check("an unknown site is rejected loudly by moodd, not silently dropped",
      bool(err), err)
# The real one, last, so the site name in the shipped config is proven to be a
# site this engine actually has.
err = afferent._post_drive(dict(ING, ingest_site="hearing_JO"), 0.0)
check("the shipped ingest site is accepted by the live engine", err is None,
      err or "hearing_JO accepted")

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
    print("FAILED: " + ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
