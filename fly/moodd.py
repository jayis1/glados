#!/usr/bin/env python3
"""GLaDOS mood bus (IST-269 Task 2).

Runs the connectome simulation of engine.py continuously on the two T400s and
publishes its population activity as a small named state vector - GLaDOS's
mood. Same service shape as this host's paperclip-ttsd / paperclip-sttd:
systemd unit, bearer token from a file, unauthenticated /health, and the live
engine named in the startup banner.

    . env.sh        # REQUIRED - see engine.py
    python3 moodd.py

    GET  /health            unauthenticated, 200 only when the sim is stepping
    GET  /mood              the vector + the raw Hz it came from
    GET  /describe          the engine's own description of the loaded graph
    POST /drive             {"olfactory_ORN": 0.05, ...} sensory injection
    POST /reset             membrane state back to zero

WHAT THE VECTOR IS
------------------
Five readout populations, each a mean firing rate in Hz, mapped onto a named
axis. The axis names are the ticket's; the *scales* are not - every lo/hi in
mood_calibration.json is a rate this simulation was measured producing (see
tune.py respond and MOOD.md). A priori scaling is how you get a vector that is
pinned at 0.0 or 1.0 and still looks like a working service.

  arousal        central_complex   2,950 neurons
  novelty        kenyon_cell       4,064
  valence        MBON                 97   signed, -1 avoid .. +1 approach
  reinforcement  DAN                 340
  agitation      escape_GF            34

Normalised axes are clipped to [0, 1] (valence to [-1, 1]) but the raw Hz is
always in the response next to them, so a clipped axis is visible rather than
silently saturated. `saturated` and `silent` are computed against the
refractory ceiling and reported in /health: this network's two known failure
modes are a constant maximum and a constant zero, and both are invisible from
the vector alone.

THREADING
---------
engine.Connectome is not thread-safe and CUDA contexts are bound to the thread
that made them, so exactly one thread ever touches it: the sim thread owns the
simulation and the HTTP handlers only ever read the last published snapshot or
push a command onto a queue. That also keeps the step loop free of the GIL
contention a per-request sim call would add - a request must never be able to
stall the network's clock.

DEGRADATION
-----------
No CUDA, no T400, or a kernel that will not compile -> the service stays up,
/health returns 503 with the reason and /mood returns 503. It does *not* fall
back to the CPU: measured 352 ms/step, 0.003x realtime, which would publish a
mood vector minutes behind the stimulus that caused it. A wrong mood is worse
than a missing one. systemd Restart=always would also hide the reason in a
crash loop, so startup failure is a served error, not an exit.
"""
import argparse
import collections
import hmac
import json
import math
import os
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PORT = 9099
DEFAULT_CALIBRATION = os.path.join(HERE, "data", "mood_calibration.json")
MAX_BODY = 64 * 1024

STATE = {
    "ready": False,
    "error": None,
    "engine": "connectome-lif",
    "started": time.time(),
    "describe": None,
    "snapshot": None,
    "steps": 0,
    "ms_step": None,
    "realtime": None,
}
LOCK = threading.Lock()
COMMANDS = collections.deque()


def log(event, **kw):
    kw["event"] = event
    kw["t"] = round(time.time(), 3)
    print(json.dumps(kw, default=str), flush=True)


# --------------------------------------------------------------------------
# calibration
# --------------------------------------------------------------------------
def load_calibration(path):
    """Axis definitions, measured. Absent file is fatal on purpose.

    Guessing a range here is the one thing that would make this service lie
    convincingly, so there is no built-in default: if the calibration is
    missing the service reports why and serves 503s.
    """
    with open(path, "r", encoding="utf-8") as fh:
        cal = json.load(fh)
    for ax in cal["axes"]:
        for k in ("name", "population", "lo_hz", "hi_hz"):
            if k not in ax:
                raise ValueError(f"axis {ax.get('name')!r} missing {k}")
        if ax["hi_hz"] <= ax["lo_hz"]:
            raise ValueError(f"axis {ax['name']}: hi_hz <= lo_hz")
    return cal


def project(cal, rates):
    """Rates in Hz -> the named vector.

    A `signed` axis is centred on its measured baseline and reports -1..+1, so
    0.0 means "no opinion" rather than "minimum"; an unsigned axis reports
    0..1 over its measured range.

    `scale: "log"` axes are scaled in log-rate, because this network's readouts
    respond to drive by a roughly constant factor per step (kenyon_cell to
    olfactory drive, measured: 4.0 / 8.7 / 19.1 / 56.3 / 94.5 Hz for drive 0 /
    0.01 / 0.02 / 0.05 / 0.1). Linearly scaled, a clear smell reads 0.03.
    """
    out = {}
    for ax in cal["axes"]:
        hz = float(rates.get(ax["population"], 0.0))
        lo, hi = float(ax["lo_hz"]), float(ax["hi_hz"])
        log = ax.get("scale") == "log"
        if ax.get("signed"):
            mid = float(ax.get("mid_hz", (lo + hi) / 2.0))
            if log:
                hz = max(hz, 1e-6)
                up, dn = math.log(hi / mid), math.log(mid / max(lo, 1e-6))
                v = (math.log(hz / mid) / up if hz >= mid
                     else -math.log(mid / hz) / max(dn, 1e-9))
            else:
                span = max(hi - mid, mid - lo, 1e-9)
                v = (hz - mid) / span
            v = max(-1.0, min(1.0, v))
        else:
            if log:
                v = (math.log(max(hz, 1e-6) / max(lo, 1e-6)) /
                     math.log(hi / max(lo, 1e-6)))
            else:
                v = (hz - lo) / (hi - lo)
            v = max(0.0, min(1.0, v))
        out[ax["name"]] = round(v, 4)
    return out


# --------------------------------------------------------------------------
# the sim thread
# --------------------------------------------------------------------------
def sim_loop(args, cal, stop):
    import engine

    devices = (tuple(int(x) for x in args.gpus.replace(",", " ").split())
               if args.gpus else engine.available_devices())
    if not devices:
        raise RuntimeError(
            "no T400 visible; GPU 0 belongs to whisper+Kokoro and the CPU "
            "path measured 352 ms/step, so there is nothing to degrade to")

    sim = engine.Connectome(devices=devices)
    d = sim.describe()
    # One real step before declaring readiness: a cupy device opens and reports
    # its name even when NVRTC cannot find the CUDA headers, and the failure
    # only arrives with the first kernel. See engine.py.
    t0 = time.perf_counter()
    sim.step(args.settle)
    # rates_hz() is what synchronises. Timing sim.step() alone measures kernel
    # *launch* time, not kernel time: cupy queues asynchronously, so a 100-step
    # chunk fits in the queue and would report 0.86 ms/step against a real
    # 4.5 ms - a service claiming 1.16x realtime while running at 0.23x.
    sim.rates_hz()                      # drop the settling window, and sync
    ms_step = (time.perf_counter() - t0) / args.settle * 1000.0

    with LOCK:
        STATE["describe"] = d
        STATE["ms_step"] = round(ms_step, 3)
        STATE["realtime"] = round(engine.DT / ms_step, 4)
        STATE["ready"] = True
    log("ready", engine=STATE["engine"], gpus=d["gpus"], devices=d["devices"],
        exchange=d["exchange"], neurons=d["neurons"], edges=d["edges"],
        ms_step=STATE["ms_step"], realtime=STATE["realtime"],
        settle_steps=args.settle)

    ceiling = d["ceiling_hz"]
    ema = None
    alpha_window = None
    while not stop.is_set():
        while COMMANDS:
            kind, payload = COMMANDS.popleft()
            try:
                if kind == "drive":
                    sim.set_drive(**payload)
                elif kind == "reset":
                    sim.reset()
                    sim.rates_hz()
                    ema = None
                log("command", kind=kind, payload=payload)
            except Exception as exc:                          # noqa: BLE001
                log("command_failed", kind=kind, error=str(exc)[:200])

        wt0 = time.perf_counter()
        sim.step(args.window)
        rates = sim.rates_hz()          # also the sync point; see above
        dt_wall = time.perf_counter() - wt0

        # Smoothing is in *simulation* time, not wall time: the sim runs at
        # ~0.22x realtime and that factor would otherwise silently change the
        # time constant of the mood whenever the step rate changed.
        if alpha_window is None:
            alpha_window = min(1.0, (args.window * engine.DT) /
                               max(args.smooth_ms, args.window * engine.DT))
        if ema is None:
            ema = dict(rates)
        else:
            for k, v in rates.items():
                ema[k] = ema[k] + alpha_window * (v - ema[k])

        net = ema[engine.ALL]
        snap = {
            "t": round(time.time(), 3),
            "sim_ms": round(sim.steps * engine.DT, 1),
            "window_ms": args.window * engine.DT,
            "smooth_ms": args.smooth_ms,
            "mood": project(cal, ema),
            "rates_hz": {k: round(v, 3) for k, v in ema.items()},
            "instant_hz": {k: round(v, 3) for k, v in rates.items()},
            "network_hz": round(net, 3),
            "ceiling_hz": ceiling,
            "saturated": bool(any(ema[k] > 0.7 * ceiling
                                  for k in engine.READOUTS)),
            "silent": bool(net < 0.05),
            "drive": dict(sim.drive),
        }
        with LOCK:
            STATE["snapshot"] = snap
            STATE["steps"] = sim.steps
            STATE["ms_step"] = round(dt_wall / args.window * 1000.0, 3)
            STATE["realtime"] = round(
                engine.DT * args.window / (dt_wall * 1000.0), 4)


def sim_thread(args, cal):
    stop = threading.Event()

    def run():
        try:
            sim_loop(args, cal, stop)
        except Exception as exc:                              # noqa: BLE001
            with LOCK:
                STATE["ready"] = False
                STATE["error"] = str(exc)[:300]
            log("sim_failed", error=str(exc)[:300],
                trace=traceback.format_exc()[-1200:])

    th = threading.Thread(target=run, name="sim", daemon=True)
    th.start()
    return th, stop


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "paperclip-moodd/1.0"

    def log_message(self, fmt, *args):
        log("http", line=fmt % args, path=self.path)

    def _send(self, code, obj):
        raw = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except BrokenPipeError:
            pass

    def _authed(self):
        token = self.server.token
        if not token:
            return True
        got = self.headers.get("Authorization", "")
        if not got.startswith("Bearer "):
            return False
        return hmac.compare_digest(got[7:].strip(), token)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0 or length > MAX_BODY:
            return None, {"error": "bad_body_length", "max": MAX_BODY}
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8", "replace") or "{}")
        except Exception:                                     # noqa: BLE001
            return None, {"error": "bad_json"}
        if not isinstance(payload, dict):
            return None, {"error": "bad_json", "detail": "object expected"}
        return payload, None

    def do_GET(self):
        import urllib.parse
        path = urllib.parse.urlparse(self.path).path
        with LOCK:
            ready, err = STATE["ready"], STATE["error"]
            d, snap = STATE["describe"], STATE["snapshot"]
            ms_step, rt, steps = (STATE["ms_step"], STATE["realtime"],
                                  STATE["steps"])

        if path == "/health":
            # Unauthenticated, mirroring ttsd/sttd: a consumer polls it to
            # decide whether a mood is available at all, and it discloses
            # nothing a LAN peer cannot read off nvidia-smi.
            body = {
                "ok": bool(ready and snap),
                "engine": STATE["engine"],
                "error": err,
                "uptime_s": round(time.time() - STATE["started"], 1),
                "sim_steps": steps,
                "ms_step": ms_step,
                "realtime": rt,
                "axes": [a["name"] for a in self.server.cal["axes"]],
                "calibration": self.server.cal.get("source"),
            }
            if d:
                body.update(gpus=d["gpus"], devices=d["devices"],
                            exchange=d["exchange"], neurons=d["neurons"],
                            edges=d["edges"])
            if snap:
                body.update(saturated=snap["saturated"], silent=snap["silent"],
                            network_hz=snap["network_hz"],
                            age_s=round(time.time() - snap["t"], 3))
            return self._send(200 if body["ok"] else 503, body)

        if not self._authed():
            return self._send(401, {"error": "unauthorized"})

        if path == "/mood":
            if not snap:
                return self._send(503, {"error": "not_ready", "detail": err})
            if not ready:
                # The sim thread died after publishing at least once. Serving
                # the last vector would be serving a mood frozen at whatever
                # the network was doing when CUDA went away.
                return self._send(503, {
                    "error": "sim_stopped", "detail": err,
                    "last_age_s": round(time.time() - snap["t"], 3)})
            out = dict(snap)
            out["age_s"] = round(time.time() - snap["t"], 3)
            out["ms_step"] = ms_step
            out["realtime"] = rt
            return self._send(200, out)

        if path == "/describe":
            if not d:
                return self._send(503, {"error": "not_ready", "detail": err})
            return self._send(200, {"engine": STATE["engine"], "sim": d,
                                    "calibration": self.server.cal})

        self._send(404, {"error": "not_found"})

    def do_POST(self):
        import urllib.parse
        path = urllib.parse.urlparse(self.path).path
        if path not in ("/drive", "/reset"):
            return self._send(404, {"error": "not_found"})
        if not self._authed():
            return self._send(401, {"error": "unauthorized"})
        with LOCK:
            ready, err = STATE["ready"], STATE["error"]
        if not ready:
            return self._send(503, {"error": "not_ready", "detail": err})

        payload, bad = self._body()
        if bad:
            return self._send(400, bad)

        if path == "/reset":
            COMMANDS.append(("reset", {}))
            return self._send(200, {"ok": True, "queued": "reset"})

        import engine
        drive = {}
        for k, v in payload.items():
            if k not in engine.SENSORS:
                # Loud, not ignored: a typo that injected nothing would be
                # indistinguishable from a network that does not respond.
                return self._send(400, {"error": "unknown_population",
                                        "name": k,
                                        "known": list(engine.SENSORS)})
            try:
                f = float(v)
            except (TypeError, ValueError):
                return self._send(400, {"error": "bad_level", "name": k})
            if not 0.0 <= f <= 1.0:
                return self._send(400, {"error": "level_out_of_range",
                                        "name": k, "range": [0.0, 1.0]})
            drive[k] = f
        if not drive:
            return self._send(400, {"error": "empty_drive",
                                    "known": list(engine.SENSORS)})
        COMMANDS.append(("drive", drive))
        self._send(200, {"ok": True, "queued": "drive", "drive": drive})


def read_token(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("MOOD_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("MOOD_PORT", DEFAULT_PORT)))
    ap.add_argument("--gpus", default=os.environ.get("MOOD_GPUS", ""),
                    help="default: every T400 (GPU 0 is the voice stack's)")
    ap.add_argument("--window", type=int,
                    default=int(os.environ.get("MOOD_WINDOW_MS", "100")),
                    help="sim ms per published sample")
    ap.add_argument("--smooth-ms", type=float,
                    default=float(os.environ.get("MOOD_SMOOTH_MS", "600")),
                    help="EMA time constant in simulation ms")
    ap.add_argument("--settle", type=int,
                    default=int(os.environ.get("MOOD_SETTLE_MS", "400")),
                    help="sim ms to run before publishing anything")
    ap.add_argument("--calibration",
                    default=os.environ.get("MOOD_CALIBRATION",
                                           DEFAULT_CALIBRATION))
    ap.add_argument("--token-file",
                    default=os.environ.get("MOOD_TOKEN_FILE",
                                           os.path.join(HERE, "token")))
    args = ap.parse_args()

    try:
        cal = load_calibration(args.calibration)
        cal.setdefault("source", args.calibration)
    except Exception as exc:                                  # noqa: BLE001
        STATE["error"] = f"calibration: {str(exc)[:260]}"
        log("calibration_failed", path=args.calibration,
            error=str(exc)[:300])
        cal = {"axes": [], "source": args.calibration, "error": True}
    else:
        log("calibration", path=args.calibration,
            axes={a["name"]: a["population"] for a in cal["axes"]})
        sim_thread(args, cal)

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.token = read_token(args.token_file)
    srv.cal = cal
    # The banner names the live engine, the cards it is actually on and the
    # graph it actually loaded, because every other surface here is a number
    # that would look plausible against the wrong engine.
    log("listening", engine=STATE["engine"], host=args.host, port=args.port,
        auth="bearer" if srv.token else "none",
        gpus=args.gpus or "auto-T400", window_ms=args.window,
        smooth_ms=args.smooth_ms, ready=STATE["ready"])
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
