#!/usr/bin/env python3
"""One model called `glados` - eyes, ears and a voice (Paperclip IST-270).

"can we cobble every model we have running in to the GLaDOS model, like the
mondream the kokoro and wispher all that in one model called GLaDOS"

Yes - at the level that matters to a caller. This module makes
`"model": "glados"` accept an image, accept speech, and answer out loud,
without the caller ever naming moondream, whisper or Kokoro. Four services
behind one name:

    glados
      |- qwen2.5:7b-instruct   words            CPU, 10 threads   (gladosd)
      |- the fly connectome    state            2x T400 :9099     (moodd)
      |- moondream             sight            GPU 0 :11501      (ollama)
      |- whisper large-v3      hearing          GPU 0 :9097       (sttd)
      '- Kokoro                voice            GPU 0 :9098       (ttsd)

WHAT THIS IS NOT, SAID PLAINLY
------------------------------
It is not one set of weights. You cannot concatenate a 7B decoder-only
transformer, a ViT captioner, an encoder-decoder ASR model and an ONNX vocoder
into a single file - they have different architectures, different tokenisers,
different output spaces, and they were each fitted separately. A genuinely
single-weight multimodal model is trained that way from the start, which needs
training headroom this host does not have (see the IST-270 plan addendum). So
this is one NAME, one API and one personality over five engines. From outside
there is exactly one model and it sees, hears, thinks and speaks; inside, the
honest accounting is the table above.

SCOPED TO `glados` ON PURPOSE
-----------------------------
`multimodal_models` is ["glados"], the same fence `mood_models` uses. The tag
all 22 live n8n nodes name - `qwen2.5:7b-instruct` - gets none of this, so
IST-271's re-point stays the byte-identical host swap IST-266 accepted. The
senses are additive to a model that did not exist before today, so nothing
that works today can change.

FAIL OPEN, WITH ONE EXCEPTION THAT MUST FAIL LOUD
-------------------------------------------------
GLaDOS was mute for days because one host went down; no sense added here may
be able to do that again. whisper down, Kokoro down, moondream down, a bad
token, a hung socket - she still answers in text.

The exception, and it is deliberate: if audio or an image is the ONLY content
of the request and that sense fails, there is nothing left to answer. Replying
anyway would be inventing an utterance nobody made. That case returns a 502
naming the dead sense. "Fail open" means never losing words she has; it does
not mean fabricating words she never heard.
"""

import base64
import json
import os
import struct
import threading
import time
from http.client import HTTPConnection

# Everything from the backend arrives base64 in JSON, so the practical cap is
# on the decoded payload. 25 MiB of 16 kHz mono WAV is ~13 minutes of audio -
# far past anything a doorbell or a phone turn produces, and sttd has its own
# MAX_BODY behind this anyway.
MAX_DECODED_BYTES = 25 * 1024 * 1024

_token_cache = {}
_token_lock = threading.Lock()

COUNTERS = {
    "heard": 0, "stt_errors": 0,
    "seen": 0, "vision_errors": 0,
    "spoken": 0, "tts_errors": 0,
}
_counter_lock = threading.Lock()


def _bump(key, n=1):
    with _counter_lock:
        COUNTERS[key] = COUNTERS.get(key, 0) + n


def _token(path):
    """Read a bearer token off disk, cached. Absent file -> None, not a raise.

    sttd and ttsd both treat an unset server-side token as "no auth required",
    so a missing file here is a legitimate configuration, not an error.
    """
    if not path:
        return None
    with _token_lock:
        if path in _token_cache:
            return _token_cache[path]
    tok = None
    try:
        with open(path) as fh:
            tok = fh.read().strip() or None
    except Exception:  # noqa: BLE001
        tok = None
    with _token_lock:
        _token_cache[path] = tok
    return tok


def _request(host_port, method, path, body=None, headers=None, timeout=10.0):
    """One-shot HTTP. Returns (status, bytes, headers); raises on transport."""
    conn = HTTPConnection(host_port, timeout=timeout)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read()
        return resp.status, data, {k.lower(): v for k, v in resp.getheaders()}
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def model_multimodal(model, cfg):
    """True iff this model name is fenced in as multimodal."""
    if not cfg.get("multimodal"):
        return False
    m = (model or "").strip().lower()
    if not m:
        return False
    base = m.split(":", 1)[0]
    for name in cfg.get("multimodal_models") or []:
        n = str(name).strip().lower()
        if m == n or base == n.split(":", 1)[0]:
            return True
    return False


# ---------------------------------------------------------------- ears -----

def _decode_b64(value):
    """Base64 (or a data: URL) -> bytes. Raises ValueError with a reason."""
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
    elif isinstance(value, str):
        s = value.strip()
        if s.startswith("data:"):
            _, _, s = s.partition(",")
        try:
            raw = base64.b64decode(s, validate=False)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("not base64: %s" % exc)
    else:
        raise ValueError("expected a base64 string, got %s" % type(value).__name__)
    if not raw:
        raise ValueError("decoded to zero bytes")
    if len(raw) > MAX_DECODED_BYTES:
        raise ValueError("decoded %d bytes, over the %d byte cap"
                         % (len(raw), MAX_DECODED_BYTES))
    return raw


def wav_duration_ms(raw):
    """Duration of a RIFF/WAVE buffer, or None if it cannot be read.

    Only used for the log and the proof; a wrong answer here must never fail a
    request, so every parse error degrades to None.
    """
    try:
        if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
            return None
        pos, rate, channels, bits, data_len = 12, None, None, None, None
        while pos + 8 <= len(raw):
            cid = raw[pos:pos + 4]
            (size,) = struct.unpack("<I", raw[pos + 4:pos + 8])
            if cid == b"fmt " and size >= 16:
                _, channels, rate, _, _, bits = struct.unpack(
                    "<HHIIHH", raw[pos + 8:pos + 24])
            elif cid == b"data":
                data_len = min(size, len(raw) - pos - 8)
            pos += 8 + size + (size & 1)
        if not (rate and channels and bits and data_len):
            return None
        return int(data_len / (rate * channels * (bits / 8)) * 1000)
    except Exception:  # noqa: BLE001
        return None


def _transcribe(raw, cfg, language):
    """One whisper call. Returns (text, meta) or raises RuntimeError."""
    # sttd decodes RIFF/WAVE only and there is no ffmpeg or sox on this host,
    # so an mp3 would die inside sttd with a less useful message. Say it here.
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise RuntimeError("audio is not RIFF/WAVE; this host has no "
                           "transcoder, send 16-bit PCM WAV")
    headers = {"Content-Type": "audio/wav", "Content-Length": str(len(raw))}
    tok = _token(cfg.get("stt_token_path"))
    if tok:
        headers["Authorization"] = "Bearer " + tok
    path = "/transcribe?language=%s&beam_size=%d" % (
        language, int(cfg.get("stt_beam_size") or 1))
    status, data, _ = _request(cfg["stt_url"], "POST", path, body=raw,
                               headers=headers,
                               timeout=float(cfg.get("stt_timeout_s") or 120))
    try:
        out = json.loads(data or b"{}")
    except Exception:  # noqa: BLE001
        out = {}
    if status != 200:
        raise RuntimeError("sttd %s: %s" % (status, (out.get("error") or
                                                     data[:200].decode("utf-8", "replace"))))
    text = (out.get("text") or "").strip()
    if not text:
        # Whisper legitimately returns nothing for silence. That is an empty
        # utterance, not a broken service, and the caller needs to know which.
        raise RuntimeError("whisper transcribed no speech (silence?)")
    return text, {"stt_ms": out.get("stt_ms"), "audio_ms": out.get("audio_ms"),
                  "language": out.get("language"), "rtf": out.get("rtf")}


def _audio_sites(body, path):
    """Every place audio can ride in, as (getter, setter) over text + clips.

    /api/generate carries `audio` beside `prompt`; /api/chat carries it on a
    message beside `content`. Both are the `images` convention, one layer over.
    """
    sites = []
    if isinstance(body.get("audio"), list) and body["audio"]:
        sites.append(("body", body, "prompt", "audio"))
    for i, msg in enumerate(body.get("messages") or []):
        if isinstance(msg, dict) and isinstance(msg.get("audio"), list) and msg["audio"]:
            sites.append(("messages[%d]" % i, msg, "content", "audio"))
    return sites


def apply_ears(body, cfg, path, note):
    """Replace inbound audio clips with their transcripts, in place.

    Returns None on success, or (status, payload) when the request had nothing
    but audio and the audio could not be transcribed.
    """
    sites = _audio_sites(body, path)
    if not sites:
        return None
    language = (body.get("language") or (body.get("options") or {}).get("language")
                or cfg.get("stt_language_default") or "en")
    heard, errors = [], []
    for where, holder, text_key, audio_key in sites:
        clips = holder.pop(audio_key, []) or []
        transcripts = []
        for n, clip in enumerate(clips):
            try:
                raw = _decode_b64(clip)
                text, meta = _transcribe(raw, cfg, str(language).lower())
            except Exception as exc:  # noqa: BLE001
                _bump("stt_errors")
                errors.append("%s[%d]: %s" % (where, n, exc))
                continue
            _bump("heard")
            transcripts.append(text)
            heard.append({"at": where, "chars": len(text), "text": text[:200],
                          **{k: v for k, v in meta.items() if v is not None}})
        if transcripts:
            joined = " ".join(transcripts)
            existing = holder.get(text_key)
            holder[text_key] = (existing.rstrip() + "\n\n" + joined
                                if isinstance(existing, str) and existing.strip()
                                else joined)
    if heard:
        note["heard"] = heard
    if errors:
        note["stt_errors"] = errors
        note["stt_url"] = cfg.get("stt_url")
    if errors and not heard and not _has_any_text(body, path):
        # Audio-only request, no transcript: there is no utterance to answer.
        return 502, {"error": "glados could not hear: %s" % "; ".join(errors),
                     "sense": "hearing", "backend": cfg.get("stt_url")}
    return None


def _has_any_text(body, path):
    if isinstance(body.get("prompt"), str) and body["prompt"].strip():
        return True
    for msg in body.get("messages") or []:
        if isinstance(msg, dict) and msg.get("role") != "system":
            if isinstance(msg.get("content"), str) and msg["content"].strip():
                return True
    return False


# ---------------------------------------------------------------- eyes -----

def _image_sites(body):
    sites = []
    if isinstance(body.get("images"), list) and body["images"]:
        sites.append(("body", body, "prompt", "images"))
    for i, msg in enumerate(body.get("messages") or []):
        if isinstance(msg, dict) and isinstance(msg.get("images"), list) and msg["images"]:
            sites.append(("messages[%d]" % i, msg, "content", "images"))
    return sites


def _caption(image_b64, cfg, placement):
    """One moondream call through the same backend and placement policy.

    Going through apply_placement_policy rather than hand-rolling the body is
    the point: the delegate inherits the `vision_num_gpu` pin and the
    keep_alive clamp that stop moondream from pinning GPU 0 away from the
    voice stack - which is exactly the trap this service already guards.
    """
    delegate = {
        "model": cfg.get("vision_delegate_model") or "moondream",
        "prompt": cfg.get("vision_delegate_prompt")
                  or "Describe this image in one or two plain factual sentences.",
        "images": [image_b64],
        "stream": False,
        # The caption is evidence, not performance. GLaDOS does the sneering
        # afterwards; moondream should report what is actually there.
        "options": {"temperature": 0.0, "num_predict":
                    int(cfg.get("vision_delegate_num_predict") or 80)},
    }
    delegate, _ = placement(delegate)
    raw = json.dumps(delegate).encode()
    status, data, _ = _request(
        cfg["backend"], "POST", "/api/generate", body=raw,
        headers={"Content-Type": "application/json", "Content-Length": str(len(raw))},
        timeout=float(cfg.get("vision_delegate_timeout_s") or 180))
    try:
        out = json.loads(data or b"{}")
    except Exception:  # noqa: BLE001
        out = {}
    if status != 200 or out.get("error"):
        raise RuntimeError("moondream %s: %s" % (status, out.get("error")
                           or data[:200].decode("utf-8", "replace")))
    text = (out.get("response") or "").strip()
    if not text:
        raise RuntimeError("moondream returned an empty caption")
    return text


def apply_eyes(body, cfg, note, placement):
    """Caption inbound images with moondream and fold them into the text.

    qwen2.5 is not a vision model: handed `images` it ignores them at best.
    So for `glados` the image goes to moondream first and arrives at the
    language layer as a sentence. The `images` key is then REMOVED - leaving it
    would send a payload the text model cannot use.

    Returns None, or (status, payload) when the image was the only content.
    """
    sites = _image_sites(body)
    if not sites:
        return None
    seen, errors = [], []
    for where, holder, text_key, image_key in sites:
        images = holder.pop(image_key, []) or []
        captions = []
        for n, img in enumerate(images):
            try:
                _decode_b64(img)  # validate and cap before spending a GPU call
                caption = _caption(img if isinstance(img, str) else
                                   base64.b64encode(img).decode(), cfg, placement)
            except Exception as exc:  # noqa: BLE001
                _bump("vision_errors")
                errors.append("%s[%d]: %s" % (where, n, exc))
                continue
            _bump("seen")
            captions.append(caption)
            seen.append({"at": where, "caption": caption[:300]})
        if captions:
            label = cfg.get("vision_inject_label") or "What you can see"
            block = "\n\n".join("%s: %s" % (label, c) if len(captions) == 1
                                else "%s (%d): %s" % (label, i + 1, c)
                                for i, c in enumerate(captions))
            existing = holder.get(text_key)
            holder[text_key] = (existing.rstrip() + "\n\n" + block
                                if isinstance(existing, str) and existing.strip()
                                else block)
    if seen:
        note["seen"] = seen
    if errors:
        note["vision_errors"] = errors
    if errors and not seen and not _has_any_text(body, "")\
            and not note.get("heard"):
        return 502, {"error": "glados could not see: %s" % "; ".join(errors),
                     "sense": "sight",
                     "model": cfg.get("vision_delegate_model") or "moondream"}
    return None


# --------------------------------------------------------------- voice -----

def wants_voice(body):
    """`speak: true` at the top level, or in options for callers that only
    have an options bag to work with (n8n's Ollama node is one)."""
    for src in (body, body.get("options") or {}):
        if isinstance(src, dict) and "speak" in src:
            v = src["speak"]
            if isinstance(v, bool):
                return v
            if isinstance(v, str):
                return v.strip().lower() in ("1", "true", "yes", "on")
            return bool(v)
    return False


def voice_options(body):
    """Lift `voice`/`speed` out of the body before strip_sense_keys removes it.

    apply_voice runs after the generation, by which point the body has been
    cleaned for ollama - so the render settings have to be taken while they
    still exist. This returns the same shape apply_voice reads.
    """
    opts = body.get("options") if isinstance(body.get("options"), dict) else {}
    return {"voice": body.get("voice") or opts.get("voice"),
            "speed": body.get("speed") or opts.get("speed")}


def strip_sense_keys(body):
    """Remove our extensions before the body goes to ollama.

    ollama is not promised to ignore unknown top-level keys, and a 400 from the
    backend on a field WE invented would be our bug surfacing as hers.
    """
    removed = []
    for key in ("speak", "voice", "speed", "language", "audio"):
        if key in body:
            body.pop(key)
            removed.append(key)
    opts = body.get("options")
    if isinstance(opts, dict):
        for key in ("speak", "voice", "speed", "language"):
            if key in opts:
                opts.pop(key)
                removed.append("options." + key)
    for msg in body.get("messages") or []:
        if isinstance(msg, dict) and "audio" in msg:
            msg.pop("audio")
            removed.append("message.audio")
    return removed


def reply_text(out):
    """The assistant's words out of either API's response shape."""
    if not isinstance(out, dict):
        return ""
    msg = out.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("content"), str):
        return msg["content"]
    if isinstance(out.get("response"), str):
        return out["response"]
    choices = out.get("choices")
    if isinstance(choices, list) and choices:
        m = (choices[0] or {}).get("message") or {}
        if isinstance(m.get("content"), str):
            return m["content"]
    return ""


def apply_voice(out, body, cfg, note):
    """Render the reply with Kokoro and attach it to the response, in place.

    Additive fields only (`audio`, `audio_*`); nothing existing is touched, so
    a caller that asked to be spoken to and a caller that did not read the same
    response shape apart from the extra keys.
    """
    text = reply_text(out)
    if not text.strip():
        note["tts_skipped"] = "empty_reply"
        return
    voice = (body.get("voice") or (body.get("options") or {}).get("voice")
             or cfg.get("glados_voice"))
    try:
        speed = float(body.get("speed") or (body.get("options") or {}).get("speed")
                      or cfg.get("glados_speed") or 1.0)
    except (TypeError, ValueError):
        speed = float(cfg.get("glados_speed") or 1.0)
    payload = json.dumps({"text": text, "voice": voice, "speed": speed}).encode()
    headers = {"Content-Type": "application/json", "Content-Length": str(len(payload))}
    tok = _token(cfg.get("tts_token_path"))
    if tok:
        headers["Authorization"] = "Bearer " + tok
    try:
        status, data, hdrs = _request(
            cfg["tts_url"], "POST", "/speak", body=payload, headers=headers,
            timeout=float(cfg.get("tts_timeout_s") or 120))
        if status != 200:
            raise RuntimeError("ttsd %s: %s" % (status,
                               data[:200].decode("utf-8", "replace")))
        if not data:
            raise RuntimeError("ttsd returned an empty body")
    except Exception as exc:  # noqa: BLE001
        # Kokoro is the one sense whose failure is always recoverable: the
        # words exist, they just arrive silent. Never drop the reply for it.
        _bump("tts_errors")
        out["audio_error"] = str(exc)[:300]
        note["tts_error"] = str(exc)[:200]
        note["tts_url"] = cfg.get("tts_url")
        return
    _bump("spoken")
    out["audio"] = base64.b64encode(data).decode()
    out["audio_format"] = "wav"
    out["audio_voice"] = voice
    out["audio_speed"] = speed
    out["audio_bytes"] = len(data)
    rate = hdrs.get("x-tts-rate") or hdrs.get("x-tts-native-rate")
    if rate:
        try:
            out["audio_rate"] = int(rate)
        except ValueError:
            pass
    ms = wav_duration_ms(data)
    if ms is not None:
        out["audio_ms"] = ms
    note["spoken"] = {"voice": voice, "speed": speed, "bytes": len(data),
                      "audio_ms": ms, "chars": len(text)}


# -------------------------------------------------------------- top level ---

def apply_senses(body, cfg, path, placement):
    """Pre-pass: turn audio into words and images into words.

    Mutates `body` in place. Returns (note, error) where error is
    (status, payload) if the request can no longer be answered honestly.
    """
    note = {}
    try:
        err = apply_ears(body, cfg, path, note)
        if err:
            return note, err
        err = apply_eyes(body, cfg, note, placement)
        if err:
            return note, err
    except Exception as exc:  # noqa: BLE001
        # An unforeseen bug in this module must not cost her her voice: log it
        # and let the request through as whatever text survived.
        note["senses_error"] = "%s: %s" % (type(exc).__name__, exc)
    return note, None


def health(cfg):
    """Per-sense liveness for /health. Never raises; a dead sense reads dead."""
    out = {
        "multimodal": bool(cfg.get("multimodal")),
        "models": cfg.get("multimodal_models") if cfg.get("multimodal") else None,
        "counters": dict(COUNTERS),
    }
    if not cfg.get("multimodal"):
        return out
    out["sight"] = {"engine": cfg.get("vision_delegate_model") or "moondream",
                    "backend": cfg.get("backend")}
    for sense, key, probe in (("hearing", "stt_url", "/health"),
                              ("voice", "tts_url", "/health")):
        info = {"backend": cfg.get(key)}
        t0 = time.time()
        try:
            status, data, _ = _request(cfg[key], "GET", probe, timeout=2.0)
            body = json.loads(data or b"{}")
            info["ok"] = status == 200 and bool(body.get("ok"))
            info["engine"] = body.get("engine") or body.get("model")
            info["device"] = body.get("device")
            info["probe_ms"] = round((time.time() - t0) * 1000)
        except Exception as exc:  # noqa: BLE001
            info["ok"] = False
            info["error"] = "%s: %s" % (type(exc).__name__, exc)
            info["probe_ms"] = round((time.time() - t0) * 1000)
        out[sense] = info
    out["voice"]["voice"] = cfg.get("glados_voice")
    return out
