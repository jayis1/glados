#!/usr/bin/env python3
"""IST-269 acceptance probe for the mood bus.

    python3 probe_moodd.py

Checks, against the *running* service, the three things the ticket asks for
plus the ones that would otherwise be assumed:

  1. /health is up, names its engine, and reports both cards.
  2. The vector MOVES under sensory input, and comes back when the input
     stops. This is the acceptance criterion the known failure mode would
     silently pass: a service pinned at a constant answers every request.
  3. It survives `systemctl restart`.
  4. Authentication: /mood without the token is 401, /health without it is
     200, and an unknown sensory population is a 400 rather than a silent
     no-op (a typo that injected nothing would look exactly like a network
     that does not respond).

Exit status is 0 only if every check passes.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("MOOD_URL", "http://127.0.0.1:9099")
TOKEN = open(os.environ.get("MOOD_TOKEN_FILE",
                            os.path.join(os.path.dirname(os.path.abspath(__file__)), "token"))).read().strip()
UNIT = "paperclip-moodd"
FAILS = []


def call(path, body=None, token=TOKEN, timeout=20):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})},
        method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:                                     # noqa: BLE001
            return exc.code, {}
    except Exception as exc:                                  # noqa: BLE001
        return 0, {"error": str(exc)}


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{'  ' + detail if detail else ''}")
    if not ok:
        FAILS.append(name)
    return ok


def wait_healthy(deadline=180):
    t0 = time.time()
    while time.time() - t0 < deadline:
        code, body = call("/health", token=None, timeout=10)
        if code == 200 and body.get("ok"):
            return round(time.time() - t0, 1), body
        time.sleep(2)
    return None, None


def settle(drive, hold_s, samples=6):
    """Apply a drive, let the EMA catch up, then average a few samples.

    The sim runs at ~0.22x realtime, so a 600 ms EMA over simulation time is
    ~2.7 s of wall clock; sampling immediately after a POST would read the
    previous mood and call it a non-response.
    """
    code, _ = call("/drive", drive)
    if code != 200:
        return None, code
    time.sleep(hold_s)
    acc, raw = {}, {}
    for _ in range(samples):
        code, body = call("/mood")
        if code != 200:
            return None, code
        for k, v in body["mood"].items():
            acc[k] = acc.get(k, 0.0) + v / samples
        for k, v in body["rates_hz"].items():
            raw[k] = raw.get(k, 0.0) + v / samples
        time.sleep(0.5)
    return ({k: round(v, 4) for k, v in acc.items()},
            {k: round(v, 3) for k, v in raw.items()}), 200


def main():
    print(f"probing {BASE}\n")

    print("1. health")
    t, h = wait_healthy()
    if not check("/health reports ok", h is not None, f"after {t}s" if h else ""):
        return 1
    check("engine named", bool(h.get("engine")), h.get("engine", ""))
    check("both T400s in use", sorted(h.get("devices", [])) == [1, 2],
          str(h.get("gpus")))
    check("graph loaded", h.get("neurons") == 164587 and
          h.get("edges") == 24539704,
          f"{h.get('neurons')} neurons / {h.get('edges')} edges")
    check("peer exchange", h.get("exchange") == "peer", str(h.get("exchange")))
    check("not saturated", h.get("saturated") is False)
    check("not silent", h.get("silent") is False,
          f"network {h.get('network_hz')} Hz")
    print(f"       ms/step {h.get('ms_step')}  realtime {h.get('realtime')}x  "
          f"axes {h.get('axes')}")

    print("\n2. does the vector move?")
    zero = {k: 0.0 for k in ("olfactory_ORN", "photoreceptor", "hearing_JO",
                             "mechanosensory", "gustatory", "hygro_thermo")}
    rest, code = settle(zero, 20)
    if not check("baseline read", rest is not None, f"http {code}"):
        return 1
    base_v, base_hz = rest
    print(f"       rest   {json.dumps(base_v)}")

    # The tolerance for "came back to rest" is the service's own rest-to-rest
    # jitter, measured here, rather than a number chosen to pass: escape_GF is
    # 34 neurons and MBON is 97, so their axes carry real counting noise and a
    # fixed threshold would be either a rubber stamp or a flake.
    rest2, code = settle(zero, 20)
    if not check("second baseline read", rest2 is not None, f"http {code}"):
        return 1
    jitter = {k: abs(rest2[0][k] - base_v[k]) for k in base_v}
    tol = max(0.05, 3.0 * max(jitter.values()))
    print(f"       rest   {json.dumps(rest2[0])}")
    print(f"       rest-to-rest jitter {json.dumps({k: round(v, 3) for k, v in jitter.items()})}"
          f"  -> return tolerance {tol:.3f}")

    moved = {}
    for label, drive in (("olfactory_ORN=0.05", {**zero, "olfactory_ORN": 0.05}),
                         ("olfactory_ORN=0.20", {**zero, "olfactory_ORN": 0.20}),
                         ("mechanosensory=0.20",
                          {**zero, "mechanosensory": 0.20})):
        got, code = settle(drive, 12)
        if not check(f"read under {label}", got is not None, f"http {code}"):
            continue
        v, hz = got
        delta = {k: round(v[k] - base_v[k], 4) for k in v}
        moved[label] = delta
        big = {k: d for k, d in delta.items() if abs(d) >= 0.05}
        check(f"{label} moves the vector", bool(big),
              f"delta {json.dumps({k: delta[k] for k in sorted(delta, key=lambda x: -abs(delta[x]))[:3]})}")
        print(f"       {label:>20}  {json.dumps(v)}")

    back, code = settle(zero, 25)
    if back:
        v, _ = back
        worst = max(v, key=lambda k: abs(v[k] - base_v[k]))
        drift = abs(v[worst] - base_v[worst])
        check("returns to rest when the stimulus stops", drift <= tol,
              f"max axis drift {drift:.3f} ({worst}) vs tolerance {tol:.3f}")

    check("at least two axes respond",
          len({k for d in moved.values() for k, x in d.items()
               if abs(x) >= 0.05}) >= 2,
          str(sorted({k for d in moved.values() for k, x in d.items()
                      if abs(x) >= 0.05})))

    print("\n3. restart")
    before = call("/health", token=None)[1].get("sim_steps", 0)
    subprocess.run(["systemctl", "restart", UNIT], check=True)
    t, h2 = wait_healthy()
    if check("healthy again after restart", h2 is not None,
             f"in {t}s" if h2 else ""):
        check("it is a new process (step counter restarted)",
              h2.get("sim_steps", 0) < before or before == 0,
              f"{before} -> {h2.get('sim_steps')}")
        code, body = call("/mood")
        check("/mood serves after restart", code == 200 and "mood" in body,
              json.dumps(body.get("mood", {})))

    print("\n4. auth and input validation")
    code, _ = call("/mood", token=None)
    check("/mood without a token is 401", code == 401, f"http {code}")
    code, _ = call("/mood", token="wrong" * 10)
    check("/mood with a wrong token is 401", code == 401, f"http {code}")
    code, _ = call("/health", token=None)
    check("/health without a token is 200", code == 200, f"http {code}")
    code, body = call("/drive", {"nose": 0.1})
    check("unknown population is rejected", code == 400, f"http {code} {body}")
    code, body = call("/drive", {"olfactory_ORN": 7})
    check("out-of-range level is rejected", code == 400, f"http {code}")
    code, _ = call("/drive", zero)
    check("drive reset accepted", code == 200)

    print()
    if FAILS:
        print(f"FAILED: {len(FAILS)} check(s): {', '.join(FAILS)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
