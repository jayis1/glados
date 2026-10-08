#!/usr/bin/env python3
"""Proof for the senses: one model called `glados` (Paperclip IST-270).

Run on the GPU host with paperclip-sttd, paperclip-ttsd and ollama live:

    python3 /opt/paperclip-gladosd/test_senses.py

Two properties are being proved, and the second is the one that keeps her safe.

  1. `glados` really is one model from outside. An image, a clip of speech and
     a request to answer out loud all work under that single name, against the
     real whisper / moondream / Kokoro services - no mocks, because a mock
     cannot be wrong the way a socket can. The hearing test renders its own
     audio with Kokoro and feeds it back to whisper, so her ears are proved
     against her own voice rather than a fixture somebody trimmed to pass.

  2. No sense can take her words away. GLaDOS was mute for days because one
     host went down. Every added engine is exercised dead, refusing,
     unauthorised and HUNG (a socket that accepts and never answers - the
     branch a closed port does not cover), and in each case a request that has
     text still gets an answer.

     With one deliberate exception: a request whose ONLY content is audio or an
     image, where that sense failed, returns 502. Answering anyway would mean
     inventing an utterance nobody made. Fail open means never losing words
     she has; it must not mean fabricating words she never heard.
"""

import base64
import copy
import json
import os
import socket
import struct
import sys
import threading
import time
from http.client import HTTPConnection

sys.path.insert(0, "/opt/paperclip-gladosd")
import gladosd  # noqa: E402  (for the shipped config and placement policy)
import senses  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("%s  %s%s" % ("ok  " if cond else "FAIL", name,
                        ("  -- " + str(detail)) if detail else ""))


def section(title):
    print("\n== %s" % title)


def deaf_listener():
    """Accepts, then never answers: a wedged service, not a refused one."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)

    def accept_forever():
        held = []
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            held.append(conn)

    threading.Thread(target=accept_forever, daemon=True).start()
    return srv.getsockname()[1]


DEAF_PORT = deaf_listener()
DEAD_PORT = 11977          # nothing listens here; verified in section 2
CFG = gladosd.CFG          # the SHIPPED config, not a test fixture
PLACE = gladosd.apply_placement_policy


# ---------------------------------------------------------------------------
section("1. the fence: which models get the senses")

for tag in ("glados", "glados:latest", "GLaDOS", "  glados  "):
    check("`%s` is multimodal" % tag.strip(),
          senses.model_multimodal(tag, CFG))
# This is the whole reason the fence exists: IST-271's re-point must stay a
# byte-identical host swap, so the tag the 22 live n8n nodes name gets nothing.
check("qwen2.5:7b-instruct is NOT multimodal (IST-271 stays a host swap)",
      not senses.model_multimodal("qwen2.5:7b-instruct", CFG))
check("moondream is NOT multimodal (it is a sense, not the model)",
      not senses.model_multimodal("moondream", CFG))
for bad in (None, "", "llama3", "glados-ish"):
    check("`%r` is not multimodal" % bad, not senses.model_multimodal(bad, CFG))
check("the kill switch turns every sense off at once",
      not senses.model_multimodal("glados", dict(CFG, multimodal=False)))


# ---------------------------------------------------------------------------
section("2. payload decoding and the caps")


def wav(ms=400, rate=16000, hz=440):
    """A real RIFF/WAVE buffer, built here so its duration is known exactly."""
    n = int(rate * ms / 1000)
    import math
    frames = b"".join(struct.pack("<h", int(12000 * math.sin(
        2 * math.pi * hz * i / rate))) for i in range(n))
    hdr = (b"RIFF" + struct.pack("<I", 36 + len(frames)) + b"WAVE"
           + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
           + b"data" + struct.pack("<I", len(frames)))
    return hdr + frames


TONE = wav()
check("base64 round-trips", senses._decode_b64(
    base64.b64encode(TONE).decode()) == TONE)
check("a data: URL is accepted", senses._decode_b64(
    "data:audio/wav;base64," + base64.b64encode(TONE).decode()) == TONE)
for bad, why in ((123, "not a string"), ("", "empty"), ("   ", "blank")):
    try:
        senses._decode_b64(bad)
        check("%r is rejected" % (bad,), False, "accepted")
    except ValueError as exc:
        check("%s is rejected with a reason" % why, True, exc)
try:
    senses._decode_b64(base64.b64encode(b"x" * (senses.MAX_DECODED_BYTES + 1)).decode())
    check("an oversized payload is rejected", False, "accepted")
except ValueError as exc:
    check("an oversized payload is rejected before any GPU call", True, exc)

check("wav_duration_ms reads the real duration",
      abs((senses.wav_duration_ms(TONE) or 0) - 400) <= 2,
      senses.wav_duration_ms(TONE))
check("wav_duration_ms degrades to None on junk rather than raising",
      senses.wav_duration_ms(b"not a wav at all") is None)

# The dead port has to actually be dead or every fail-open test below is void.
probe = socket.socket()
probe.settimeout(0.5)
dead = probe.connect_ex(("127.0.0.1", DEAD_PORT)) != 0
probe.close()
check("the port used as 'dead' really refuses connections", dead,
      "127.0.0.1:%d" % DEAD_PORT)


# ---------------------------------------------------------------------------
section("3. ears: whisper, proved against her own voice")


def kokoro(text, voice=None):
    """Render with the live Kokoro so the ear test uses real speech."""
    payload = json.dumps({"text": text, "voice": voice or CFG["glados_voice"],
                          "speed": 1.0}).encode()
    headers = {"Content-Type": "application/json",
               "Content-Length": str(len(payload))}
    tok = senses._token(CFG["tts_token_path"])
    if tok:
        headers["Authorization"] = "Bearer " + tok
    status, data, hdrs = senses._request(CFG["tts_url"], "POST", "/speak",
                                         body=payload, headers=headers,
                                         timeout=60)
    assert status == 200, (status, data[:200])
    return data


SPOKEN = "The cake is a lie and the doorbell is broken."
try:
    SPEECH = kokoro(SPOKEN)
    check("Kokoro rendered the test sentence (%d bytes, %s ms)"
          % (len(SPEECH), senses.wav_duration_ms(SPEECH)),
          SPEECH[:4] == b"RIFF")
except Exception as exc:  # noqa: BLE001
    SPEECH = None
    check("Kokoro rendered the test sentence", False, exc)

if SPEECH:
    b64 = base64.b64encode(SPEECH).decode()
    body = {"model": "glados", "messages": [
        {"role": "system", "content": "You are GLaDOS."},
        {"role": "user", "content": "", "audio": [b64]}]}
    note = {}
    err = senses.apply_ears(body, CFG, "/api/chat", note)
    heard = body["messages"][1].get("content", "")
    words = {w.strip(".,!?").lower() for w in heard.split()}
    want = {"cake", "lie", "doorbell", "broken"}
    check("she heard her own voice: %r" % heard[:80],
          err is None and want <= words, sorted(want - words) or "all four words")
    check("the audio key is gone once it is a transcript",
          "audio" not in body["messages"][1])
    check("the transcript is logged as evidence", bool(note.get("heard")),
          note.get("heard"))

    # Audio BESIDE text: both must survive. A clip is an addition to the turn,
    # not a replacement for what the caller also wrote.
    body = {"model": "glados", "messages": [
        {"role": "user", "content": "Someone is at the door.", "audio": [b64]}]}
    senses.apply_ears(body, CFG, "/api/chat", {})
    content = body["messages"][0]["content"]
    check("text sent alongside audio is kept, not overwritten",
          "Someone is at the door." in content and "cake" in content.lower(),
          content[:90])

    # /api/generate carries audio at the top level, beside `prompt`.
    body = {"model": "glados", "prompt": "", "audio": [b64]}
    senses.apply_ears(body, CFG, "/api/generate", {})
    check("/api/generate top-level audio becomes the prompt",
          "cake" in body["prompt"].lower() and "audio" not in body,
          body["prompt"][:80])

# Non-WAV input: this host has no transcoder, so the error must say so rather
# than letting sttd fail with something less actionable.
body = {"model": "glados", "prompt": "",
        "audio": [base64.b64encode(b"ID3\x04\x00not an mp3 really").decode()]}
note = {}
err = senses.apply_ears(body, CFG, "/api/generate", note)
check("non-WAV audio is refused by name, not by sttd's internals",
      err and "RIFF/WAVE" in json.dumps(note), note.get("stt_errors"))
check("audio-only + unusable audio = 502, not an invented utterance",
      err and err[0] == 502 and err[1].get("sense") == "hearing", err)

# Same bad clip, but the caller also wrote something: she must still answer.
body = {"model": "glados", "messages": [
    {"role": "user", "content": "Did you hear that?",
     "audio": [base64.b64encode(b"ID3 junk").decode()]}]}
note = {}
err = senses.apply_ears(body, CFG, "/api/chat", note)
check("deaf ear + text = she still answers the text",
      err is None and body["messages"][0]["content"] == "Did you hear that?",
      note.get("stt_errors"))

# A dead whisper, a hung whisper, and a refused token - each against a socket.
if SPEECH:
    for label, cfg_over, bound_s in (
            ("a refused port", {"stt_url": "127.0.0.1:%d" % DEAD_PORT}, 5),
            ("a HUNG whisper", {"stt_url": "127.0.0.1:%d" % DEAF_PORT,
                                "stt_timeout_s": 1.0}, 3),
            ("an unauthorised ear", {"stt_token_path": "/opt/paperclip-gladosd/.no-token"}, 20)):
        body = {"model": "glados", "messages": [
            {"role": "user", "content": "Answer me.",
             "audio": [base64.b64encode(SPEECH).decode()]}]}
        t0 = time.time()
        err = senses.apply_ears(body, dict(CFG, **cfg_over), "/api/chat", {})
        took = time.time() - t0
        ok = err is None and body["messages"][0]["content"] == "Answer me."
        check("%s cannot cost her the reply (%.2fs)" % (label, took),
              ok and took < bound_s, "err=%s" % (err,))

        # ...and audio-only against the same broken ear must be a loud 502.
        body = {"model": "glados", "prompt": "",
                "audio": [base64.b64encode(SPEECH).decode()]}
        err = senses.apply_ears(body, dict(CFG, **cfg_over), "/api/generate", {})
        check("%s + audio-only = 502" % label,
              bool(err) and err[0] == 502, err and err[0])


# ---------------------------------------------------------------------------
section("4. eyes: moondream, under the glados name")

FIXTURE = "/opt/paperclip-gladosd/fixtures/doorstep.b64"
IMG = None
if os.path.exists(FIXTURE):
    with open(FIXTURE) as fh:
        IMG = fh.read().strip()
check("the doorstep fixture is present", bool(IMG), FIXTURE)

if IMG:
    body = {"model": "glados", "messages": [
        {"role": "user", "content": "Who is that?", "images": [IMG]}]}
    note = {}
    t0 = time.time()
    err = senses.apply_eyes(body, CFG, note, PLACE)
    took = time.time() - t0
    content = body["messages"][0]["content"]
    check("`glados` saw the image without the caller naming moondream (%.1fs)"
          % took, err is None and bool(note.get("seen")),
          (note.get("seen") or [{}])[0].get("caption", note.get("vision_errors")))
    check("the caption arrives at the language layer as text",
          CFG["vision_inject_label"] in content and "Who is that?" in content,
          content[:140])
    # qwen2.5 is not a vision model: an images payload left on the body is at
    # best ignored and at worst a 400 from ollama.
    check("the images key is removed once captioned",
          "images" not in body["messages"][0])

    body = {"model": "glados", "prompt": "", "images": [IMG]}
    err = senses.apply_eyes(body, CFG, {}, PLACE)
    check("/api/generate top-level images become the prompt",
          err is None and "images" not in body and len(body["prompt"]) > 20,
          body["prompt"][:120])

# A blind eye must not cost her the words. Point the delegate at a dead
# backend - the delegate uses `backend`, so this also proves the delegate is
# genuinely going through the shared backend config.
body = {"model": "glados", "messages": [
    {"role": "user", "content": "Describe it.", "images": [IMG or "Zm9v"]}]}
note = {}
err = senses.apply_eyes(body, dict(CFG, backend="127.0.0.1:%d" % DEAD_PORT),
                        note, PLACE)
check("a dead moondream backend cannot cost her the reply",
      err is None and body["messages"][0]["content"] == "Describe it.",
      note.get("vision_errors"))
body = {"model": "glados", "prompt": "", "images": [IMG or "Zm9v"]}
err = senses.apply_eyes(body, dict(CFG, backend="127.0.0.1:%d" % DEAD_PORT),
                        {}, PLACE)
check("image-only + a blind eye = 502", bool(err) and err[0] == 502, err and err[0])

body = {"model": "glados", "prompt": "", "images": [IMG or "Zm9v"]}
t0 = time.time()
err = senses.apply_eyes(body, dict(CFG, backend="127.0.0.1:%d" % DEAF_PORT,
                                   vision_delegate_timeout_s=1.0), {}, PLACE)
check("a HUNG moondream is bounded by its timeout (%.2fs)" % (time.time() - t0),
      bool(err) and time.time() - t0 < 4)

# The delegate must inherit the keep_alive clamp, or moondream can pin GPU 0
# away from the voice stack - the exact trap gladosd already guards for n8n.
delegate, dnote = PLACE({"model": CFG["vision_delegate_model"],
                         "prompt": "x", "images": ["Zm9v"], "keep_alive": -1})
check("the vision delegate inherits the GPU-0 pin and keep_alive clamp",
      dnote.get("route") == "vision"
      and delegate["keep_alive"] == CFG["vision_keep_alive_max_s"]
      and delegate["options"]["num_gpu"] == CFG["vision_num_gpu"],
      {"keep_alive": delegate.get("keep_alive"),
       "num_gpu": delegate.get("options", {}).get("num_gpu")})


# ---------------------------------------------------------------------------
section("5. voice: Kokoro on the way out")

LINE = "Oh. It's you. How very predictable."
out = {"message": {"role": "assistant", "content": LINE}}
note = {}
t0 = time.time()
senses.apply_voice(out, {}, CFG, note)
took = time.time() - t0
check("the reply came back spoken (%.1fs)" % took, "audio" in out,
      out.get("audio_error"))
if "audio" in out:
    rendered = base64.b64decode(out["audio"])
    check("the attached audio is a real WAV", rendered[:4] == b"RIFF"
          and rendered[8:12] == b"WAVE")
    check("it is as long as a spoken sentence, not a click",
          (out.get("audio_ms") or 0) > 800, out.get("audio_ms"))
    check("the text is untouched by having been spoken",
          out["message"]["content"] == LINE)
    check("the default voice is the unassigned one from the IST-266 map",
          out["audio_voice"] == "af_jessica", out.get("audio_voice"))

out2 = {"response": LINE}
senses.apply_voice(out2, {"voice": "af_sky", "speed": 1.3}, CFG, {})
check("/api/generate shape is spoken too, honouring voice and speed",
      "audio" in out2 and out2["audio_voice"] == "af_sky"
      and out2["audio_speed"] == 1.3, out2.get("audio_error"))

# The one that matters: a dead Kokoro must lose the audio and keep the words.
for label, over, bound in (
        ("a refused port", {"tts_url": "127.0.0.1:%d" % DEAD_PORT}, 5),
        ("a HUNG Kokoro", {"tts_url": "127.0.0.1:%d" % DEAF_PORT,
                           "tts_timeout_s": 1.0}, 3),
        ("a bad voice name", {"glados_voice": "af_not_a_voice"}, 20),
        ("an unauthorised voice",
         {"tts_token_path": "/opt/paperclip-gladosd/.no-token"}, 20)):
    out3 = {"message": {"content": LINE}}
    t0 = time.time()
    senses.apply_voice(out3, {}, dict(CFG, **over), {})
    took = time.time() - t0
    check("%s = silent but not mute (%.2fs)" % (label, took),
          "audio" not in out3 and out3["message"]["content"] == LINE
          and bool(out3.get("audio_error")) and took < bound,
          out3.get("audio_error"))

out4 = {"message": {"content": "   "}}
note = {}
senses.apply_voice(out4, {}, CFG, note)
check("an empty reply is skipped rather than sent to Kokoro",
      "audio" not in out4 and note.get("tts_skipped") == "empty_reply")


# ---------------------------------------------------------------------------
section("6. nothing we invented reaches ollama")

body = {"model": "glados", "speak": True, "voice": "af_sky", "speed": 1.1,
        "language": "en", "audio": ["Zm9v"],
        "options": {"temperature": 0.7, "speak": True, "voice": "af_sky"},
        "messages": [{"role": "user", "content": "hi", "audio": ["Zm9v"]}]}
lifted = senses.voice_options(body)
removed = senses.strip_sense_keys(body)
check("voice and speed are lifted before the body is cleaned",
      lifted == {"voice": "af_sky", "speed": 1.1}, lifted)
check("every extension key is stripped",
      not ({"speak", "voice", "speed", "language", "audio"} & set(body))
      and not ({"speak", "voice"} & set(body["options"]))
      and "audio" not in body["messages"][0], body)
check("the real ollama options survive the cleaning",
      body["options"] == {"temperature": 0.7}, body["options"])
check("what was stripped is recorded for the log", len(removed) >= 6, removed)

check("reply_text reads /api/chat", senses.reply_text(
    {"message": {"content": "a"}}) == "a")
check("reply_text reads /api/generate", senses.reply_text({"response": "b"}) == "b")
check("reply_text reads the OpenAI shape", senses.reply_text(
    {"choices": [{"message": {"content": "c"}}]}) == "c")
check("reply_text degrades to empty rather than raising",
      senses.reply_text(None) == "" and senses.reply_text({}) == "")


# ---------------------------------------------------------------------------
section("7. apply_senses never raises, whatever it is handed")

for label, body in (
        ("an empty body", {}),
        ("audio that is not a list", {"audio": "not-a-list"}),
        ("a message that is not a dict", {"messages": ["nope"]}),
        ("None inside the clip list", {"prompt": "x", "audio": [None]}),
        ("images that are not a list", {"prompt": "x", "images": 7}),
        ("a nested dict where a string belongs", {"prompt": {"a": 1}})):
    try:
        note, err = senses.apply_senses(copy.deepcopy(body), CFG,
                                        "/api/chat", PLACE)
        check("%s is survived" % label, True,
              note.get("senses_error") or err or "clean")
    except Exception as exc:  # noqa: BLE001
        check("%s is survived" % label, False, "%s: %s" % (type(exc).__name__, exc))

check("senses.health() never raises and reports each sense",
      set(senses.health(CFG)) >= {"multimodal", "sight", "hearing", "voice"},
      {k: v.get("ok") if isinstance(v, dict) else v
       for k, v in senses.health(CFG).items()})
check("senses.health() on a dead host reports dead rather than raising",
      senses.health(dict(CFG, stt_url="127.0.0.1:%d" % DEAD_PORT,
                         tts_url="127.0.0.1:%d" % DEAD_PORT))["hearing"]["ok"]
      is False)


print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
    print("FAILED: " + "\n         ".join(FAIL))
sys.exit(1 if FAIL else 0)
