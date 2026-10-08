#!/usr/bin/env python3
"""Layer 2a: giving the fly a sense that needs no credential (IST-270).

Layer 2 (mood.py) couples the fly's state into GLaDOS's replies. On its own
that is theatre: nothing drives the connectome, so the vector sits at rest
(arousal 0.000, novelty 0.044, valence 0.010, reinforcement 0.021) and a
constant state line buys nothing. The plan's ingest - doorbell and motion ->
mechanosensory, HA deltas -> hygro_thermo - is blocked on a Home Assistant
token that has to be handed over.

This is the part that is NOT blocked. gladosd's own inbound request traffic is
already an event stream, it needs no credential from anyone, and it has a
natural anatomical home: hearing_JO, the Johnston's organ, which is how a fly
hears. "Someone is talking to me" is exactly what that organ is for. So she
gets to hear her own traffic today, and gains touch and temperature when the
token arrives.

MEASURED, NOT ASSUMED
---------------------
The site was verified before anything was wired to it, because this service
already has one dead input: all 4,102 photoreceptors at full drive move no
readout at all - the optic paths are not in the traced subset - so a plausible
site name is not evidence. probe_hearing.py on the live calibrated service:

    drive   0      0.02   0.05   0.1    0.2    0.4
    arousal 0.000  0.004  0.018  0.057  0.106  0.188     40 sd at the top
    valence +0.010 +0.006 +0.011 +0.012 -0.005 +0.003    drifts negative

Monotonic in arousal, which is what a rate code should look like. The valence
drift is worth naming: under sustained traffic her valence goes slightly
NEGATIVE. Nobody wrote that down as a GLaDOS trait - it is what the connectome
does when its auditory nerve is held on - and it is better than anything we
would have hand-authored.

THE INTEGRATOR
--------------
A request is an instant; a mood is not. So traffic accumulates into a leaky
level that decays with a time constant, rather than setting a drive per
request:

    level <- level * exp(-dt/tau) + kick * (requests since last tick)

tau 90 s and kick 0.06 mean one lone request is barely audible (drive 0.06 ->
arousal ~0.02), a burst of five reads as a busy building (drive 0.3 -> arousal
~0.15, i.e. `alert` after rescaling), and it fades over a few minutes. It is
capped at 0.4; the engine notes ~0.3 saturates a site, and past that we would
be pinning her instead of informing her.

RULES IT MUST NOT BREAK
-----------------------
1. NEVER on the request path. note_request() takes a lock and increments an
   int. All I/O happens on a background thread, so moodd being slow or down can
   never add a millisecond to one of her replies. This is the same reason the
   coupling fails open: GLaDOS was mute for days once already.
2. NEVER write a site we do not own. set_drive() updates only the names it is
   given, so writing hearing_JO leaves mechanosensory alone - the HA ingest can
   land later without the two fighting over one drive vector.
3. ALWAYS hand her back at rest. A probe or a service that exits leaving a
   residual drive leaves her permanently stimulated, and the next reader has no
   way to know why. atexit posts zero.
"""

import atexit
import json
import math
import threading
import time
from http.client import HTTPConnection

SITE_DEFAULT = "hearing_JO"

_lock = threading.Lock()
_pending = 0           # requests seen since the last tick
_level = 0.0           # current drive level
_posted = None         # last value moodd accepted, so we post only on change
_stats = {"requests_sensed": 0, "posts": 0, "post_errors": 0,
          "last_error": None, "last_post_at": None}
_thread = None
_stop = threading.Event()   # NOT _stop as a Thread attribute; see pbx lesson


def note_request(model=None):
    """Called from the request path. Must stay O(1) and never raise."""
    global _pending
    try:
        with _lock:
            _pending += 1
            _stats["requests_sensed"] += 1
    except Exception:  # noqa: BLE001 - a counter must never break inference
        pass


def _post_drive(cfg, value):
    """POST /drive {site: value}. Returns an error string, or None on success."""
    site = cfg.get("ingest_site", SITE_DEFAULT)
    body = json.dumps({site: round(float(value), 5)}).encode()
    headers = {"Content-Type": "application/json",
               "Content-Length": str(len(body))}
    try:
        with open(cfg.get("mood_token_path",
                          "/opt/paperclip-glados/token")) as fh:
            tok = fh.read().strip()
        if tok:
            headers["Authorization"] = "Bearer " + tok
    except OSError as exc:
        return "token: %s" % exc
    try:
        conn = HTTPConnection(cfg.get("mood_url", "127.0.0.1:9099"),
                              timeout=float(cfg.get("ingest_timeout_s", 2.0)))
        conn.request("POST", "/drive", body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        if resp.status != 200:
            return "http_%d: %s" % (resp.status, (raw or b"")[:120].decode(
                "utf-8", "replace"))
        return None
    except Exception as exc:  # noqa: BLE001 - the ingest degrades, never raises
        return "%s: %s" % (type(exc).__name__, exc)


def _tick(cfg, dt):
    """One integrator step. Returns the new level, or None if nothing to post."""
    global _pending, _level, _posted
    tau = max(1.0, float(cfg.get("ingest_tau_s", 90.0)))
    kick = float(cfg.get("ingest_kick", 0.06))
    cap = float(cfg.get("ingest_max", 0.4))
    with _lock:
        n, _pending = _pending, 0
    level = _level * math.exp(-dt / tau) + kick * n
    # Snap to zero rather than decaying asymptotically forever, so an idle
    # service posts 0 once and then goes quiet instead of posting 1e-9.
    if level < float(cfg.get("ingest_floor", 0.002)):
        level = 0.0
    _level = min(cap, level)
    if _posted is None or abs(_level - _posted) >= float(
            cfg.get("ingest_epsilon", 0.005)) or (
            _level == 0.0 and _posted != 0.0):
        return _level
    return None


def _run(cfg):
    period = float(cfg.get("ingest_tick_s", 2.0))
    last = time.time()
    while not _stop.wait(period):
        now = time.time()
        dt, last = now - last, now
        try:
            value = _tick(cfg, dt)
            if value is None:
                continue
            err = _post_drive(cfg, value)
            if err:
                _stats["post_errors"] += 1
                _stats["last_error"] = err
            else:
                globals()["_posted"] = value
                _stats["posts"] += 1
                _stats["last_post_at"] = round(now, 1)
        except Exception as exc:  # noqa: BLE001 - the thread must not die
            _stats["post_errors"] += 1
            _stats["last_error"] = "%s: %s" % (type(exc).__name__, exc)


def start(cfg):
    """Start the ingest thread if enabled. Safe to call once at boot."""
    global _thread
    if not cfg.get("mood_ingest") or _thread is not None:
        return False
    _stop.clear()
    _thread = threading.Thread(target=_run, args=(cfg,), daemon=True,
                               name="afferent")
    _thread.start()
    atexit.register(_release, cfg)
    return True


def _release(cfg):
    """Hand the network back at rest on the way out."""
    _stop.set()
    try:
        if _posted not in (None, 0.0):
            _post_drive(cfg, 0.0)
    except Exception:  # noqa: BLE001
        pass


def health(cfg):
    """The ingest block for gladosd's /health."""
    out = {"enabled": bool(cfg.get("mood_ingest")),
           "site": cfg.get("ingest_site", SITE_DEFAULT),
           "running": bool(_thread and _thread.is_alive()),
           "level": round(_level, 4),
           "posted": _posted,
           "tau_s": cfg.get("ingest_tau_s", 90.0),
           "kick": cfg.get("ingest_kick", 0.06),
           "max": cfg.get("ingest_max", 0.4)}
    out.update(_stats)
    return out
