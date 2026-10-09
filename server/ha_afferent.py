#!/usr/bin/env python3
"""Layer 2b: the house's own senses -> the fly's skin and its thermometer (IST-280).

Layer 2a (afferent.py) gave her ONE sense: her own request traffic, into
hearing_JO. That needed no credential and it works, but it is a weak nerve and
it only moves arousal. This module adds the two senses the plan wanted, now
that the Home Assistant token has been handed over:

    doorbell / motion / occupancy  ->  mechanosensory   (she is touched)
    temperature change             ->  hygro_thermo     (she feels the heat)

MEASURED FIRST, ALWAYS
----------------------
Both sites were probed on the live calibrated service BEFORE anything was
wired to them, because this service already has one dead input: all 4,102
photoreceptors at full drive move no readout at all - the optic paths are not
in the traced subset - so a plausible site name is not evidence.

    measurements/mechanosensory.json   measurements/hygro_thermo.json

    drive          0.02    0.05    0.10    0.20    0.40     best
    mechano arousal 0.074  0.531   0.589   0.775   1.000    213 sd
    mechano agitn   0.722  0.601   0.482   0.108   0.019     11 sd, monotonic
    hygro novelty   0.057  0.085   0.104   0.202   0.293    143 sd, monotonic
    hygro valence   0.017  0.037   0.049   0.212   0.357    173 sd, monotonic
    hygro reinforce 0.000  0.007   0.035   0.257   0.420    218 sd, monotonic
    hygro arousal   0.000  0.000   0.000   0.000   0.000      DEAD on this site

The three nerves turn out to be almost perfectly complementary, which nobody
designed and the plan got wrong in an interesting way. IST-280 predicted that
mechanosensory was "the axis that would drive novelty, valence and
reinforcement properly". It is not: reinforcement is EXACTLY 0.0000 at every
drive level on mechanosensory. hygro_thermo is the site that drives all three,
and it is the strongest coupling in the fly - 218 sd on the axis that was
previously dead everywhere. Meanwhile arousal is exactly dead on hygro_thermo.
So:

    hearing_JO      arousal, weakly (40 sd)
    mechanosensory  arousal hard (213 sd) + agitation
    hygro_thermo    novelty + valence + reinforcement

Four of the five axes are now driven by something, for the first time.

WHAT DRIVES THE THERMOMETER, AND WHY IT IS NOT THE THERMOSTAT
------------------------------------------------------------
Every room climate sensor in this house currently reports `unavailable` - both
thermostats, their external probes, the living-room sensor and the only
humidity sensor. That is six dead entities, and it is why `ha_thermo_entities`
below names MACHINE temperatures instead. Those are live and they genuinely
move (1.0-3.7 degC mean step, tens of changes an hour), so her thermal sense is
the heat coming off the hardware she runs on, which is a stand-in and is named
as one here rather than dressed up as room temperature. If the thermostats come
back, put them at the front of that list and delete nothing else.

RULES IT MUST NOT BREAK (all four are scars; see afferent.py)
-------------------------------------------------------------
1. NEVER on the request path. Nothing in this module is called from a request.
   It is a background thread that polls Home Assistant and posts a drive. A
   dead HA, a slow HA, a wrong token - none of them can add a millisecond to
   one of her replies, or stop one.
2. NEVER write a site we do not own. _post_drive posts ONLY mechanosensory and
   hygro_thermo, so afferent.py's hearing_JO is untouched and the two ingests
   coexist on one drive vector instead of fighting over it.
3. ALWAYS hand her back at rest - and post zero on START, not only on exit.
   systemd stops this service with SIGTERM, and SIGTERM does not run atexit, so
   an exit-only release leaves the fly permanently stimulated with no way for
   the next reader to know why. Posting zero at startup is the guarantee that
   survives being killed.
4. FAIL OPEN, loudly in the log and silently to the caller. Every failure path
   records itself in health() and then does nothing else.
"""

import atexit
import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection

TOUCH_SITE = "mechanosensory"
THERMO_SITE = "hygro_thermo"

# The sites this module owns. Nothing else may be written, ever.
OWNED = (TOUCH_SITE, THERMO_SITE)

_lock = threading.Lock()
_level = {TOUCH_SITE: 0.0, THERMO_SITE: 0.0}
_posted = {TOUCH_SITE: None, THERMO_SITE: None}
_last_temp = {}            # entity_id -> last numeric value seen
_last_touch = {}           # entity_id -> last state seen, across polls
_last_seen_ts = {}         # entity_id -> timestamp of the last point consumed
_cursor = None             # ISO timestamp: history has been read up to here
_thread = None
_stop = threading.Event()  # deliberately not named _stop on a Thread subclass
_stats = {
    "polls": 0, "poll_errors": 0, "last_poll_at": None, "last_error": None,
    "touch_events": 0, "thermo_degc": 0.0, "posts": 0, "post_errors": 0,
    "last_touch_at": None, "entities_seen": 0,
}


# ---------------------------------------------------------------------------
# credentials
# ---------------------------------------------------------------------------
def load_env(path):
    """Read HA_BASE_URL / HA_TOKEN out of a 0600 env file.

    Returns (base_url, token, error). The token is never logged, never put in
    health(), and never interpolated into anything that gets printed.
    """
    base = tok = None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                val = val.strip().strip("'").strip('"')
                if key.strip() == "HA_BASE_URL":
                    base = val.rstrip("/")
                elif key.strip() == "HA_TOKEN":
                    tok = val
    except OSError as exc:
        return None, None, "env: %s" % exc
    if not base or not tok:
        return None, None, "env: HA_BASE_URL or HA_TOKEN missing"
    return base, tok, None


# ---------------------------------------------------------------------------
# Home Assistant
# ---------------------------------------------------------------------------
def _history(base, tok, entities, since, timeout):
    """All recorded state changes for `entities` since `since`.

    /api/history/period is the right endpoint rather than polling /api/states:
    it returns every transition in the window, so a doorbell that goes on and
    off again between two polls is still counted. Polling current state would
    silently miss exactly the events this sense exists for.
    """
    q = urllib.parse.urlencode({
        "filter_entity_id": ",".join(entities),
        "minimal_response": "",
        "no_attributes": "",
    })
    url = "%s/api/history/period/%s?%s" % (base, urllib.parse.quote(since), q)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _point_time(point):
    """The timestamp of one history point, or None if it has none we can read.

    None means "process this point": the failure this whole function exists to
    stop is a dropped knock, so an unreadable timestamp must fall towards
    counting rather than towards silence.
    """
    raw = point.get("last_changed") or point.get("last_updated")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def _fresh(eid, series):
    """The points of `series` not already consumed on an earlier poll.

    Windows overlap on purpose (see `ha_overlap_s`), so the same change is
    handed to us several times and must be counted once. Dedupe is by the
    point's own timestamp rather than by its position: position is exactly what
    could not be trusted. The baseline point HA synthesises at the start of a
    window carries the window start as its timestamp, so on an overlapping
    window it is older than what we have already seen and drops out here.
    """
    last = _last_seen_ts.get(eid)
    out, newest = [], last
    for point in series:
        when = _point_time(point)
        if when is not None and last is not None and when <= last:
            continue
        out.append(point)
        if when is not None and (newest is None or when > newest):
            newest = when
    if newest is not None:
        _last_seen_ts[eid] = newest
    return out


def _count(series_list, touch_set, thermo_set):
    """Transitions -> (touch events, aggregate degC of thermal change).

    A touch event is a transition INTO "on": a motion sensor that clears is not
    a second knock. Thermal change is the absolute step summed across the
    watched sensors, because a fly's thermoreceptors answer to change rather
    than to absolute temperature, and because 52 degC forever is not a stimulus.
    """
    events = 0
    degc = 0.0
    seen = 0
    for raw_series in series_list or ():
        if not raw_series:
            continue
        eid = raw_series[0].get("entity_id") or ""
        seen += 1
        series = _fresh(eid, raw_series)
        if not series:
            continue
        if eid in touch_set:
            # Compare against the state remembered from the LAST poll, not
            # against the first point of this window.
            #
            # The first point of a series is the state at the start of the
            # window, synthesised by HA rather than observed inside it, so it
            # must not be counted on its own: that would invent a knock on
            # every restart for any sensor that happened to be `on`, which for
            # an indoor occupancy sensor is most of the day.
            #
            # But skipping it outright loses real events, and in the field it
            # lost ALL of them. /api/history/period makes a change visible
            # anywhere from 0 to over 12 seconds after it happened (measured;
            # measurements/raw/ha_history_visibility_lag.json), while the
            # cursor advances to "now" every 5 s. An event that commits after
            # the cursor has passed it is never inside a window again - it only
            # ever reappears as a baseline point. Result: 11,436 error-free
            # polls, three real transitions in Home Assistant's own recorder,
            # and touch_events = 0.
            #
            # Remembering the state per entity instead makes the baseline point
            # informative: `on` against a remembered `off` is the event we
            # missed, counted exactly once, however late it arrives. An entity
            # seen for the first time seeds silently, which is what keeps the
            # restart case honest. This is how the thermometer channel below
            # has always worked, and it is why that one never lost a reading.
            prev = _last_touch.get(eid)
            for point in series:
                state = point.get("state")
                if state == "on" and prev is not None and prev != "on":
                    events += 1
                prev = state
            _last_touch[eid] = prev
        elif eid in thermo_set:
            for point in series:
                try:
                    val = float(point.get("state"))
                except (TypeError, ValueError):
                    continue          # unavailable / unknown: not a reading
                last = _last_temp.get(eid)
                if last is not None:
                    degc += abs(val - last)
                _last_temp[eid] = val
    return events, degc, seen


# ---------------------------------------------------------------------------
# the fly
# ---------------------------------------------------------------------------
def _post_drive(cfg, values):
    """POST /drive with ONLY the sites this module owns.

    moodd's set_drive updates named sites and leaves the rest alone, which is
    what lets this ingest and afferent.py's hearing_JO share one drive vector.
    Filtering here as well is belt and braces: a typo in config must not be
    able to reach hearing_JO.
    """
    body = {k: round(float(v), 5) for k, v in values.items() if k in OWNED}
    if not body:
        return "nothing owned to post"
    raw = json.dumps(body).encode()
    headers = {"Content-Type": "application/json", "Content-Length": str(len(raw))}
    try:
        with open(cfg.get("mood_token_path", "/opt/paperclip-glados/token")) as fh:
            tok = fh.read().strip()
        if tok:
            headers["Authorization"] = "Bearer " + tok
    except OSError as exc:
        return "token: %s" % exc
    try:
        conn = HTTPConnection(cfg.get("mood_url", "127.0.0.1:9099"),
                              timeout=float(cfg.get("ha_post_timeout_s", 2.0)))
        conn.request("POST", "/drive", body=raw, headers=headers)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        if resp.status != 200:
            return "http_%d: %s" % (resp.status,
                                    (payload or b"")[:120].decode("utf-8", "replace"))
        return None
    except Exception as exc:  # noqa: BLE001 - the ingest degrades, never raises
        return "%s: %s" % (type(exc).__name__, exc)


def _decay(site, dt, tau, add, cap, floor):
    """One leaky-integrator step for one site. Returns the new level."""
    level = _level[site] * math.exp(-dt / max(1.0, tau)) + add
    if level < floor:
        level = 0.0
    _level[site] = min(cap, level)
    return _level[site]


def _changed(site, epsilon):
    """Post only on a real change, and always post the return to zero."""
    now, before = _level[site], _posted[site]
    if before is None:
        return True
    if now == 0.0 and before != 0.0:
        return True
    return abs(now - before) >= epsilon


# ---------------------------------------------------------------------------
# the loop
# ---------------------------------------------------------------------------
def _poll_once(cfg, base, tok, dt):
    """One poll + one integrator step for both channels. Never raises."""
    global _cursor
    touch = list(cfg.get("ha_touch_entities") or ())
    thermo = list(cfg.get("ha_thermo_entities") or ())
    watched = touch + thermo
    events, degc = 0, 0.0

    if watched:
        # Windows OVERLAP, by more than Home Assistant's worst measured delay
        # in admitting that a change happened (5.4 to 13.8 s on this install,
        # every sample longer than the 5 s poll interval:
        # measurements/raw/ha_touch_visibility.json). Without the overlap, an
        # event that commits after the cursor has passed it is never inside a
        # window again, and the door sense counted 0 of 6 real transitions in
        # twelve hours. The overlap is only safe because _fresh() dedupes by
        # each point's own timestamp; re-reading a window without that would
        # re-ring every doorbell press and re-sum every degree.
        overlap = timedelta(seconds=float(cfg.get("ha_overlap_s", 60.0)))
        if _cursor:
            since = (datetime.fromisoformat(_cursor) - overlap).isoformat()
        else:
            since = (datetime.now(timezone.utc)
                     - timedelta(seconds=float(cfg.get("ha_backfill_s", 30)))
                     ).isoformat()
        # Advance the cursor BEFORE counting, and to "now" rather than to the
        # newest point we saw: a window that ended at the last event would keep
        # that event as its own baseline for as long as nothing else happened.
        next_cursor = datetime.now(timezone.utc).isoformat()
        try:
            series = _history(base, tok, watched, since,
                              float(cfg.get("ha_timeout_s", 10.0)))
            events, degc, seen = _count(series, set(touch), set(thermo))
            _cursor = next_cursor
            _stats["polls"] += 1
            _stats["entities_seen"] = seen
            _stats["last_poll_at"] = round(time.time(), 1)
            _stats["last_error"] = None
        except Exception as exc:  # noqa: BLE001
            _stats["poll_errors"] += 1
            _stats["last_error"] = "%s: %s" % (type(exc).__name__, exc)
            # Deliberately do NOT advance the cursor: the next poll re-reads
            # the same window, so an HA blip delays events rather than eating
            # them. Decay still runs below, so she calms down during an
            # outage instead of freezing at her last level.

    if events:
        _stats["touch_events"] += events
        _stats["last_touch_at"] = round(time.time(), 1)
    if degc:
        _stats["thermo_degc"] = round(_stats["thermo_degc"] + degc, 3)

    floor = float(cfg.get("ha_floor", 0.002))
    eps = float(cfg.get("ha_epsilon", 0.005))
    _decay(TOUCH_SITE, dt,
           float(cfg.get("ha_touch_tau_s", 120.0)),
           events * float(cfg.get("ha_touch_kick", 0.02)),
           float(cfg.get("ha_touch_max", 0.20)), floor)
    _decay(THERMO_SITE, dt,
           float(cfg.get("ha_thermo_tau_s", 180.0)),
           degc * float(cfg.get("ha_thermo_kick", 0.01)),
           float(cfg.get("ha_thermo_max", 0.25)), floor)

    out = {}
    for site in OWNED:
        if _changed(site, eps):
            out[site] = _level[site]
    if not out:
        return
    err = _post_drive(cfg, out)
    if err:
        _stats["post_errors"] += 1
        _stats["last_error"] = err
    else:
        _stats["posts"] += 1
        for site, value in out.items():
            _posted[site] = value


def _run(cfg, base, tok):
    period = float(cfg.get("ha_poll_s", 5.0))
    last = time.time()
    while not _stop.wait(period):
        now = time.time()
        dt, last = now - last, now
        try:
            _poll_once(cfg, base, tok, dt)
        except Exception as exc:  # noqa: BLE001 - the thread must not die
            _stats["poll_errors"] += 1
            _stats["last_error"] = "%s: %s" % (type(exc).__name__, exc)


def start(cfg):
    """Start the HA ingest. Returns a short status string for the startup log.

    Independent of mood_coupling on purpose: the fly should hold a real state
    whether or not anything is currently reading it.
    """
    global _thread
    if not cfg.get("ha_ingest"):
        return "disabled"
    if _thread is not None:
        return "already running"
    base, tok, err = load_env(cfg.get("ha_env_path",
                                      "/opt/paperclip-glados/secrets/ha.env"))
    if err:
        _stats["last_error"] = err
        return err
    # Rule 3: zero on START. systemd's SIGTERM does not run atexit, so this is
    # the only release that is guaranteed to happen.
    zeroed = _post_drive(cfg, {s: 0.0 for s in OWNED})
    if zeroed:
        _stats["post_errors"] += 1
        _stats["last_error"] = zeroed
    else:
        for site in OWNED:
            _posted[site] = 0.0
    _stop.clear()
    _thread = threading.Thread(target=_run, args=(cfg, base, tok), daemon=True,
                               name="ha-afferent")
    _thread.start()
    atexit.register(release, cfg)
    return "running"


def release(cfg):
    """Hand both owned sites back to zero. Safe to call more than once."""
    _stop.set()
    try:
        if any(_posted[s] not in (None, 0.0) for s in OWNED):
            _post_drive(cfg, {s: 0.0 for s in OWNED})
            for site in OWNED:
                _posted[site] = 0.0
    except Exception:  # noqa: BLE001
        pass


def health(cfg):
    """The ha_ingest block for gladosd's /health. Never contains the token."""
    out = {
        "enabled": bool(cfg.get("ha_ingest")),
        "running": bool(_thread and _thread.is_alive()),
        "sites": {
            TOUCH_SITE: {"level": round(_level[TOUCH_SITE], 4),
                         "posted": _posted[TOUCH_SITE],
                         "tau_s": cfg.get("ha_touch_tau_s", 120.0),
                         "kick": cfg.get("ha_touch_kick", 0.02),
                         "max": cfg.get("ha_touch_max", 0.20),
                         "entities": len(cfg.get("ha_touch_entities") or ())},
            THERMO_SITE: {"level": round(_level[THERMO_SITE], 4),
                          "posted": _posted[THERMO_SITE],
                          "tau_s": cfg.get("ha_thermo_tau_s", 180.0),
                          "kick_per_degc": cfg.get("ha_thermo_kick", 0.01),
                          "max": cfg.get("ha_thermo_max", 0.25),
                          "entities": len(cfg.get("ha_thermo_entities") or ())},
        },
        "cursor": _cursor,
    }
    out.update(_stats)
    return out
