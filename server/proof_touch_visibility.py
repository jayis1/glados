#!/usr/bin/env python3
"""Why the door sense counted nothing, and that the fix counts it (IST-280).

    python3 /opt/paperclip-gladosd/proof_touch_visibility.py [--minutes 720]

Run on the GPU host. Reads Home Assistant; writes nothing to Home Assistant,
nothing to the fly, and nothing to the live service. Safe at any time.

The field symptom was the quietest kind there is: `ha_ingest` reported 11,631
polls, 0 poll errors, 0 post errors, 1,248 successful drive posts, 562 degC of
thermal change - and `touch_events: 0`, while Home Assistant's own recorder
held six `off -> on` transitions on watched sensors over the same twelve hours.
Nothing was broken. The sense simply never fired, and a sense that never fires
is indistinguishable from a quiet house.

Two measurements, in the order that matters:

1. VISIBILITY LAG. How long after a state change does /api/history/period
   admit it exists? Measured against a sensor that moves on its own, with
   three outcomes per sample and not two: seen after N seconds, NOT seen
   within the budget, or could-not-measure. An instrument that cannot say "I
   failed to look" reports a failure to look as a zero.

2. REPLAY. The real transitions from HA's recorder, pushed through a faithful
   cursor walk at the measured lag, counted both the old way (no overlap, skip
   the first point of every window) and the new way, which is run as the
   deployed `ha_afferent._count` rather than as a paraphrase of it. Ground
   truth is HA's own history.

The old rule is correct only if every event is visible before the cursor passes
it. It is not: the cursor advances to "now" on every poll, so an event that
commits late is never inside a window again - it reappears only as the next
window's baseline point, which the old rule skipped by design, because counting
a baseline invents a knock on every restart for any sensor that happens to be
`on`. Both halves of that were right; together they dropped everything.

The fix is an overlap wider than the worst measured lag, plus a dedupe by each
point's own timestamp so the overlap cannot re-ring a doorbell or re-sum a
degree, plus a remembered per-entity state so a baseline point is informative
without being a knock on its own.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/opt/paperclip-gladosd")
import ha_afferent  # noqa: E402

CFG_PATH = "/opt/paperclip-gladosd/config.json"
OUT = "/opt/paperclip-glados/measurements/raw/ha_touch_visibility.json"
POLL_S = 5.0            # the live ha_poll_s
LAG_BUDGET_S = 15.0     # how long we will wait for history to admit a change


def load():
    cfg = json.load(open(CFG_PATH))
    base, tok, err = ha_afferent.load_env(
        cfg.get("ha_env_path", "/opt/paperclip-glados/secrets/ha.env"))
    if err:
        sys.exit("cannot read HA credentials: %s" % err)
    return cfg, base.rstrip("/"), tok


def api(base, tok, path, timeout=20):
    req = urllib.request.Request(base + path,
                                 headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def history(base, tok, entities, since, timeout=20):
    q = urllib.parse.urlencode({"filter_entity_id": ",".join(entities),
                                "minimal_response": "", "no_attributes": ""})
    return api(base, tok, "/api/history/period/%s?%s"
               % (urllib.parse.quote(since), q), timeout)


def ts(point):
    return point.get("last_changed") or point.get("last_updated") or ""


# ---------------------------------------------------------------------------
# 1. visibility lag
# ---------------------------------------------------------------------------
def measure_lag(base, tok, samples=6, budget=LAG_BUDGET_S):
    """Time from a state change to history admitting it. Three outcomes."""
    # A sensor that moves on its own, so nothing has to be poked. Picked by
    # freshness rather than by name, because naming one hard-codes this house.
    now = datetime.now(timezone.utc)
    best, pick = 1e9, None
    for state in api(base, tok, "/api/states"):
        eid = state.get("entity_id", "")
        if not eid.startswith("sensor."):
            continue
        try:
            age = (now - datetime.fromisoformat(state["last_updated"])).total_seconds()
        except Exception:                                    # noqa: BLE001
            continue
        try:
            float(state.get("state"))
        except (TypeError, ValueError):
            continue
        if 0 <= age < best:
            best, pick = age, eid
    if pick is None:
        return {"outcome": "could_not_measure",
                "why": "no numeric sensor with a recent update"}

    # The raw file is published, so it carries a description of the sensor
    # rather than its id: an entity id names a specific house. stdout keeps the
    # real one, because the person running this is standing in that house.
    print("   sampling: %s" % pick)
    out = {"entity": "a numeric sensor that updates on its own",
           "budget_s": budget, "samples": []}
    prev = None
    deadline = time.time() + 240
    while time.time() < deadline and len(out["samples"]) < samples:
        try:
            cur = api(base, tok, "/api/states/" + pick)["last_updated"]
        except Exception as exc:                             # noqa: BLE001
            out["samples"].append({"outcome": "could_not_measure",
                                   "why": "%s: %s" % (type(exc).__name__, exc)})
            break
        if prev is None:
            prev = cur
            continue
        if cur == prev:
            time.sleep(0.4)
            continue
        changed = datetime.fromisoformat(cur)
        since = (changed - timedelta(seconds=1)).isoformat()
        t0, lag = time.time(), None
        while time.time() - t0 < budget:
            try:
                series = history(base, tok, [pick], since)
            except Exception as exc:                         # noqa: BLE001
                lag = "could_not_measure: %s" % type(exc).__name__
                break
            points = series[0] if series else []
            # The baseline point is clamped to `since`; a real sighting of this
            # change carries its own timestamp.
            if any(ts(p)[:23] == cur[:23] for p in points[1:]):
                lag = round(time.time() - t0, 2)
                break
            time.sleep(0.25)
        if isinstance(lag, float):
            out["samples"].append({"outcome": "seen", "after_s": lag,
                                   "changed_at": cur})
        elif lag is None:
            out["samples"].append({"outcome": "not_seen_within_budget",
                                   "changed_at": cur})
        else:
            out["samples"].append({"outcome": "could_not_measure", "why": lag})
        prev = cur
    seen = [s["after_s"] for s in out["samples"] if s["outcome"] == "seen"]
    out["seen"] = len(seen)
    out["not_seen_within_budget"] = sum(
        1 for s in out["samples"] if s["outcome"] == "not_seen_within_budget")
    out["could_not_measure"] = sum(
        1 for s in out["samples"] if s["outcome"] == "could_not_measure")
    out["max_seen_s"] = max(seen) if seen else None
    out["exceeds_poll_interval"] = bool(
        out["not_seen_within_budget"] or (seen and max(seen) > POLL_S))
    out["poll_interval_s"] = POLL_S
    return out


# ---------------------------------------------------------------------------
# 2. replay
# ---------------------------------------------------------------------------
def count_old(windows):
    """The rule that shipped: skip the first point of every window."""
    events = 0
    for points in windows:
        if not points:
            continue
        prev = points[0]
        for state in points[1:]:
            if state == "on" and prev != "on":
                events += 1
            prev = state
    return events


def windows_for(eid, truth, start, end, lag_s, overlap_s):
    """Faithful cursor walk: what Home Assistant would have returned, per poll.

    `truth` is [(datetime, state)] from HA, oldest first. At a poll at time T
    with cursor C, /api/history/period over [C - overlap_s, T] returns the
    state as of the window start as one baseline point - timestamped at the
    window start, not when it really changed, which is the detail that made
    position untrustworthy - followed by every change in the window that has
    become visible, i.e. whose timestamp is at most T - lag_s. The cursor then
    advances to T whether or not anything was visible, which is the defect.
    """
    out, cursor = [], start
    while cursor < end:
        poll_at = cursor + timedelta(seconds=POLL_S)
        visible_to = poll_at - timedelta(seconds=lag_s)
        since = cursor - timedelta(seconds=overlap_s)
        baseline = None
        for when, state in truth:
            if when <= since and when <= visible_to:
                baseline = state
        points = []
        if baseline is not None:
            points.append({"entity_id": eid, "state": baseline,
                           "last_changed": since.isoformat()})
        for when, state in truth:
            if since < when <= poll_at and when <= visible_to:
                points.append({"entity_id": eid, "state": state,
                               "last_changed": when.isoformat()})
        if points:
            out.append(points)
        cursor = poll_at
    return out


def count_new(eid, truth, start, end, lag_s, overlap_s, touch, thermo):
    """The deployed rule, run as the deployed code, not as a paraphrase."""
    ha_afferent._last_touch.clear()
    ha_afferent._last_seen_ts.clear()
    ha_afferent._last_temp.clear()
    events = 0
    for points in windows_for(eid, truth, start, end, lag_s, overlap_s):
        got, _, _ = ha_afferent._count([points], touch, thermo)
        events += got
    return events


def main():
    minutes = 720
    if "--minutes" in sys.argv:
        minutes = int(sys.argv[sys.argv.index("--minutes") + 1])
    cfg, base, tok = load()
    touch = list(cfg.get("ha_touch_entities") or ())
    if not touch:
        sys.exit("no ha_touch_entities configured")

    report = {"issue": "IST-280", "at": datetime.now(timezone.utc).isoformat(),
              "poll_interval_s": POLL_S, "window_minutes": minutes,
              "touch_entity_count": len(touch)}

    print("1. how long does Home Assistant take to admit a change happened?")
    lag = measure_lag(base, tok)
    report["visibility_lag"] = lag
    print("   entity sampled: %s" % lag.get("entity"))
    for s in lag.get("samples", []):
        print("   %-24s %s" % (s["outcome"], s.get("after_s", s.get("why", ""))))
    print("   seen=%s  not_seen_within_%ss=%s  could_not_measure=%s  max=%s s"
          % (lag.get("seen"), lag.get("budget_s"),
             lag.get("not_seen_within_budget"), lag.get("could_not_measure"),
             lag.get("max_seen_s")))
    print("   exceeds the %.0f s poll interval: %s"
          % (POLL_S, lag.get("exceeds_poll_interval")))

    print()
    print("2. replay: the real transitions, both counting rules")
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=minutes)
    series_list = history(base, tok, touch, start.isoformat())
    # The lag to replay at: the largest we actually saw, floored at one poll
    # interval so the replay is never gentler than the measurement.
    seen_max = lag.get("max_seen_s") or 0.0
    if lag.get("not_seen_within_budget"):
        seen_max = max(seen_max, lag.get("budget_s", LAG_BUDGET_S))
    lag_s = max(POLL_S + 0.5, seen_max)
    overlap_s = float(cfg.get("ha_overlap_s", 60.0))
    thermo_entities = list(cfg.get("ha_thermo_entities") or ())
    report["replay_lag_s"] = lag_s
    report["overlap_s"] = overlap_s
    report["entities"] = []
    total_truth = total_old = total_new = 0
    for series in series_list or ():
        if not series:
            continue
        eid = series[0].get("entity_id") or ""
        if eid not in set(touch):
            continue
        truth = []
        for point in series:
            when = ts(point)
            try:
                truth.append((datetime.fromisoformat(when), point.get("state")))
            except Exception:                                # noqa: BLE001
                continue
        truth.sort()
        real = sum(1 for i, (_, s) in enumerate(truth)
                   if i > 0 and s == "on" and truth[i - 1][1] != "on")
        # The old rule as it shipped: no overlap, and skip the first point.
        old = count_old([[p.get("state") for p in w]
                         for w in windows_for(eid, truth, start, end,
                                              lag_s, 0.0)])
        new = count_new(eid, truth, start, end, lag_s, overlap_s,
                        set(touch), set(thermo_entities))
        total_truth += real
        total_old += old
        total_new += new
        row = {"entity": "touch[%d]" % touch.index(eid), "recorded_on_transitions": real,
               "counted_old_rule": old, "counted_new_rule": new,
               "polls_replayed": int((end - start).total_seconds() // POLL_S)}
        report["entities"].append(row)
        if real or old or new:
            print("   %-58s truth=%d  old=%d  new=%d"
                  % (eid.split(".", 1)[-1][:58], real, old, new))
    report["totals"] = {"recorded_on_transitions": total_truth,
                        "counted_old_rule": total_old,
                        "counted_new_rule": total_new}
    print("   TOTAL over %d min, %.1f s lag, %.0f s overlap: truth=%d old=%d new=%d"
          % (minutes, lag_s, overlap_s, total_truth, total_old, total_new))

    report["verdict"] = (
        "no transitions recorded in the window - nothing to prove either way"
        if total_truth == 0 else
        "old rule lost %d of %d; new rule counts %d"
        % (total_truth - total_old, total_truth, total_new))
    print()
    print("   " + report["verdict"])

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump(report, fh, indent=2)
    print("   raw: %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
