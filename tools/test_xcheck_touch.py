#!/usr/bin/env python3
"""Every branch of xcheck_touch.py, over real sockets.

AGREE_FIRED is the branch that carries the whole point of the cross-check, and
it is the one branch the field cannot exercise on demand - it needs somebody to
open a door. A verdict that has never been seen to fire is not evidence of
anything, which is the exact mistake that let the door sense ship dead. So the
fired path, the flurry path and both failure paths are all driven here against
a real HTTP server, not mocks.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "xcheck_touch.py")
ENTITIES = ["binary_sensor.probe_one", "binary_sensor.probe_two"]

STATE = {"touch_events": 0, "uptime_s": 600.0, "transitions": [], "enabled": True}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path.startswith("/health"):
            body = {"uptime_s": STATE["uptime_s"],
                    "ha_ingest": {"enabled": STATE["enabled"],
                                  "touch_events": STATE["touch_events"]}}
            return self._send(body)
        if self.path.startswith("/api/history/period/"):
            if self.headers.get("Authorization") != "Bearer test-token":
                self.send_response(401)
                self.end_headers()
                return
            # One series per entity, each led by the window-start baseline point
            # HA synthesises. The baseline must never read as an event.
            start = datetime.now(timezone.utc) - timedelta(seconds=STATE["uptime_s"])
            out = []
            for eid in ENTITIES:
                series = [{"entity_id": eid, "state": "off",
                           "last_changed": start.isoformat()}]
                for offset in STATE["transitions"]:
                    when = start + timedelta(seconds=offset)
                    series.append({"entity_id": eid, "state": "on",
                                   "last_changed": when.isoformat()})
                    series.append({"entity_id": eid, "state": "off",
                                   "last_changed": (when + timedelta(seconds=2)).isoformat()})
                out.append(series)
            # the fixture drives entity one only; entity two stays quiet
            out[1] = [out[1][0]]
            return self._send(out)
        self.send_response(404)
        self.end_headers()

    def _send(self, body):
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def run(tmp, expect_outcome, expect_exit, label, fresh_state=True):
    env = dict(os.environ)
    env.update(GLADOS_HEALTH_URL="http://127.0.0.1:%d/health" % PORT,
               GLADOS_CONFIG=os.path.join(tmp, "config.json"),
               GLADOS_HA_ENV=os.path.join(tmp, "ha.env"),
               GLADOS_XCHECK_STATE=os.path.join(tmp, "state.json"),
               GLADOS_XCHECK_LOG=os.path.join(tmp, "log.jsonl"))
    if fresh_state and os.path.exists(env["GLADOS_XCHECK_STATE"]):
        os.unlink(env["GLADOS_XCHECK_STATE"])
    proc = subprocess.run([sys.executable, SCRIPT], env=env,
                          capture_output=True, text=True, timeout=60)
    got = (proc.stdout.strip().split(":", 1) + [""])[0]
    ok = got == expect_outcome and proc.returncode == expect_exit
    print("%-5s %-28s got %s/%d  want %s/%d"
          % ("PASS" if ok else "FAIL", label, got, proc.returncode,
             expect_outcome, expect_exit))
    if not ok and proc.stderr.strip():
        print("      stderr:", proc.stderr.strip()[:300])
    return ok


def main():
    global PORT
    server = HTTPServer(("127.0.0.1", 0), Handler)
    PORT = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()

    results = []
    with tempfile.TemporaryDirectory() as tmp:
        json.dump({"ha_touch_entities": ENTITIES},
                  open(os.path.join(tmp, "config.json"), "w"))
        with open(os.path.join(tmp, "ha.env"), "w") as fh:
            fh.write("HA_BASE_URL=http://127.0.0.1:%d\nHA_TOKEN=test-token\n" % PORT)

        # Both zero: the state the live service is in, and the state the dead
        # sense was in for sixteen hours. Must not read as proof.
        STATE.update(touch_events=0, transitions=[])
        results.append(run(tmp, "AGREE_QUIET", 0, "both zero"))

        # The proof: real transitions, all counted.
        STATE.update(touch_events=3, transitions=[60, 180, 300])
        results.append(run(tmp, "AGREE_FIRED", 0, "fired and counted"))

        # A flurry: three presses inside the visibility lag, counted once. The
        # accepted limit, explicitly not a defect.
        STATE.update(touch_events=1, transitions=[60, 65, 70])
        results.append(run(tmp, "UNDERCOUNT", 0, "flurry collapsed"))

        # The original bug: solitary knocks, minutes apart, uncounted. First
        # check withholds judgement, second confirms.
        STATE.update(touch_events=0, transitions=[60, 300, 540])
        results.append(run(tmp, "COULD_NOT_MEASURE", 3, "missed knocks, strike 1"))
        results.append(run(tmp, "MISMATCH", 1, "missed knocks, strike 2",
                           fresh_state=False))

        # Counter ahead: one straggler at the window edge is expected and must
        # not alert; a persistent lead must.
        STATE.update(touch_events=2, transitions=[60])
        results.append(run(tmp, "COULD_NOT_MEASURE", 3, "counter ahead, strike 1"))
        results.append(run(tmp, "MISMATCH", 1, "counter ahead, strike 2",
                           fresh_state=False))

        # A restart re-anchors the counter; comparing across it is meaningless.
        STATE.update(touch_events=5, transitions=[60, 120, 180, 240, 300])
        results.append(run(tmp, "AGREE_FIRED", 0, "anchor before restart"))
        STATE.update(touch_events=0, uptime_s=30.0, transitions=[])
        results.append(run(tmp, "COULD_NOT_MEASURE", 3, "restart detected",
                           fresh_state=False))
        STATE.update(uptime_s=600.0)

        # Blind cases must say so, not say "broken".
        STATE.update(enabled=False)
        results.append(run(tmp, "COULD_NOT_MEASURE", 3, "ingest disabled"))
        STATE.update(enabled=True)

        env_bad = os.path.join(tmp, "ha.env")
        with open(env_bad, "w") as fh:
            fh.write("HA_BASE_URL=http://127.0.0.1:%d\nHA_TOKEN=wrong\n" % PORT)
        results.append(run(tmp, "COULD_NOT_MEASURE", 3, "HA rejects the token"))
        with open(env_bad, "w") as fh:
            fh.write("HA_BASE_URL=http://127.0.0.1:1\nHA_TOKEN=test-token\n")
        results.append(run(tmp, "COULD_NOT_MEASURE", 3, "HA unreachable"))

        # The log must be machine-readable, because the knock that proves this
        # will happen while nobody is watching.
        lines = [json.loads(l) for l in open(os.path.join(tmp, "log.jsonl"))
                 if l.strip()]
        ok = len(lines) == len(results) and all("outcome" in l and "at" in l
                                                for l in lines)
        print("%-5s %-28s %d JSONL records, all with outcome+timestamp"
              % ("PASS" if ok else "FAIL", "log is parseable", len(lines)))
        results.append(ok)

    server.shutdown()
    passed = sum(1 for r in results if r)
    print("\n%d passed, %d failed" % (passed, len(results) - passed))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
