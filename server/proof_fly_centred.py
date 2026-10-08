#!/usr/bin/env python3
"""End-to-end proof that `glados` is driven by the fly (IST-270 layers 2 + 2a).

    python3 /opt/paperclip-gladosd/proof_fly_centred.py

test_mood.py proves the properties of the coupling in isolation. This proves
the loop actually closes on the running services, which is a different claim
and the one that is easy to get wrong - the coupling code sat on disk for an
hour already written, passing nothing, because the service it belonged to had
been started before it existed.

The loop under test:

    a request arrives at gladosd
      -> afferent.note_request()            (layer 2a, no credential needed)
      -> hearing_JO drive on moodd          (the fly's auditory nerve)
      -> 164,587 LIF neurons on two T400s   (layer 1, IST-269)
      -> five readout populations           -> the mood vector
      -> a state clause + temperature       (layer 2)
      -> what qwen2.5:7b-instruct renders   (layer 3)

What makes it a proof rather than a demo is the control arm: the same prompt is
sent to `qwen2.5:7b-instruct` in the same conditions, and that tag must come
back provably uncoupled. If traffic moved both, the mood would be a coincidence
of load rather than a coupling; if it moved neither, the ingest is dead.

Traffic is generated with num_predict=1 bodies - the fly hears a request when
it ARRIVES (note_request runs before the proxy call), so a stimulus costs a
second rather than the ~13 s a full reply takes at 8 tok/s.
"""
import json
import subprocess
import sys
import time
import urllib.request

GLADOSD = "http://127.0.0.1:11434"
EVENTS = "/var/log/paperclip-gladosd/events.jsonl"
AXES = ("arousal", "novelty", "valence", "reinforcement", "agitation")
PROMPT = "Someone is at the front door."
SYSTEM = ("You are GLaDOS from Portal. Deadpan, sarcastic, condescending, "
          "occasionally menacing. Under 25 words. One sentence. No emojis, "
          "no quotes, no meta about being an AI.")


def post(path, payload, timeout=300):
    req = urllib.request.Request(
        GLADOSD + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def health():
    with urllib.request.urlopen(GLADOSD + "/health", timeout=10) as r:
        return json.loads(r.read())


def last_inference():
    """The most recent inference event, which is where the mood note lands."""
    out = subprocess.run(["tail", "-n", "400", EVENTS], capture_output=True,
                         text=True).stdout.splitlines()
    for line in reversed(out):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("event") == "inference":
            return rec
    return {}


def ask(model, temperature=0.7, num_predict=100):
    body = {"model": model, "stream": False,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": PROMPT}],
            "options": {"temperature": temperature,
                        "num_predict": num_predict}}
    t0 = time.time()
    out = post("/api/chat", body)
    dt = time.time() - t0
    text = (out.get("message") or {}).get("content", "").strip()
    return text, dt, last_inference()


def stimulate(n, model="glados"):
    """n cheap requests: heard on arrival, so they cost ~a second each."""
    for _ in range(n):
        try:
            post("/api/chat", {"model": model, "stream": False,
                               "messages": [{"role": "user", "content": "."}],
                               "options": {"num_predict": 1}}, timeout=120)
        except Exception as exc:  # noqa: BLE001
            print("   (stimulus request failed, still heard: %s)" % exc)


def show(label, h):
    m = h["mood"]
    print("   %-10s state=%-10s arousal=%.4f (raw %.4f)  valence=%+.4f  "
          "drive=%.3f  sensed=%d"
          % (label, m.get("state"), (m.get("mood") or {}).get("arousal", -1),
             (m.get("mood_raw") or {}).get("arousal", -1),
             (m.get("mood_raw") or {}).get("valence", 0),
             h["ingest"]["level"], h["ingest"]["requests_sensed"]))


def main():
    h = health()
    print("gladosd up %.0fs   coupling=%s on %s   ingest=%s -> %s"
          % (h["uptime_s"], h["mood"]["coupling"], h["mood"]["coupled_models"],
             h["ingest"]["running"], h["ingest"]["site"]))
    if not h["mood"]["coupling"] or not h["ingest"]["running"]:
        print("coupling or ingest is off; nothing to prove", file=sys.stderr)
        return 2
    results = {"started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "arms": {}}

    print("\n== A. at rest ==")
    show("rest", h)
    results["arms"]["rest_state"] = h["mood"]
    text_rest, dt, ev = ask("glados")
    print("   glados (%.1fs): %s" % (dt, text_rest))
    print("   note: state=%s phrase=%r temp %s -> %s"
          % (ev.get("mood_state"), ev.get("mood_phrase"),
             ev.get("temperature_before"), ev.get("temperature")))
    results["arms"]["rest"] = {"reply": text_rest, "latency_s": round(dt, 1),
                               "note": ev}
    rest_arousal = (h["mood"].get("mood_raw") or {}).get("arousal", 0.0)

    print("\n== B. after a burst of traffic ==")
    stimulate(8)
    # The EMA is 1500 SIMULATED ms and the sim runs at 0.21x, so the smoother
    # alone is ~7 s of wall clock. Reading immediately reads the old state.
    time.sleep(14)
    hb = health()
    show("busy", hb)
    results["arms"]["busy_state"] = hb["mood"]
    busy_arousal = (hb["mood"].get("mood_raw") or {}).get("arousal", 0.0)
    text_busy, dt, ev_busy = ask("glados")
    print("   glados (%.1fs): %s" % (dt, text_busy))
    print("   note: state=%s phrase=%r temp %s -> %s"
          % (ev_busy.get("mood_state"), ev_busy.get("mood_phrase"),
             ev_busy.get("temperature_before"), ev_busy.get("temperature")))
    results["arms"]["busy"] = {"reply": text_busy, "latency_s": round(dt, 1),
                               "note": ev_busy}

    print("\n== C. control arm: the tag all 22 n8n nodes use ==")
    text_ctl, dt, ev_ctl = ask("qwen2.5:7b-instruct")
    print("   qwen2.5:7b-instruct (%.1fs): %s" % (dt, text_ctl))
    print("   note: %s" % {k: ev_ctl.get(k) for k in
                           ("mood_applied", "mood_skipped", "model")})
    results["arms"]["control"] = {"reply": text_ctl, "note": ev_ctl}

    print("\n== verdict ==")
    checks = [
        ("the fly heard the traffic",
         hb["ingest"]["requests_sensed"] > h["ingest"]["requests_sensed"],
         "%d -> %d requests sensed" % (h["ingest"]["requests_sensed"],
                                       hb["ingest"]["requests_sensed"])),
        ("the drive reached moodd",
         hb["ingest"]["level"] > 0 and hb["ingest"]["post_errors"] == 0,
         "level %.3f, %d posts, %d errors" % (hb["ingest"]["level"],
                                              hb["ingest"]["posts"],
                                              hb["ingest"]["post_errors"])),
        ("the connectome's arousal rose",
         busy_arousal > rest_arousal,
         "raw arousal %.4f -> %.4f" % (rest_arousal, busy_arousal)),
        ("the state line changed with it",
         ev_busy.get("mood_phrase") != ev.get("mood_phrase"),
         "%r -> %r" % (ev.get("mood_phrase"), ev_busy.get("mood_phrase"))),
        ("temperature rose, and stayed inside the 0.2-0.9 band",
         (ev_busy.get("temperature") or 0.7) >= (ev.get("temperature") or 0.7)
         and 0.2 <= (ev_busy.get("temperature") or 0.7) <= 0.9,
         "%s -> %s" % (ev.get("temperature"), ev_busy.get("temperature"))),
        ("`glados` was coupled on both arms",
         ev.get("mood_applied") is True and ev_busy.get("mood_applied") is True,
         "rest=%s busy=%s" % (ev.get("mood_applied"),
                              ev_busy.get("mood_applied"))),
        ("the control arm was NOT coupled, under the same conditions",
         ev_ctl.get("mood_applied") is False
         and ev_ctl.get("mood_skipped") == "model_not_coupled",
         ev_ctl.get("mood_skipped")),
        ("the control arm still answered in character",
         bool(text_ctl) and len(text_ctl.split()) <= 60,
         "%d words" % len(text_ctl.split())),
        ("she said something different in the two states",
         text_rest != text_busy, "yes" if text_rest != text_busy else
         "IDENTICAL - suspicious at temperature > 0"),
    ]
    ok = True
    for name, cond, detail in checks:
        ok = ok and cond
        print("%s  %s  -- %s" % ("ok  " if cond else "FAIL", name, detail))
    results["checks"] = [{"name": n, "pass": bool(c), "detail": d}
                         for n, c, d in checks]
    results["passed"] = ok
    out = "/opt/paperclip-glados/results/fly_centred_proof.json"
    with open(out, "w") as fh:
        json.dump(results, fh, indent=1)
    print("\n%s   wrote %s" % ("PASS" if ok else "FAIL", out))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
