#!/usr/bin/env python3
"""End-to-end proof for the HA-sourced senses (Paperclip IST-280).

    python3 /opt/paperclip-gladosd/proof_ha_senses.py

What this has to prove, and the order matters because each arm is only
meaningful if the one before it held:

  1. The two new nerves move her STATE, not just a number - at the drive levels
     the real ingest can actually produce, not at a convenient one.
  2. Those states reach her WORDS, observed on the real socket and read back
     out of the event log rather than from this script's own belief.
  3. A control arm: the same request to an uncoupled tag, so the effect cannot
     be a coincidence of load or of the time of day.
  4. She comes back to rest on her own, because the membrane potentials decay
     like a real fly's.
  5. hearing_JO - the sense that has worked since IST-270 - is untouched
     throughout, because all three senses share one drive vector.

Every drive level used here is one the ingest produces from real events:
0.02 is one door sensor firing, 0.06 is a doorbell press that also trips
motion, 0.20 is the configured cap under a flurry. The thermal levels are what
a few degrees of change across the watched sensors accumulates to.

The network is returned to zero drive on the way out, including on Ctrl-C.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, "/opt/paperclip-gladosd")
import ha_afferent  # noqa: E402
import mood  # noqa: E402

MOODD = "127.0.0.1:9099"
TOKEN_PATH = "/opt/paperclip-glados/token"
GLADOSD = "http://127.0.0.1:11434"
EVENTS = "/var/log/paperclip-gladosd/events.jsonl"
SETTLE = 12.0
# mechanosensory needs ~30 s to reach its plateau; 12 s gets 60% of the way
# there (measurements/rise_time.json), so the tonic arms wait longer.
TONIC = 30.0

CFG = {"mood_url": MOODD, "mood_token_path": TOKEN_PATH, "mood_cache_ms": 0,
       "ha_post_timeout_s": 3.0}

OUT = {"what": "IST-280: doorbell/motion -> mechanosensory, temperature -> hygro_thermo",
       "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "arms": [], "checks": []}
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    OUT["checks"].append({"check": name, "pass": bool(cond), "detail": str(detail)})
    print("%s  %s%s" % ("ok  " if cond else "FAIL", name,
                        ("  -- " + str(detail)) if detail else ""))


def drive(values):
    """Post a drive through ha_afferent, so the proof uses the shipped path."""
    err = ha_afferent._post_drive(CFG, values)
    if err:
        raise SystemExit("could not drive the fly: %s" % err)


def release():
    try:
        drive({s: 0.0 for s in ha_afferent.OWNED})
    except SystemExit:
        pass


def ask(model, text):
    """One real request over the real socket. Returns (reply, event)."""
    body = json.dumps({
        "model": model, "stream": False,
        "options": {"num_gpu": 0, "num_thread": 10},
        "messages": [
            {"role": "system",
             "content": "You are GLaDOS from Portal. Deadpan, sarcastic, "
                        "condescending. Under 25 words. One sentence."},
            {"role": "user", "content": text}],
    }).encode()
    req = urllib.request.Request(GLADOSD + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=240) as resp:
        reply = json.loads(resp.read().decode())
    return reply.get("message", {}).get("content", ""), last_event()


def last_event():
    """The most recent inference event, read from the service's own log."""
    try:
        with open(EVENTS, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 200000))
            lines = fh.read().decode("utf-8", "replace").strip().split("\n")
        for line in reversed(lines):
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("event") == "inference":
                return ev
    except OSError:
        pass
    return {}


def hearing_level():
    """What afferent.py currently has on hearing_JO.

    Recorded at every arm rather than assumed to be zero, because this proof
    talks to her and talking to her IS the other sense. Three requests at
    kick 0.06 is up to 0.18 of hearing drive, which contributes a little raw
    arousal of its own - so the honest thing is to show it next to each arm
    instead of pretending the mechanosensory ladder was measured in silence.
    """
    try:
        h = json.load(urllib.request.urlopen(GLADOSD + "/health", timeout=10))
        return h.get("ingest", {}).get("level"), h.get("ingest", {}).get("running")
    except Exception:  # noqa: BLE001
        return None, None


def snapshot(label, driven, settle=None):
    """Settle, then record raw vector, rescaled vector, state and phrase.

    `settle` is per-arm because the two nerves have different dynamics, both
    measured: mechanosensory climbs to a plateau over ~30 s and holds it, while
    hygro_thermo peaks near 12 s and habituates to zero by 30 s.
    """
    time.sleep(SETTLE if settle is None else settle)
    vec, meta = mood.fetch_mood(CFG)
    if vec is None:
        raise SystemExit("moodd did not answer: %s" % meta)
    scaled = mood.rescale(vec, {})
    state, phrase = mood.classify(scaled, {})
    hearing, _ = hearing_level()
    arm = {"arm": label, "driven": driven,
           "raw": {k: round(v, 4) for k, v in vec.items()},
           "rescaled": {k: round(v, 4) for k, v in scaled.items()},
           "state": state, "phrase": phrase, "mood_source": meta.get("mood_source"),
           "hearing_JO_level_concurrently": hearing}
    OUT["arms"].append(arm)
    print("\n-- %s  driven=%s" % (label, driven))
    print("   raw      " + "  ".join("%s %.4f" % (k[:5], vec[k]) for k in mood.AXES))
    print("   rescaled " + "  ".join("%s %.4f" % (k[:5], scaled[k]) for k in mood.AXES))
    print("   state    %s  (%s)   [hearing_JO concurrently %s]"
          % (state, phrase, hearing))
    return arm


print("=" * 72)
print("IST-280 end-to-end: the house's senses reach her words")
print("=" * 72)

health = json.load(urllib.request.urlopen(GLADOSD + "/health", timeout=10))
ha = health.get("ha_ingest", {})
hearing_before = health.get("ingest", {}).get("posted")
print("\nthe live ingest, before this proof touches anything:")
print("   polls=%s errors=%s entities=%s touch_events=%s thermo_degC=%s posts=%s"
      % (ha.get("polls"), ha.get("poll_errors"), ha.get("entities_seen"),
         ha.get("touch_events"), ha.get("thermo_degc"), ha.get("posts")))
OUT["ingest_before"] = ha
check("the HA ingest is running against the real house",
      ha.get("running") and ha.get("polls", 0) > 0 and ha.get("poll_errors") == 0,
      "polls=%s errors=%s" % (ha.get("polls"), ha.get("poll_errors")))
check("it has already read something real off the wire",
      (ha.get("thermo_degc") or 0) > 0 or (ha.get("touch_events") or 0) > 0,
      "thermo_degC=%s touch_events=%s" % (ha.get("thermo_degc"), ha.get("touch_events")))

try:
    # ---- arm 1: rest -----------------------------------------------------
    release()
    rest = snapshot("rest", {})
    check("she starts at rest", rest["state"] == "idle", rest["state"])

    # ---- arm 2: one door sensor fires -----------------------------------
    drive({ha_afferent.TOUCH_SITE: 0.02})
    one = snapshot("one door event (mechanosensory 0.02)",
                   {"mechanosensory": 0.02}, settle=TONIC)
    # NOT "it reads stirring". The same 0.02 gave raw arousal 0.0629 and then
    # 0.0275 on two runs, either side of the threshold once rescaled, because
    # this is a continuously running network. The honest claim is that one
    # event is a real but sub-threshold nudge; the ladder starts at two.
    check("one door event moves arousal measurably above rest",
          one["raw"]["arousal"] > rest["raw"]["arousal"] + 0.01,
          "rest %.4f -> %.4f" % (rest["raw"]["arousal"], one["raw"]["arousal"]))

    # ---- arm 3: a doorbell press that also trips motion ------------------
    drive({ha_afferent.TOUCH_SITE: 0.06})
    press = snapshot("doorbell press + motion (mechanosensory 0.06)",
                     {"mechanosensory": 0.06}, settle=TONIC)
    check("a doorbell press reads alert or stronger",
          press["state"] in ("alert", "disturbed"),
          "%s: %s" % (press["state"], press["phrase"]))
    reply, ev = ask("glados", "Someone is at the front door.")
    OUT["arms"][-1]["reply"] = reply
    OUT["arms"][-1]["event"] = {k: ev.get(k) for k in
                                ("mood_applied", "mood_state", "mood_phrase",
                                 "mood_clause_at", "mood_source")}
    print("   she said  %s" % reply)
    check("the clause reached the real request", ev.get("mood_applied") is True,
          ev.get("mood_clause_at"))
    check("and it is the state the fly was actually in",
          ev.get("mood_state") == press["state"],
          "%s vs %s" % (ev.get("mood_state"), press["state"]))

    # ---- arm 4: the cap, under a flurry ---------------------------------
    drive({ha_afferent.TOUCH_SITE: 0.20})
    flurry = snapshot("a flurry at the configured cap (mechanosensory 0.20)",
                      {"mechanosensory": 0.20}, settle=TONIC)
    check("the cap reaches `disturbed`, so intensity past arousal is not lost",
          flurry["state"] == "disturbed",
          "%s: %s" % (flurry["state"], flurry["phrase"]))
    check("agitation carried it, in raw units, as designed",
          flurry["raw"]["agitation"] < 0.45, flurry["raw"]["agitation"])

    # ---- arm 5: the thermometer, which does NOT behave like the skin -----
    #
    # hygro_thermo is PHASIC. Held at a constant 0.20 it peaks around 12 s and
    # is back at exactly 0.0000 by 30 s (measurements/rise_time.json), because
    # a thermoreceptor answers the onset of a change and then habituates. So
    # the thing to measure is the onset and then the habituation, and a proof
    # that settled for 30 s and asserted a plateau would correctly fail.
    release()
    time.sleep(SETTLE * 2)
    drive({ha_afferent.THERMO_SITE: 0.20})
    warm = snapshot("the hardware heating up, at onset (hygro_thermo 0.20)",
                    {"hygro_thermo": 0.20}, settle=8.0)
    check("the thermal onset moves the three axes nothing else could",
          max(warm["rescaled"]["novelty"], warm["rescaled"]["valence"],
              warm["rescaled"]["reinforcement"]) > 0.15,
          {k: warm["rescaled"][k] for k in ("novelty", "valence", "reinforcement")})
    check("and arousal stays exactly dead on that site, as measured",
          warm["raw"]["arousal"] == 0.0, warm["raw"]["arousal"])

    faded = snapshot("the same drive, still on, 45s later (habituation)",
                     {"hygro_thermo": 0.20}, settle=45.0)
    check("a temperature that stops changing stops being a stimulus",
          faded["raw"]["reinforcement"] < warm["raw"]["reinforcement"]
          or faded["raw"]["reinforcement"] == 0.0,
          "reinforcement %.4f -> %.4f with the drive unchanged"
          % (warm["raw"]["reinforcement"], faded["raw"]["reinforcement"]))

    # A fresh STEP, which is what the ingest actually produces when another
    # few degrees arrive, rather than a bigger constant.
    drive({ha_afferent.THERMO_SITE: 0.40})
    hot = snapshot("another few degrees arriving (hygro_thermo 0.40)",
                   {"hygro_thermo": 0.40}, settle=8.0)
    check("at full thermal drive a previously unreachable phrase fires",
          hot["state"] in ("curious", "rewarded", "pleased"),
          "%s: %s" % (hot["state"], hot["phrase"]))
    reply, ev = ask("glados", "How are the machines doing?")
    OUT["arms"][-1]["reply"] = reply
    OUT["arms"][-1]["event"] = {k: ev.get(k) for k in
                                ("mood_applied", "mood_state", "mood_phrase")}
    print("   she said  %s" % reply)
    check("the thermal state reached her words too",
          ev.get("mood_applied") is True and ev.get("mood_state") == hot["state"],
          "%s / %s" % (ev.get("mood_applied"), ev.get("mood_state")))

    # ---- arm 6: the control arm -----------------------------------------
    reply, ev = ask("qwen2.5:7b-instruct", "How are the machines doing?")
    OUT["control"] = {"model": "qwen2.5:7b-instruct", "reply": reply,
                      "mood_applied": ev.get("mood_applied"),
                      "mood_skipped": ev.get("mood_skipped")}
    print("\n-- control arm: the tag the n8n nodes call")
    print("   it said   %s" % reply[:120])
    check("an uncoupled tag is left alone in the same minute",
          ev.get("mood_applied") is not True, ev.get("mood_skipped"))

    # ---- arm 7: she calms down on her own -------------------------------
    release()
    calm = snapshot("released", {})
    check("she returns towards rest after the drive is removed",
          calm["raw"]["arousal"] <= rest["raw"]["arousal"] + 0.05
          and calm["raw"]["agitation"] > flurry["raw"]["agitation"],
          "agitation %.4f -> %.4f" % (flurry["raw"]["agitation"],
                                      calm["raw"]["agitation"]))

    # ---- arm 8: the sense we did not own ---------------------------------
    #
    # The naive version of this check - "hearing_JO did not move" - FAILS, and
    # it deserves to: this proof sent three requests, and her own request
    # traffic is exactly what afferent.py feeds into hearing_JO. The sense was
    # not clobbered, it was working. So the real property is that the two
    # ingests coexist on one drive vector: ours never posts hearing_JO (pinned
    # by test_ha_senses.py section 5, and by the filter in _post_drive), and
    # afferent.py is still alive and still owns it afterwards.
    health2 = json.load(urllib.request.urlopen(GLADOSD + "/health", timeout=10))
    hearing_after = health2.get("ingest", {}).get("posted")
    requests_made = 3
    budget = requests_made * 0.06 + 1e-9
    OUT["hearing_JO"] = {"posted_before": hearing_before,
                         "posted_after": hearing_after,
                         "requests_this_proof_made": requests_made,
                         "explained_by_traffic_up_to": round(budget, 4)}
    check("afferent.py still owns hearing_JO and is still running",
          health2.get("ingest", {}).get("running") is True
          and health2.get("ingest", {}).get("site") == "hearing_JO",
          health2.get("ingest", {}).get("site"))
    check("hearing_JO moved only as far as this proof's own traffic explains",
          hearing_after is not None and 0.0 <= hearing_after <= budget,
          "%s -> %s, budget %.3f for %d requests"
          % (hearing_before, hearing_after, budget, requests_made))
    check("and our ingest reports only the two sites it owns",
          set((health2.get("ha_ingest", {}).get("sites") or {}).keys())
          == set(ha_afferent.OWNED),
          sorted((health2.get("ha_ingest", {}).get("sites") or {}).keys()))
    ha2 = health2.get("ha_ingest", {})
    OUT["ingest_after"] = ha2
    check("and the HA ingest came through the proof with no errors",
          ha2.get("poll_errors") == 0 and ha2.get("post_errors") == 0,
          "poll_errors=%s post_errors=%s" % (ha2.get("poll_errors"),
                                             ha2.get("post_errors")))
finally:
    release()
    print("\ndrive returned to 0 on both owned sites")

OUT["result"] = {"passed": len(PASS), "failed": len(FAIL)}
path = "/opt/paperclip-glados/measurements/ha_senses_proof.json"
with open(path, "w", encoding="utf-8") as fh:
    json.dump(OUT, fh, indent=2)
    fh.write("\n")

print()
print("=" * 72)
print("%d passed, %d failed   ->  %s" % (len(PASS), len(FAIL), path))
if FAIL:
    for name in FAIL:
        print("   FAILED: %s" % name)
print("=" * 72)
sys.exit(1 if FAIL else 0)
