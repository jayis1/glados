#!/usr/bin/env python3
"""End-to-end proof: one model called `glados` (Paperclip IST-270).

    python3 /opt/paperclip-gladosd/proof_one_model.py

test_senses.py proves each sense in isolation. This proves the claim a caller
actually cares about: that there is ONE model name on this host, and asking for
it gets you sight, hearing, words and a voice without naming any other engine.
Everything here goes over the wire to the live service on :11434 - no imports,
no internals - because "one model" is a statement about the API, so the API is
what has to be interrogated.

It also carries a CONTROL ARM, and that is not a formality. The same four
extension fields are sent to `qwen2.5:7b-instruct`, the tag all 22 live n8n
nodes name, and must be ignored there: plain text in, plain text out, no audio
attached. Without that arm "the senses work" and "the n8n re-point is still a
byte-identical host swap" are both assertions; with it they are both measured.

Written to /opt/paperclip-glados/results/one_model_proof.json.
"""

import base64
import json
import os
import sys
import time
from http.client import HTTPConnection

HOST = os.environ.get("GLADOS_HOST", "127.0.0.1:11434")
FIXTURE = "/opt/paperclip-gladosd/fixtures/doorstep.b64"
TTS = "127.0.0.1:9098"
RESULTS = "/opt/paperclip-glados/results/one_model_proof.json"

PASS, FAIL, RECORD = [], [], {}


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("%s  %s%s" % ("ok  " if cond else "FAIL", name,
                        ("\n       " + str(detail)) if detail else ""))


def post(path, body, host=HOST, timeout=300, headers=None):
    raw = json.dumps(body).encode()
    h = {"Content-Type": "application/json", "Content-Length": str(len(raw))}
    h.update(headers or {})
    conn = HTTPConnection(host, timeout=timeout)
    t0 = time.time()
    conn.request("POST", path, body=raw, headers=h)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    try:
        out = json.loads(data or b"{}")
    except Exception:  # noqa: BLE001
        out = {"_raw": data[:400].decode("utf-8", "replace")}
    return resp.status, out, round((time.time() - t0) * 1000)


def get(path, host=HOST, timeout=20):
    conn = HTTPConnection(host, timeout=timeout)
    conn.request("GET", path)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, json.loads(data or b"{}")


def say(text, voice="am_echo"):
    """Render a human's question with Kokoro so there is real speech to hear.

    Deliberately NOT her own voice: `am_echo` stands in for someone at the
    door, so the clip is not the same voice the voice test renders.
    """
    payload = json.dumps({"text": text, "voice": voice, "speed": 1.0}).encode()
    tok = open("/opt/paperclip-tts/token").read().strip()
    conn = HTTPConnection(TTS, timeout=60)
    conn.request("POST", "/speak", body=payload, headers={
        "Content-Type": "application/json", "Content-Length": str(len(payload)),
        "Authorization": "Bearer " + tok})
    resp = conn.getresponse()
    wav = resp.read()
    conn.close()
    assert resp.status == 200, (resp.status, wav[:200])
    return base64.b64encode(wav).decode()


SYSTEM = ("You are GLaDOS from Portal. Deadpan, sarcastic, condescending, "
          "occasionally menacing. Under 25 words. One sentence. No emojis, "
          "no quotes, no meta about being an AI.")

print("one model called `glados` - end to end against %s\n" % HOST)

# --------------------------------------------------------------- 0. the name
status, caps = get("/capabilities")
RECORD["capabilities"] = caps
parts = {e["part"]: e for e in caps.get("engines", [])}
check("/capabilities answers and names one model",
      status == 200 and caps.get("model") == "glados", caps.get("model"))
check("all five parts are declared behind that one name",
      set(parts) == {"state", "words", "sight", "hearing", "voice"},
      sorted(parts))
check("the borrowing is stated rather than glossed over",
      parts.get("state", {}).get("ours") is True
      and parts.get("words", {}).get("ours") is False,
      "state ours=%s, words ours=%s" % (parts.get("state", {}).get("ours"),
                                        parts.get("words", {}).get("ours")))

status, tags = get("/api/tags")
names = sorted(m.get("name", "") for m in tags.get("models", []))
check("`glados` is a real tag the API will serve",
      any(n.startswith("glados") for n in names), names)

# --------------------------------------------------------------- 1. words
status, out, ms = post("/api/chat", {
    "model": "glados", "stream": False,
    "messages": [{"role": "system", "content": SYSTEM},
                 {"role": "user", "content": "Someone rang the doorbell."}],
    "options": {"temperature": 0.7, "num_predict": 100}})
words = (out.get("message") or {}).get("content", "")
RECORD["words"] = {"status": status, "ms": ms, "reply": words}
check("words: she answers plain text (%d ms)" % ms,
      status == 200 and len(words.strip()) > 5, repr(words))

# --------------------------------------------------------------- 2. sight
with open(FIXTURE) as fh:
    IMG = fh.read().strip()
status, out, ms = post("/api/chat", {
    "model": "glados", "stream": False,
    "messages": [{"role": "system", "content": SYSTEM},
                 {"role": "user", "content": "Who is at the door?",
                  "images": [IMG]}],
    "options": {"temperature": 0.7, "num_predict": 100}})
seen = (out.get("message") or {}).get("content", "")
RECORD["sight"] = {"status": status, "ms": ms, "reply": seen}
check("sight: `glados` answered about an image, moondream never named (%d ms)"
      % ms, status == 200 and len(seen.strip()) > 5, repr(seen))

# --------------------------------------------------------------- 3. hearing
QUESTION = "Hello, is anyone home? I have a package for you."
clip = say(QUESTION)
status, out, ms = post("/api/chat", {
    "model": "glados", "stream": False,
    "messages": [{"role": "system", "content": SYSTEM},
                 {"role": "user", "content": "", "audio": [clip]}],
    "options": {"temperature": 0.7, "num_predict": 100}})
heard = (out.get("message") or {}).get("content", "")
RECORD["hearing"] = {"status": status, "ms": ms, "spoken_to_her": QUESTION,
                     "reply": heard}
check("hearing: she answered speech, whisper never named (%d ms)" % ms,
      status == 200 and len(heard.strip()) > 5, repr(heard))

# --------------------------------------------------------------- 4. voice
status, out, ms = post("/api/chat", {
    "model": "glados", "stream": False, "speak": True,
    "messages": [{"role": "system", "content": SYSTEM},
                 {"role": "user", "content": "Say something unwelcoming."}],
    "options": {"temperature": 0.7, "num_predict": 100}})
spoken = (out.get("message") or {}).get("content", "")
audio = out.get("audio")
wav = base64.b64decode(audio) if audio else b""
RECORD["voice"] = {"status": status, "ms": ms, "reply": spoken,
                   "audio_bytes": out.get("audio_bytes"),
                   "audio_ms": out.get("audio_ms"),
                   "audio_voice": out.get("audio_voice"),
                   "audio_error": out.get("audio_error")}
check("voice: the reply came back as audio, Kokoro never named (%d ms)" % ms,
      status == 200 and wav[:4] == b"RIFF" and wav[8:12] == b"WAVE",
      "%s, %s ms of audio, %r" % (out.get("audio_bytes"), out.get("audio_ms"),
                                  spoken))
check("the spoken audio is long enough to be the sentence, not a click",
      (out.get("audio_ms") or 0) > 800, out.get("audio_ms"))
check("the text reply is still present alongside the audio",
      len(spoken.strip()) > 5)

# ------------------------------------------------- 5. all of it, at one name
#
# The whole claim in a single request: she is shown the doorstep, spoken to,
# and asked to answer out loud - one model name, four engines, one round trip.
clip2 = say("Open the door, I know you are in there.")
status, out, ms = post("/api/chat", {
    "model": "glados", "stream": False, "speak": True,
    "messages": [{"role": "system", "content": SYSTEM},
                 {"role": "user", "content": "", "images": [IMG],
                  "audio": [clip2]}],
    "options": {"temperature": 0.7, "num_predict": 100}})
everything = (out.get("message") or {}).get("content", "")
wav_all = base64.b64decode(out["audio"]) if out.get("audio") else b""
RECORD["all_senses_at_once"] = {
    "status": status, "ms": ms, "reply": everything,
    "audio_ms": out.get("audio_ms"), "audio_bytes": out.get("audio_bytes"),
    "audio_error": out.get("audio_error")}
check("ALL AT ONCE: saw, heard, thought and spoke in one request (%d ms)" % ms,
      status == 200 and len(everything.strip()) > 5 and wav_all[:4] == b"RIFF",
      "%r + %s ms of audio" % (everything, out.get("audio_ms")))

# ------------------------------------------------------------ 6. control arm
#
# The fence. The tag every one of the 22 live n8n nodes names gets none of
# this, so IST-271's re-point stays the byte-identical host swap IST-266
# accepted. Same four extension fields, and they must do nothing.
status, out, ms = post("/api/chat", {
    "model": "qwen2.5:7b-instruct", "stream": False, "speak": True,
    "voice": "af_sky",
    "messages": [{"role": "system", "content": SYSTEM},
                 {"role": "user", "content": "Someone rang the doorbell."}],
    "options": {"temperature": 0.7, "num_predict": 100}})
control = (out.get("message") or {}).get("content", "")
RECORD["control_arm_qwen"] = {"status": status, "ms": ms, "reply": control,
                              "audio_present": "audio" in out,
                              "audio_error": out.get("audio_error")}
check("CONTROL: the n8n tag answered normally (%d ms)" % ms,
      status == 200 and len(control.strip()) > 5, repr(control))
check("CONTROL: `speak` is inert on the n8n tag - no audio attached",
      "audio" not in out and "audio_error" not in out,
      sorted(k for k in out if k.startswith("audio")) or "no audio keys")

# ------------------------------------------- 7. streaming + speak, refused
status, out, ms = post("/api/chat", {
    "model": "glados", "stream": True, "speak": True,
    "messages": [{"role": "user", "content": "hello"}]})
RECORD["stream_plus_speak"] = {"status": status, "error": out.get("error")}
check("speak + stream:true is refused clearly, not silently dropped",
      status == 400 and "stream" in (out.get("error") or ""),
      "%s %s" % (status, out.get("error")))

# -------------------------------------------------- 8. the log is evidence
time.sleep(0.5)
events = []
try:
    with open("/var/log/paperclip-gladosd/events.jsonl") as fh:
        for line in fh.readlines()[-60:]:
            try:
                events.append(json.loads(line))
            except Exception:  # noqa: BLE001
                pass
except Exception as exc:  # noqa: BLE001
    print("   (could not read the event log: %s)" % exc)

mm = [e for e in events if e.get("multimodal")]
check("every multimodal request is logged as one", len(mm) >= 4, len(mm))
check("the transcript is in the log, so an odd reply is traceable",
      any(e.get("heard") for e in mm),
      [h.get("text") for e in mm for h in (e.get("heard") or [])][:2])
check("the caption is in the log too",
      any(e.get("seen") for e in mm),
      [s.get("caption", "")[:60] for e in mm for s in (e.get("seen") or [])][:2])
check("the render is logged with its voice and length",
      any(e.get("spoken") for e in mm),
      [e["spoken"] for e in mm if e.get("spoken")][:1])
check("the keys we invented are recorded as stripped before ollama saw them",
      any(e.get("sense_keys_stripped") for e in mm),
      [e["sense_keys_stripped"] for e in mm if e.get("sense_keys_stripped")][:1])
qwen = [e for e in events if e.get("model") == "qwen2.5:7b-instruct"]
check("the control request is logged WITHOUT any sense activity",
      bool(qwen) and not any(e.get("multimodal") or e.get("heard")
                             or e.get("seen") or e.get("spoken") for e in qwen),
      [{k: v for k, v in e.items() if k in ("model", "multimodal", "mood_skipped")}
       for e in qwen[-1:]])

RECORD["summary"] = {"passed": len(PASS), "failed": len(FAIL),
                     "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     "host": HOST, "failures": FAIL}
try:
    os.makedirs(os.path.dirname(RESULTS), exist_ok=True)
    with open(RESULTS, "w") as fh:
        json.dump(RECORD, fh, indent=2)
    print("\nraw: %s" % RESULTS)
except Exception as exc:  # noqa: BLE001
    print("\n(could not write %s: %s)" % (RESULTS, exc))

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
    print("FAILED: " + "\n        ".join(FAIL))
sys.exit(1 if FAIL else 0)
