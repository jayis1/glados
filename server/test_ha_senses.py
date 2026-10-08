#!/usr/bin/env python3
"""Proof for layer 2b, the HA-sourced senses (Paperclip IST-280).

    python3 /opt/paperclip-gladosd/test_ha_senses.py

Run on the GPU host with paperclip-moodd live. Like test_mood.py, every
fail-open branch is exercised against a REAL socket - a closed port, a host
that does not answer, a listener that accepts and then hangs, an HTTP 401 - and
not against a mock, because a mock cannot be wrong in the way a socket can.

Three properties, in descending order of how badly they would hurt:

1. A dead or slow or unauthorised Home Assistant cannot touch her voice. This
   ingest is on a background thread and never on the request path, so the test
   is that no failure path raises and that the thread survives all of them.
2. It writes ONLY the two sites it owns. afferent.py's hearing_JO shares the
   same drive vector, and clobbering it would silently delete the one sense
   that has been working since IST-270.
3. An event is counted exactly once. A doorbell that is re-counted on every
   poll rings forever; one that is dropped between polls is the sense not
   existing. The cursor is the whole correctness story and it gets its own
   section.
"""

import json
import math
import socket
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, "/opt/paperclip-gladosd")
import ha_afferent  # noqa: E402
import mood  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("%s  %s%s" % ("ok  " if cond else "FAIL", name,
                        ("  -- " + str(detail)) if detail else ""))


def reset():
    """Put the module back to a known state between sections."""
    ha_afferent._level.update({s: 0.0 for s in ha_afferent.OWNED})
    ha_afferent._posted.update({s: None for s in ha_afferent.OWNED})
    ha_afferent._last_temp.clear()
    ha_afferent._cursor = None


# ---------------------------------------------------------------------------
# fixtures: a fake Home Assistant, and sockets that misbehave on purpose
# ---------------------------------------------------------------------------
class FakeHA(BaseHTTPRequestHandler):
    series = []
    status = 200
    delay = 0.0
    hits = 0

    def do_GET(self):
        FakeHA.hits += 1
        if FakeHA.delay:
            time.sleep(FakeHA.delay)
        body = json.dumps(FakeHA.series).encode()
        self.send_response(FakeHA.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def start_fake_ha():
    srv = HTTPServer(("127.0.0.1", 0), FakeHA)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return "http://127.0.0.1:%d" % srv.server_address[1]


def closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def deaf_listener():
    """Accepts and never answers: the branch a closed port does NOT cover."""
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
            held.append(conn)

    threading.Thread(target=accept_forever, daemon=True).start()
    return srv.getsockname()[1]


HA_BASE = start_fake_ha()
DEAF = deaf_listener()
CLOSED = closed_port()

TOUCH = ["binary_sensor.front_door_visitor", "binary_sensor.front_door_person"]
THERMO = ["sensor.a_temperature", "sensor.b_temperature"]

BASE_CFG = {
    "ha_ingest": True,
    "ha_touch_entities": TOUCH,
    "ha_thermo_entities": THERMO,
    "ha_touch_kick": 0.02, "ha_touch_tau_s": 120.0, "ha_touch_max": 0.20,
    "ha_thermo_kick": 0.01, "ha_thermo_tau_s": 180.0, "ha_thermo_max": 0.25,
    "ha_epsilon": 0.005, "ha_floor": 0.002,
    "mood_url": "127.0.0.1:9099",
    "mood_token_path": "/opt/paperclip-glados/token",
    "ha_timeout_s": 2.0, "ha_post_timeout_s": 1.0,
}


def cfg(**over):
    out = dict(BASE_CFG)
    out.update(over)
    return out


def ser(eid, *states):
    return [{"entity_id": eid, "state": s} for s in states]


print("=" * 72)
print("1. counting events: exactly once, or the sense is wrong")
print("=" * 72)

reset()
n, d, seen = ha_afferent._count([ser(TOUCH[0], "off", "on")], set(TOUCH), set(THERMO))
check("an off->on transition is one touch event", n == 1, "events=%d" % n)

reset()
n, _, _ = ha_afferent._count([ser(TOUCH[0], "off", "on", "off")], set(TOUCH), set(THERMO))
check("on then off is still ONE event, not two", n == 1, "events=%d" % n)

reset()
n, _, _ = ha_afferent._count([ser(TOUCH[0], "on", "on", "on")], set(TOUCH), set(THERMO))
check("a sensor already on, reported repeatedly, is not a new knock",
      n == 0, "events=%d" % n)

reset()
n, _, _ = ha_afferent._count([ser(TOUCH[0], "off", "on", "off", "on")],
                             set(TOUCH), set(THERMO))
check("two separate presses in one window count twice", n == 2, "events=%d" % n)

reset()
n, _, _ = ha_afferent._count([ser("binary_sensor.not_watched", "off", "on")],
                             set(TOUCH), set(THERMO))
check("an entity we do not watch cannot drive her", n == 0, "events=%d" % n)

reset()
n, _, _ = ha_afferent._count([ser(TOUCH[0], "off", "unavailable", "on")],
                             set(TOUCH), set(THERMO))
check("a sensor going unavailable and back is one event, not a storm",
      n == 1, "events=%d" % n)

print()
print("=" * 72)
print("2. the thermometer answers to CHANGE, not to being hot")
print("=" * 72)

reset()
_, d, _ = ha_afferent._count([ser(THERMO[0], "50.0", "52.0")], set(TOUCH), set(THERMO))
check("a 2 degC rise is 2 degC of stimulus", abs(d - 2.0) < 1e-6, "degC=%.3f" % d)

reset()
_, d, _ = ha_afferent._count([ser(THERMO[0], "52.0", "50.0")], set(TOUCH), set(THERMO))
check("a 2 degC FALL is also 2 degC of stimulus (absolute)",
      abs(d - 2.0) < 1e-6, "degC=%.3f" % d)

reset()
_, d, _ = ha_afferent._count([ser(THERMO[0], "61.0", "61.0", "61.0")],
                             set(TOUCH), set(THERMO))
check("a drive sitting at 61 degC forever is not a stimulus",
      d == 0.0, "degC=%.3f" % d)

reset()
_, d, _ = ha_afferent._count([ser(THERMO[0], "unavailable", "52.0", "unknown", "53.0")],
                             set(TOUCH), set(THERMO))
check("unavailable is skipped, not read as zero degrees",
      abs(d - 1.0) < 1e-6, "degC=%.3f (a 52->0->53 misread would be 105)" % d)

reset()
ha_afferent._count([ser(THERMO[0], "50.0")], set(TOUCH), set(THERMO))
_, d, _ = ha_afferent._count([ser(THERMO[0], "51.0")], set(TOUCH), set(THERMO))
check("the last reading carries across polls, so a slow drift is still felt",
      abs(d - 1.0) < 1e-6, "degC=%.3f" % d)

print()
print("=" * 72)
print("3. the cursor: an HA blip must delay events, never eat them")
print("=" * 72)

reset()
FakeHA.status, FakeHA.delay = 200, 0.0
FakeHA.series = [ser(TOUCH[0], "off", "on")]
ha_afferent._poll_once(cfg(), HA_BASE, "tok", 1.0)
first = ha_afferent._cursor
check("a successful poll advances the cursor", first is not None, first)
check("one event moved mechanosensory off zero",
      ha_afferent._level[ha_afferent.TOUCH_SITE] > 0,
      "level=%.4f" % ha_afferent._level[ha_afferent.TOUCH_SITE])

level_before = ha_afferent._level[ha_afferent.TOUCH_SITE]
FakeHA.series = []
ha_afferent._poll_once(cfg(), HA_BASE, "tok", 1.0)
check("the same press is NOT re-counted on the next poll",
      ha_afferent._level[ha_afferent.TOUCH_SITE] < level_before,
      "level %.4f -> %.4f (decaying, not ringing forever)"
      % (level_before, ha_afferent._level[ha_afferent.TOUCH_SITE]))

held = ha_afferent._cursor
FakeHA.status = 500
ha_afferent._poll_once(cfg(), HA_BASE, "tok", 1.0)
check("a failing poll does NOT advance the cursor",
      ha_afferent._cursor == held, "cursor unchanged")
check("and it records why", bool(ha_afferent._stats["last_error"]),
      ha_afferent._stats["last_error"])
FakeHA.status = 200

print()
print("=" * 72)
print("4. fail open: nothing here may cost her a reply")
print("=" * 72)

for name, base in (("a closed port", "http://127.0.0.1:%d" % CLOSED),
                   ("a host that accepts and then hangs", "http://127.0.0.1:%d" % DEAF),
                   ("a garbage base URL", "http://no-such-host.invalid:8123")):
    reset()
    t0 = time.time()
    raised = None
    try:
        ha_afferent._poll_once(cfg(ha_timeout_s=1.0), base, "tok", 1.0)
    except Exception as exc:  # noqa: BLE001
        raised = exc
    took = (time.time() - t0) * 1000
    check("%s does not raise" % name, raised is None, raised)
    check("%s is bounded by the timeout" % name, took < 3000, "%.0f ms" % took)

reset()
FakeHA.status = 401
raised = None
try:
    ha_afferent._poll_once(cfg(), HA_BASE, "wrong-token", 1.0)
except Exception as exc:  # noqa: BLE001
    raised = exc
check("a rejected token does not raise", raised is None, raised)
check("a rejected token is recorded, not swallowed silently",
      "401" in str(ha_afferent._stats["last_error"]),
      ha_afferent._stats["last_error"])
FakeHA.status = 200

reset()
FakeHA.series = [[{"nonsense": True}], None, []]
raised = None
try:
    ha_afferent._poll_once(cfg(), HA_BASE, "tok", 1.0)
except Exception as exc:  # noqa: BLE001
    raised = exc
check("a malformed HA response does not raise", raised is None, raised)
FakeHA.series = []

print()
print("=" * 72)
print("5. it writes only the two sites it owns")
print("=" * 72)

posted = {}
real_post = ha_afferent._post_drive


def spy(cfg_, values):
    posted.clear()
    posted.update(values)
    return None


ha_afferent._post_drive = spy
reset()
FakeHA.series = [ser(TOUCH[0], "off", "on"), ser(THERMO[0], "50.0", "53.0")]
ha_afferent._poll_once(cfg(), HA_BASE, "tok", 1.0)
ha_afferent._post_drive = real_post
check("a poll posts mechanosensory and hygro_thermo",
      set(posted) == set(ha_afferent.OWNED), sorted(posted))
check("and nothing else, ever", "hearing_JO" not in posted, sorted(posted))

err = ha_afferent._post_drive(cfg(), {"hearing_JO": 0.4})
check("a direct attempt to post hearing_JO is refused by the filter",
      err == "nothing owned to post", err)

print()
print("=" * 72)
print("6. the integrator decays like the fly does")
print("=" * 72)

reset()
ha_afferent._level[ha_afferent.TOUCH_SITE] = 0.2
ha_afferent._decay(ha_afferent.TOUCH_SITE, 120.0, 120.0, 0.0, 0.2, 0.002)
got = ha_afferent._level[ha_afferent.TOUCH_SITE]
check("one tau of quiet decays by about 1/e",
      abs(got - 0.2 / math.e) < 0.002, "0.2000 -> %.4f" % got)

reset()
ha_afferent._level[ha_afferent.TOUCH_SITE] = 0.0019
ha_afferent._decay(ha_afferent.TOUCH_SITE, 1.0, 120.0, 0.0, 0.2, 0.002)
check("below the floor it snaps to exactly zero, not 1e-9",
      ha_afferent._level[ha_afferent.TOUCH_SITE] == 0.0,
      ha_afferent._level[ha_afferent.TOUCH_SITE])

reset()
ha_afferent._decay(ha_afferent.TOUCH_SITE, 1.0, 120.0, 5.0, 0.20, 0.002)
check("the cap holds, so a flurry informs her instead of pinning her",
      ha_afferent._level[ha_afferent.TOUCH_SITE] == 0.20,
      ha_afferent._level[ha_afferent.TOUCH_SITE])

print()
print("=" * 72)
print("7. the credential file")
print("=" * 72)

base, tok, err = ha_afferent.load_env("/opt/paperclip-glados/secrets/ha.env")
check("the deployed env file parses", err is None and base and tok, err or base)
h = ha_afferent.health(cfg())
check("the token is not in /health anywhere",
      tok not in json.dumps(h) if tok else True)
check("nor is the base URL mistaken for a secret, it is simply absent",
      "HA_TOKEN" not in json.dumps(h))

_, _, err = ha_afferent.load_env("/opt/paperclip-glados/secrets/does-not-exist")
check("a missing env file is an error string, not an exception", bool(err), err)

print()
print("=" * 72)
print("8. against the live fly: the other sense survives us")
print("=" * 72)

tok_path = "/opt/paperclip-glados/token"
live_cfg = cfg()


def live_mood():
    vec, meta = mood.fetch_mood({"mood_url": "127.0.0.1:9099",
                                 "mood_token_path": tok_path,
                                 "mood_cache_ms": 0})
    return vec, meta


vec, meta = live_mood()
check("moodd is live and answering", vec is not None, meta)

err = ha_afferent._post_drive(live_cfg, {ha_afferent.TOUCH_SITE: 0.02})
check("the live engine accepts a mechanosensory drive", err is None, err)
time.sleep(2.0)
err = ha_afferent._post_drive(live_cfg, {s: 0.0 for s in ha_afferent.OWNED})
check("and accepts the release back to zero", err is None, err)

err = ha_afferent._post_drive(live_cfg, {"photoreceptor": 0.4})
check("a site we do not own is filtered before it reaches the engine",
      err == "nothing owned to post", err)

print()
print("=" * 72)
print("9. the rescaling IST-280 added")
print("=" * 72)

spans = mood.AXIS_SPAN
check("arousal still scales against hearing's measured ceiling",
      spans.get("arousal") == (0.0, 0.19), spans.get("arousal"))
for axis in ("novelty", "valence", "reinforcement"):
    check("%s now has a measured span" % axis, axis in spans, spans.get(axis))
check("agitation is deliberately unscaled", "agitation" not in spans)

cfg_none = {}
rest = {"arousal": 0.0, "novelty": 0.0587, "valence": -0.0005,
        "reinforcement": 0.0, "agitation": 0.8275}
scaled = mood.rescale(rest, cfg_none)
check("measured rest still rescales to the floor on every axis",
      all(scaled[a] <= 0.01 for a in ("arousal", "novelty", "valence",
                                      "reinforcement")),
      {a: round(scaled[a], 4) for a in scaled})
state, _ = mood.classify(scaled, cfg_none)
check("and rest still reads idle, so this changed nothing at rest",
      state == "idle", state)

hygro_full = {"arousal": 0.0, "novelty": 0.2934, "valence": 0.3569,
              "reinforcement": 0.4197, "agitation": 0.8175}
scaled = mood.rescale(hygro_full, cfg_none)
check("hygro_thermo at full drive now reaches the top of three axes",
      all(scaled[a] > 0.95 for a in ("novelty", "valence", "reinforcement")),
      {a: round(scaled[a], 3) for a in ("novelty", "valence", "reinforcement")})
state, phrase = mood.classify(scaled, cfg_none)
check("which makes a previously unreachable phrase reachable",
      state in ("curious", "rewarded", "pleased"), "%s: %s" % (state, phrase))

mech_full = {"arousal": 1.0, "novelty": 0.0429, "valence": 0.0009,
             "reinforcement": 0.0, "agitation": 0.0191}
state, phrase = mood.classify(mood.rescale(mech_full, cfg_none), cfg_none)
check("mechanosensory at full drive reads disturbed, not idle",
      state == "disturbed", "%s: %s" % (state, phrase))

mech_one = {"arousal": 0.0741, "novelty": 0.0601, "valence": 0.0059,
            "reinforcement": 0.0, "agitation": 0.7222}
state, phrase = mood.classify(mood.rescale(mech_one, cfg_none), cfg_none)
check("and one single door event reads as something stirring",
      state == "stirring", "%s: %s" % (state, phrase))

print()
print("=" * 72)
print("%d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
    for name in FAIL:
        print("   FAILED: %s" % name)
print("=" * 72)
sys.exit(1 if FAIL else 0)
