#!/usr/bin/env python3
"""Proof that the fly drives a model it has never seen before.

    python3 proof_byom.py [--base llama3.2:1b] [--json out.json]

WHAT IS BEING CLAIMED

`byom.sh <base>` builds a GLaDOS on top of any language model you like, and
the fly drives it with no server change, no config edit and no restart. That
claim is cheap to make and easy to get wrong in a way nobody notices, because
a BYO tag that is quietly uncoupled still answers in character - it just has
no moods, forever.

So this measures the whole path over the wire:

  1. Two tags are built from the SAME base and differ ONLY in their name:
     `glados-byom-proof` and `byom-proof-control`.
  2. Each is sent one identical chat request through gladosd.
  3. The event log says what gladosd did to each body.

The control arm is the point. gladosd fences the coupling by name prefix, so
two tags over identical weights must come out differently: the `glados-*` one
carries a state clause, and the other is untouched. If both were coupled the
fence would be broken (and the tag your other software calls would be at
risk); if neither were, the feature would not exist. One of each is the only
passing outcome.

The base deliberately is NOT `qwen2.5:7b-instruct`. A proof run against the
deployed language organ would prove nothing about bringing your own.

Leaves the host as it found it: both proof tags are removed at the end and the
live tags are re-listed to show they survived.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

GLADOSD = os.environ.get("GLADOS_HEALTH", "127.0.0.1:11434")
EVENTS = os.environ.get("GLADOS_EVENTS", "/var/log/paperclip-gladosd/events.jsonl")
BACKEND = os.environ.get("GLADOS_BACKEND", "127.0.0.1:11501")

COUPLED_TAG = "glados-byom-proof"
CONTROL_TAG = "byom-proof-control"

# One sentence of persona and one user turn, identical for both arms. The
# system message matters: the clause is APPENDED to an existing system message
# and never invented, so a body without one is correctly left alone.
SYSTEM = "You are GLaDOS from Portal. Deadpan and condescending. One sentence."
USER = "Someone is at the front door."


def ollama(*args, timeout=600):
    env = dict(os.environ, OLLAMA_HOST=BACKEND)
    p = subprocess.run(("ollama",) + args, env=env, timeout=timeout,
                       capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def create(tag, base):
    """Create `tag` FROM `base`. Same bytes as byom.sh emits, inlined so the
    proof does not depend on a shell script's argument handling."""
    mf = ("FROM %s\n\nSYSTEM \"\"\"%s\"\"\"\n\n"
          "PARAMETER temperature 0.7\nPARAMETER num_predict 60\n"
          % (base, SYSTEM))
    path = os.path.join(os.environ.get("TMPDIR", "/tmp"), "Modelfile." + tag)
    with open(path, "w") as fh:
        fh.write(mf)
    rc, out = ollama("create", tag, "-f", path)
    os.unlink(path)
    if rc != 0:
        raise SystemExit("ollama create %s failed: %s" % (tag, out[-400:]))


def digest(tag):
    """The base weights' blob digest, so 'same base' is checked, not assumed."""
    rc, out = ollama("show", "--modelfile", tag)
    for line in out.splitlines():
        if line.startswith("FROM ") and "blobs" in line:
            return line.split("sha256-")[-1].strip()[:16]
    return None


def chat(tag, temperature=0.7):
    body = json.dumps({
        "model": tag, "stream": False,
        "options": {"temperature": temperature, "num_predict": 60},
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": USER}]}).encode()
    req = urllib.request.Request("http://%s/api/chat" % GLADOSD, data=body,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            payload = json.loads(r.read() or b"{}")
            code = r.status
    except urllib.error.HTTPError as exc:
        payload, code = {"error": exc.read().decode("utf-8", "replace")}, exc.code
    return code, payload, round((time.time() - t0) * 1000)


def last_event(tag):
    """gladosd's own record of what it did to that body."""
    found = None
    with open(EVENTS) as fh:
        for line in fh:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("model") == tag and ev.get("event") == "inference":
                found = ev
    return found or {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="llama3.2:1b",
                    help="any ollama model reference that is not the deployed one")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    if args.base.startswith("qwen2.5:7b"):
        raise SystemExit("pick a base that is not the deployed language organ; "
                         "that is the whole claim being tested")

    out = {"base": args.base, "gladosd": GLADOSD, "arms": {}}

    with urllib.request.urlopen("http://%s/health" % GLADOSD, timeout=5) as r:
        health = json.loads(r.read())
    out["coupled_prefixes"] = health["mood"]["coupled_models"]
    out["tags_before"] = health["models_present"]

    for tag in (COUPLED_TAG, CONTROL_TAG):
        create(tag, args.base)
    out["digests"] = {t: digest(t) for t in (COUPLED_TAG, CONTROL_TAG)}

    for tag in (COUPLED_TAG, CONTROL_TAG):
        code, payload, ms = chat(tag)
        ev = last_event(tag)
        out["arms"][tag] = {
            "http": code, "latency_ms": ms,
            "reply": (payload.get("message") or {}).get("content", "")[:300],
            "error": payload.get("error"),
            "mood_applied": ev.get("mood_applied"),
            "mood_skipped": ev.get("mood_skipped"),
            "mood_state": ev.get("mood_state"),
            "mood_clause_at": ev.get("mood_clause_at"),
            "temperature_before": ev.get("temperature_before"),
            "temperature": ev.get("temperature"),
            "system_as_sent": ((ev.get("request") or {}).get("messages") or
                               [{}])[0].get("content"),
        }

    hot, cold = out["arms"][COUPLED_TAG], out["arms"][CONTROL_TAG]
    clause_in = lambda s: "Current state:" in (s or "")

    checks = [
        ("both arms are the same weights under two names",
         out["digests"][COUPLED_TAG] == out["digests"][CONTROL_TAG]
         and out["digests"][COUPLED_TAG] is not None,
         out["digests"]),
        ("the base is not the deployed language organ",
         not args.base.startswith("qwen2.5:7b"), args.base),
        ("the BYO tag was never named in any config",
         not any(p == COUPLED_TAG for p in out["coupled_prefixes"]),
         out["coupled_prefixes"]),
        ("BYO tag: the fly reached it",
         hot["mood_applied"] is True, hot["mood_skipped"]),
        ("BYO tag: the state clause is in the system message as sent",
         clause_in(hot["system_as_sent"]), hot["mood_clause_at"]),
        ("BYO tag: temperature stayed inside the caller's band",
         hot["temperature"] is None or 0.7 <= float(hot["temperature"]) <= 0.9,
         "%s -> %s" % (hot["temperature_before"], hot["temperature"])),
        ("BYO tag: she answered",
         bool(hot["reply"].strip()) and hot["http"] == 200,
         "%d chars" % len(hot["reply"])),
        ("control tag: identical weights, wrong name, no fly",
         cold["mood_applied"] is False
         and cold["mood_skipped"] == "model_not_coupled",
         cold["mood_skipped"]),
        ("control tag: its system message went through untouched",
         not clause_in(cold["system_as_sent"]) and cold["system_as_sent"] == SYSTEM,
         repr((cold["system_as_sent"] or "")[-40:])),
    ]

    for tag in (COUPLED_TAG, CONTROL_TAG):
        ollama("rm", tag)
    with urllib.request.urlopen("http://%s/health" % GLADOSD, timeout=5) as r:
        out["tags_after"] = json.loads(r.read())["models_present"]
    checks.append(("the live tags survived the proof",
                   all(t in out["tags_after"] for t in out["tags_before"])
                   and COUPLED_TAG not in " ".join(out["tags_after"]),
                   out["tags_after"]))

    out["checks"] = [{"check": c, "pass": bool(p), "detail": str(d)}
                     for c, p, d in checks]
    out["passed"] = sum(1 for c in out["checks"] if c["pass"])
    out["total"] = len(out["checks"])

    print("\nBYOM proof - base %s\n" % args.base)
    for c in out["checks"]:
        print("  %s %-58s %s" % ("PASS" if c["pass"] else "FAIL",
                                 c["check"], c["detail"][:70]))
    print("\n  %s: %s" % (COUPLED_TAG, hot["reply"].strip()[:160]))
    print("  %s: %s\n" % (CONTROL_TAG, cold["reply"].strip()[:160]))
    print("  %d/%d\n" % (out["passed"], out["total"]))

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(out, fh, indent=2)
        print("  wrote %s\n" % args.json)
    return 0 if out["passed"] == out["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
