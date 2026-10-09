#!/usr/bin/env python3
"""Does the door sense agree with Home Assistant's own recorder?

Why this exists
---------------
The touch sense was wired, tested, deployed and reported working, and it had
counted zero of six real door transitions for sixteen hours. Nothing errored.
`poll_errors` was 0, the thermal channel was fine, and the only symptom was a
GLaDOS who was never startled - which in this project reads as working
correctly. The counter that would have exposed it (`ha_ingest.touch_events`)
was sitting in /health the whole time saying 0.

The counting bug is fixed and proved by replay. What is NOT yet proved is the
thing that actually matters: a real person opening a real door, counted by the
live service. That cannot be staged without touching someone's house, and it
cannot be confirmed by looking once, because

    a sense that has never fired is indistinguishable from a quiet house.

So instead of asking a human to go and knock, this compares the live counter
against Home Assistant's recorder on a timer and says which of those two worlds
it is in. The first real knock proves the sense by itself, and if that knock is
missed, this says so rather than waiting to be noticed.

Outcomes
--------
AGREE_FIRED        HA recorded transitions and the counter matched. This is the
                   proof. Nothing else here is.
AGREE_QUIET        Both zero. Consistent, and evidence of nothing - the whole
                   failure above looked exactly like this.
UNDERCOUNT         The counter is behind, and the missing transitions were
                   clustered inside HA's visibility lag. A known, accepted
                   limit: several presses within ~15 s collapse into one event
                   (one knock is one knock). Not a defect.
MISMATCH           The counter is behind on transitions that were NOT a
                   flurry, or ahead of the recorder persistently. The original
                   bug, back.
COULD_NOT_MEASURE  /health or HA was unreachable, or the service restarted and
                   the counter re-anchored. A checker that cannot say "I failed
                   to look" says "broken" instead, and gets ignored.

Exit code is 0 for AGREE_* and UNDERCOUNT, 1 for MISMATCH, 3 for
COULD_NOT_MEASURE - so a timer failure and a real regression are not the same
alert.

Accounting
----------
Cumulative since the service started, not per-window deltas. HA admits a change
up to ~15 s after it happened, so an event near a window edge lands on one side
of the counter and the other side of the recorder; compared as deltas that
shows up as a phantom mismatch in one direction and then the other. Compared
cumulatively, a boundary straggler is a transient that closes itself on the next
run, and only a persistent gap survives - which is what a real defect is.
Hence also the two-strike rule: a gap must survive two consecutive checks
before it is called a MISMATCH.

Read-only. It never posts drive, never writes to Home Assistant, and never
restarts anything.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# These /opt paths are this deployment's, not a general assumption; they are
# left visible rather than rewritten so the published copy is the copy that
# produced the logs. Override with the matching env vars.
HEALTH = os.environ.get("GLADOS_HEALTH_URL", "http://127.0.0.1:11435/health")
CONFIG = os.environ.get("GLADOS_CONFIG", "/opt/paperclip-gladosd/config.json")
HA_ENV = os.environ.get("GLADOS_HA_ENV", "/opt/paperclip-glados/secrets/ha.env")
STATE = os.environ.get("GLADOS_XCHECK_STATE",
                       "/opt/paperclip-glados/measurements/xcheck_touch.state.json")
LOG = os.environ.get("GLADOS_XCHECK_LOG",
                     "/opt/paperclip-glados/measurements/xcheck_touch.jsonl")

# Wider than HA's worst measured delay in admitting a change (14.6 s on this
# install). Transitions closer together than this are indistinguishable from
# one event to the ingest, by design.
FLURRY_S = float(os.environ.get("GLADOS_XCHECK_FLURRY_S", "20"))


def _die(outcome, detail, state=None):
    _emit({"outcome": outcome, "detail": detail}, state)
    sys.exit(3 if outcome == "COULD_NOT_MEASURE" else 1)


def _emit(record, state=None):
    record.setdefault("at", datetime.now(timezone.utc).isoformat())
    line = json.dumps(record, sort_keys=True)
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        print("warning: could not append to %s: %s" % (LOG, exc), file=sys.stderr)
    if state is not None:
        try:
            tmp = STATE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(state, fh, sort_keys=True)
            os.replace(tmp, STATE)
        except OSError as exc:
            print("warning: could not write %s: %s" % (STATE, exc), file=sys.stderr)
    print("%s: %s" % (record["outcome"], record.get("detail", "")))


def _read_env(path):
    """base URL and token out of the ingest's own env file. Never printed."""
    base = token = None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            val = val.strip().strip('"').strip("'")
            key = key.strip().upper()
            if "TOKEN" in key:
                token = val
            elif "URL" in key or "BASE" in key or val.startswith("http"):
                base = val
    return (base or "").rstrip("/"), token


def _get_json(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _transitions(base, token, entities, since, until):
    """Every off->on transition HA recorded in [since, until], with timestamps.

    Counted the same way the ingest counts: a transition INTO "on". A sensor
    that clears is not a second knock. The first point of a series is the state
    at the window start, synthesised rather than observed, so it seeds the
    remembered state and is never an event on its own - the same rule that
    stops a restart inventing a knock for a sensor that is already `on`.
    """
    query = urllib.parse.urlencode({
        "end_time": until.isoformat(),
        "filter_entity_id": ",".join(entities),
        "minimal_response": "",
        "no_attributes": "",
    })
    url = "%s/api/history/period/%s?%s" % (base, urllib.parse.quote(since.isoformat()), query)
    series_list = _get_json(url, {"Authorization": "Bearer " + token})
    out = []
    for series in series_list or ():
        if not series:
            continue
        eid = series[0].get("entity_id") or ""
        prev = None
        for point in series:
            state = point.get("state")
            if state == "on" and prev is not None and prev != "on":
                raw = point.get("last_changed") or point.get("last_updated")
                try:
                    when = datetime.fromisoformat(raw)
                except (TypeError, ValueError):
                    when = None
                out.append({"entity": eid, "at": raw, "ts": when})
            prev = state
    out.sort(key=lambda t: t["ts"] or datetime.min.replace(tzinfo=timezone.utc))
    return out


def _clustered(missing, every):
    """Were the uncounted transitions inside another transition's shadow?

    True means the ingest was allowed to collapse them: a second press within
    HA's visibility lag arrives as part of the same window and reads as one
    event. False means a solitary knock went missing, which is the real defect.
    """
    if not missing:
        return True
    stamps = sorted(t["ts"] for t in every if t["ts"])
    for miss in missing:
        when = miss["ts"]
        if when is None:
            return False
        near = [s for s in stamps
                if s is not when and abs((s - when).total_seconds()) <= FLURRY_S]
        if not near:
            return False
    return True


def main():
    try:
        health = _get_json(HEALTH, timeout=15)
    except Exception as exc:  # noqa: BLE001
        _die("COULD_NOT_MEASURE", "/health unreachable: %s: %s" % (type(exc).__name__, exc))
    ingest = (health or {}).get("ha_ingest") or {}
    if not ingest.get("enabled"):
        _die("COULD_NOT_MEASURE", "the HA ingest is not enabled on this service")
    counter = int(ingest.get("touch_events") or 0)
    uptime = float(health.get("uptime_s") or 0.0)
    started = datetime.now(timezone.utc) - timedelta(seconds=uptime)

    try:
        config = json.load(open(CONFIG, encoding="utf-8"))
        entities = list(config.get("ha_touch_entities") or ())
        base, token = _read_env(HA_ENV)
    except Exception as exc:  # noqa: BLE001
        _die("COULD_NOT_MEASURE", "config/credential unreadable: %s" % type(exc).__name__)
    if not entities or not base or not token:
        _die("COULD_NOT_MEASURE", "no watched entities or no HA credential")

    state = {}
    if os.path.exists(STATE):
        try:
            state = json.load(open(STATE, encoding="utf-8"))
        except (OSError, ValueError):
            state = {}

    # A restart re-anchors the counter at 0, so a cumulative comparison across
    # it is meaningless. Re-anchor and say so rather than report the drop as a
    # regression.
    prev_uptime = float(state.get("uptime_s") or 0.0)
    restarted = uptime < prev_uptime or counter < int(state.get("counter") or 0)
    if restarted:
        _emit({"outcome": "COULD_NOT_MEASURE",
               "detail": "the service restarted since the last check; re-anchored",
               "counter": counter, "uptime_s": round(uptime, 1)},
              {"uptime_s": uptime, "counter": counter, "strike": 0,
               "anchor": started.isoformat()})
        sys.exit(3)

    # Compare from the service's own start: that is the window the counter is
    # cumulative over, so the two sides measure the same span of time.
    until = datetime.now(timezone.utc)
    try:
        every = _transitions(base, token, entities, started, until)
    except Exception as exc:  # noqa: BLE001
        _die("COULD_NOT_MEASURE",
             "HA history unreachable: %s: %s" % (type(exc).__name__, exc))

    recorded = len(every)
    drift = counter - recorded
    strike = int(state.get("strike") or 0)
    # Entity ids are the user's; the log records how many, never which.
    record = {"counter": counter, "recorded": recorded, "drift": drift,
              "uptime_s": round(uptime, 1), "entities": len(entities),
              "window_min": round((until - started).total_seconds() / 60.0, 1)}

    if drift == 0 and recorded > 0:
        outcome, detail, strike = ("AGREE_FIRED",
                                   "%d real transition(s), all counted by the live "
                                   "service" % recorded, 0)
    elif drift == 0:
        outcome, detail, strike = ("AGREE_QUIET",
                                   "no door events in Home Assistant's recorder "
                                   "either; consistent, and proof of nothing", 0)
    elif drift < 0:
        missing = every[counter:] if counter >= 0 else every
        if _clustered(missing, every):
            outcome, detail, strike = ("UNDERCOUNT",
                                       "%d of %d transitions collapsed inside HA's "
                                       "visibility lag; accepted limit"
                                       % (-drift, recorded), 0)
        else:
            strike += 1
            outcome = "MISMATCH" if strike >= 2 else "AGREE_QUIET"
            detail = ("%d transition(s) in HA that the counter did not see, not a "
                      "flurry (strike %d)" % (-drift, strike))
            if outcome == "AGREE_QUIET":
                outcome = "COULD_NOT_MEASURE"
                detail += "; one more check decides"
    else:
        strike += 1
        outcome = "MISMATCH" if strike >= 2 else "COULD_NOT_MEASURE"
        detail = ("the counter is %d ahead of the recorder (strike %d); a single "
                  "straggler at the window edge is expected and closes itself"
                  % (drift, strike))

    record.update(outcome=outcome, detail=detail)
    _emit(record, {"uptime_s": uptime, "counter": counter, "strike": strike,
                   "anchor": started.isoformat()})
    sys.exit(0 if outcome in ("AGREE_FIRED", "AGREE_QUIET", "UNDERCOUNT")
             else 3 if outcome == "COULD_NOT_MEASURE" else 1)


if __name__ == "__main__":
    main()
