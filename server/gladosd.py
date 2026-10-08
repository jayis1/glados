#!/usr/bin/env python3
"""paperclip-gladosd - GLaDOS's language brain (Paperclip IST-270).

Drop-in replacement for an Ollama host that died. It is a thin,
Ollama-compatible front end over a local ollama daemon, and it exists for three
reasons that a bare `ollama serve` cannot cover:

  1. Three ports.  The 22 live n8n nodes call .73 on :11435 (18 nodes, /api/chat),
     :11436 (moondream, /api/generate and /api/chat) and :11434 (1 node, /api/chat).
     Serving all three means the n8n re-point is a pure host substitution.

  2. Device placement.  One ollama backend (fenced to GPU 0 by
     CUDA_VISIBLE_DEVICES in the systemd drop-in), because the Ollama API has
     no per-request device selector - only a layer count.  gladosd routes by
     model and rewrites `options` per route:
       - text models   -> CPU, pinned to num_thread=10.  The thread count is
         the whole ballgame on this box and the default is pathological: our
         cgroup has 20 CPUs spanning BOTH NUMA nodes but only 10 full physical
         cores, so letting ollama use all 20 makes every token pay cross-socket
         traffic.  Measured on qwen2.5:7b-instruct Q4_K_M, 120-token decode:

             default (20 threads)   1.35 tok/s
             num_thread=10          7.39 tok/s     <- 5.5x, this is what we set
             both T400s, 29/29      7.06 tok/s        full GPU offload

         So full offload onto the two T400s is NOT faster than the tuned CPU -
         a T400 is a 64-bit-bus card and decode is bandwidth-bound, which also
         makes flash attention a no-op here (7.42 vs 7.39, measured).  Since it
         buys nothing, text stays on the CPU and the T400s stay entirely free
         for IST-269's fly simulation, which does run them at ~98%.  Putting
         the 7B there would have cost both workloads and gained neither.
       - vision models -> GPU 0, with keep_alive CAPPED.  The live
         "Front door -> Discord (GLaDOS)" node sends keep_alive: -1, which
         would pin moondream's VRAM forever and starve the voice stack
         (paperclip-sttd + paperclip-ttsd) that owns GPU 0.  We clamp it.

  3. /health + a structured event log, like paperclip-sttd / paperclip-ttsd.

  4. ONE MODEL CALLED `glados`.  senses.py folds every other model on this
     host in behind that single name: an `images` payload is captioned by
     moondream, an `audio` payload is transcribed by whisper, and `speak:true`
     comes back as a Kokoro WAV - the caller never names any of them.  Fenced
     to `multimodal_models` (just `glados`), so the tag the 22 live n8n nodes
     call stays plain text.  See senses.py for what "one model" does and does
     not mean: it is one API over five engines, not one set of weights.

Everything else is proxied to ollama byte-for-byte, including streaming.
"""

import json
import os
import socket
import sys
import threading
import time
import uuid
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import ThreadingMixIn

import afferent
import ha_afferent
import mood
import senses

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("GLADOSD_CONFIG", os.path.join(HERE, "config.json"))

DEFAULTS = {
    "listen_host": "0.0.0.0",
    "listen_ports": [11434, 11435, 11436],
    # The single ollama daemon behind us, fenced to GPU 0 by its systemd
    # drop-in. Every key the request path reads must exist here, so that a
    # missing or truncated config.json degrades to working defaults instead of
    # raising KeyError per request.
    "backend": "127.0.0.1:11501",
    "backend_timeout_s": 600,
    # Models whose name starts with one of these take the vision route.
    "vision_model_prefixes": ["moondream", "llava", "qwen2-vl", "qwen2.5-vl"],
    # 0 -> CPU-only, which is deliberate and measured; see the module docstring.
    # null would let ollama decide, and with GPU 0 visible it would try to put
    # a 4.7 GiB model into GPU 0's ~3.4 GiB of leftovers alongside the voice
    # stack, then thrash.
    "text_num_gpu": 0,
    # Resident forever: a cold load of the 7B measured 63 s on this disk, which
    # on its own blows every n8n node's timeout.
    "text_keep_alive": -1,
    # ollama's own estimate leaves moondream partly on the CPU (18/25 layers
    # measured) because it reserves the 900 MiB worst case of the vision
    # encoder from the layer budget; on an AVX1 CPU those 7 layers cost ~80 s.
    # Force the whole model onto GPU 0.
    "vision_num_gpu": 99,
    # Hard cap for vision models so GPU 0 goes back to the voice stack.
    "vision_keep_alive_max_s": 300,
    # 10 = the number of full physical cores in our cgroup, and the measured
    # knee: 4t 3.88, 6t 5.93, 8t 7.71, 10t 8.18, 12t 7.80, 20t 1.54 tok/s.
    # Letting ollama pick (all 20 CPUs, across both NUMA nodes) is the 1.54
    # case. Raising this is not a free win; it collapses.
    "text_num_thread": 10,
    "event_log": "/var/log/paperclip-gladosd/events.jsonl",
    "event_log_max_bytes": 16 * 1024 * 1024,
    # ---- layer 2: the fly's mood, coupled in by mood.apply_mood_policy() ---
    # OFF by default, and that is the shipped default rather than a stub.
    # Coupling makes her replies non-reproducible BY DESIGN (same doorbell,
    # different line), which amends the "host swap with no behaviour change"
    # acceptance on IST-266, so it stays off until the sensory ingest exists:
    # a constant state line on a resting network would be pure theatre.
    # One flag plus a restart returns to exactly today's behaviour.
    "mood_coupling": False,
    "mood_url": "127.0.0.1:9099",
    "mood_token_path": "/opt/paperclip-glados/token",
    # Short on purpose. moodd is on loopback and /mood only reads an already
    # published snapshot; if it cannot answer in this long her reply goes out
    # uncoupled rather than late. The fly never adds latency to her mouth.
    "mood_timeout_s": 0.75,
    "mood_cache_ms": 1000,
    # The sim publishes every ~100 ms of simulated time, so anything older
    # than this is a frozen vector, not a mood.
    "mood_max_age_s": 10,
    # arousal may only RAISE temperature, by at most this much, never past
    # mood_temp_max, and never above what the node chose for itself.
    "mood_temp_headroom": 0.3,
    "mood_temp_max": 0.9,
    # Which models get the fly. `glados` is the fly-centred model created by
    # Modelfile.glados; `qwen2.5:7b-instruct` - the tag every one of the 22
    # live n8n nodes names - is deliberately absent, so the IST-271 re-point
    # stays the byte-identical host swap IST-266 accepted. Both answers are
    # true at once, and moving one node onto the fly later is a one-word edit.
    "mood_models": ["glados"],
    # ---- layer 2a: the ingest (afferent.py) --------------------------------
    # Her own request traffic -> hearing_JO. No credential needed, unlike the
    # HA-sourced senses, so this is the one that can run today. Independent of
    # mood_coupling on purpose: the fly should hold a real state whether or not
    # anything is currently reading it.
    "mood_ingest": True,
    "ingest_site": "hearing_JO",
    "ingest_tick_s": 2.0,
    "ingest_tau_s": 90.0,
    "ingest_kick": 0.06,
    # ~0.3 saturates a sensory site per engine.set_drive, so this is "loud
    # building", not "pinned".
    "ingest_max": 0.4,
    "ingest_epsilon": 0.005,
    "ingest_floor": 0.002,
    "ingest_timeout_s": 2.0,
    # ---- layer 2b: the HA-sourced senses (ha_afferent.py) ------------------
    # IST-280. Doorbell/motion/occupancy -> mechanosensory, temperature change
    # -> hygro_thermo. Both sites were probed on the live service before being
    # wired; see measurements/mechanosensory.json and hygro_thermo.json, and
    # the AXIS_SPAN comment in mood.py for which axes each one actually moves.
    "ha_ingest": True,
    "ha_env_path": "/opt/paperclip-glados/secrets/ha.env",
    "ha_poll_s": 5.0,
    "ha_timeout_s": 10.0,
    "ha_post_timeout_s": 2.0,
    # First poll looks back this far, so a restart does not lose the last few
    # seconds of events. Later polls use the cursor.
    "ha_backfill_s": 30.0,
    # Touch. 28 "on" transitions across these six entities in the last 24h.
    # The measured ladder at a kick of 0.02, and the measurement corrected an
    # earlier reading of it: at the 30 s PLATEAU one event is raw arousal
    # 0.2238, which rescales past 1.0 and reads `alert`. Sampled at 12 s it
    # read 0.0629 and then 0.0275 on two runs, which looked like a threshold
    # coin-flip and was really just mid-rise - mechanosensory takes ~30 s to
    # plateau (measurements/rise_time.json). So: any door event reaches
    # `alert` within about half a minute and fades over a couple of minutes;
    # a flurry at the cap of 0.20 goes further, to `disturbed`, carried by
    # agitation (raw 0.19). Capped
    # well short of the 0.4 afferent.py uses for hearing, because the same
    # drive is 5x stronger here: 0.4 would pin her at maximum permanently.
    "ha_touch_entities": [
        "binary_sensor.front_door_visitor",
        "binary_sensor.front_door_person",
        "binary_sensor.front_door_vehicle",
        "binary_sensor.front_door_pet",
        "binary_sensor.front_door_bevaegelse",
        "binary_sensor.presence_sensor_fp2_6010_presence_sensor_1",
    ],
    "ha_touch_kick": 0.02,
    "ha_touch_tau_s": 120.0,
    "ha_touch_max": 0.20,
    # Thermo. NAMED AS THE STAND-IN IT IS: every room climate sensor in this
    # house reports `unavailable` right now - both thermostats, their external
    # probes, the living-room sensor and the only humidity sensor - so her
    # thermal sense is the heat coming off the hardware she runs on. Those are
    # live and they move (1.0-3.7 degC mean step). If the thermostats come
    # back, put them at the front of this list.
    "ha_thermo_entities": [
        "sensor.nasty_temperature",
        "sensor.disk_box1_ct2000p3ssd8_temperature",
        "sensor.disk_box3_ct2000p3ssd8_temperature",
        "sensor.nasty_drive_1_temperature",
    ],
    "ha_thermo_kick": 0.01,
    # 60 s, not the 180 s this started at, and the reason is a measurement.
    # hygro_thermo is a PHASIC nerve: driven at a constant 0.20 it rises to
    # reinforcement 0.177 by 12 s and is back at exactly 0.0000 by 30 s, where
    # it stays (measurements/rise_time.json). It answers the ONSET of a change
    # and then habituates completely - which is what a real thermoreceptor
    # does, and it is why `curious` is phrased "briefly interesting".
    # mechanosensory is tonic by contrast: at 0.06 it climbs to a 0.58 plateau
    # in ~30 s and holds.
    # So a long tau is actively harmful here: it keeps the drive elevated long
    # after the nerve has stopped listening, which makes the NEXT temperature
    # change a smaller step and therefore a weaker signal. A tau near the
    # habituation time means each change gets its own clean onset.
    "ha_thermo_tau_s": 60.0,
    "ha_thermo_max": 0.25,
    "ha_epsilon": 0.005,
    "ha_floor": 0.002,
    # ---- the senses: one model called `glados` (senses.py) -----------------
    # "can we cobble every model we have running in to the GLaDOS model."
    # Yes, at the API: `glados` takes an image (moondream), takes speech
    # (whisper) and answers out loud (Kokoro) without the caller naming any of
    # them. Fenced to the same model list the fly is fenced to, so the tag the
    # 22 live n8n nodes call is untouched and IST-271 stays a pure host swap.
    #
    # ON by default, unlike mood_coupling, and that is not an inconsistency:
    # coupling changes what an existing caller gets back, so it had to be
    # opt-in. The senses change nothing unless a request actually carries
    # `audio`, `images` or `speak`, and the only model they apply to did not
    # exist before today. There is no behaviour here to regress.
    "multimodal": True,
    "multimodal_models": ["glados"],
    # Ears: paperclip-sttd, whisper large-v3 on GPU 0. Generous timeout
    # because this is per-clip, not per-token, and a 30 s voicemail is real.
    "stt_url": "127.0.0.1:9097",
    "stt_token_path": "/opt/paperclip-stt/token",
    "stt_language_default": "en",
    "stt_beam_size": 1,
    "stt_timeout_s": 120,
    # Voice: paperclip-ttsd, Kokoro on GPU 0. af_jessica is the one unassigned
    # female voice in the IST-266 Matrix map (it is that map's DEFAULT_VOICE,
    # claimed by no agent) and it is graded flat - which is the right register
    # for a `portal_outsider` GLaDOS. Picking it claims nothing from anyone.
    "tts_url": "127.0.0.1:9098",
    "tts_token_path": "/opt/paperclip-tts/token",
    "tts_timeout_s": 120,
    "glados_voice": "af_jessica",
    "glados_speed": 0.9,
    # Eyes: moondream through the same backend, so the delegate inherits the
    # GPU-0 pin and the keep_alive clamp instead of re-deriving them.
    "vision_delegate_model": "moondream",
    "vision_delegate_prompt": "Describe this image in one or two plain factual sentences.",
    "vision_delegate_num_predict": 80,
    "vision_delegate_timeout_s": 180,
    "vision_inject_label": "What you can see",
}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH) as fh:
            cfg.update(json.load(fh))
    except FileNotFoundError:
        pass
    return cfg


CFG = load_config()
STARTED = time.time()
COUNTERS = {"requests": 0, "errors": 0, "text_routed": 0, "vision_routed": 0, "keep_alive_clamped": 0}
_log_lock = threading.Lock()
_counter_lock = threading.Lock()


def log_event(**fields):
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "svc": "paperclip-gladosd"}
    rec.update(fields)
    line = json.dumps(rec, ensure_ascii=False, default=str)
    path = CFG["event_log"]
    with _log_lock:
        try:
            if os.path.exists(path) and os.path.getsize(path) > CFG["event_log_max_bytes"]:
                os.replace(path, path + ".1")
            with open(path, "a") as fh:
                fh.write(line + "\n")
        except OSError:
            pass
    print(line, flush=True)


def bump(key, n=1):
    with _counter_lock:
        COUNTERS[key] = COUNTERS.get(key, 0) + n


def is_vision_model(model):
    m = (model or "").lower()
    return any(m.startswith(p) for p in CFG["vision_model_prefixes"])


def keep_alive_seconds(value):
    """Ollama accepts -1, a number of seconds, or a duration string like '5m'."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    try:
        return float(s)
    except ValueError:
        pass
    units = {"ns": 1e-9, "us": 1e-6, "ms": 1e-3, "s": 1, "m": 60, "h": 3600}
    for suffix in ("ms", "ns", "us", "h", "m", "s"):
        if s.endswith(suffix):
            try:
                return float(s[: -len(suffix)]) * units[suffix]
            except ValueError:
                return None
    return None


def apply_placement_policy(body):
    """Rewrite an /api/chat or /api/generate body to pin the model to a device.

    Returns (new_body, note) where note describes what was changed, for the log.
    """
    model = body.get("model")
    note = {"model": model}
    opts = dict(body.get("options") or {})

    if is_vision_model(model):
        note["route"] = "vision"
        note["backend"] = CFG["backend"]
        bump("vision_routed")
        if CFG["vision_num_gpu"] is not None:
            opts.setdefault("num_gpu", CFG["vision_num_gpu"])
        requested = keep_alive_seconds(body.get("keep_alive"))
        cap = CFG["vision_keep_alive_max_s"]
        # -1 (forever) and anything above the cap both get clamped.
        if requested is None or requested < 0 or requested > cap:
            if body.get("keep_alive") is not None:
                note["keep_alive_requested"] = body.get("keep_alive")
                note["keep_alive_clamped_to"] = cap
                bump("keep_alive_clamped")
            body["keep_alive"] = cap
    else:
        note["route"] = "text"
        note["backend"] = CFG["backend"]
        bump("text_routed")
        if CFG["text_num_gpu"] is not None:
            opts["num_gpu"] = CFG["text_num_gpu"]
        if CFG["text_num_thread"]:
            opts.setdefault("num_thread", CFG["text_num_thread"])
        if "keep_alive" not in body and CFG["text_keep_alive"] is not None:
            body["keep_alive"] = CFG["text_keep_alive"]

    if opts:
        body["options"] = opts
    return body, note


def redact(body):
    """Request echo for the log: keep the shape, drop the base64 payloads."""
    out = {}
    for key, value in body.items():
        if key == "images" and isinstance(value, list):
            out[key] = ["<base64 %d bytes>" % len(str(v)) for v in value]
        elif key == "messages" and isinstance(value, list):
            msgs = []
            for m in value:
                m2 = {k: v for k, v in m.items() if k != "images"}
                if "images" in m:
                    m2["images"] = ["<base64 %d bytes>" % len(str(v)) for v in m["images"]]
                if isinstance(m2.get("content"), str) and len(m2["content"]) > 200:
                    m2["content"] = m2["content"][:200] + "...[%d]" % len(m["content"])
                msgs.append(m2)
            out[key] = msgs
        elif key == "prompt" and isinstance(value, str) and len(value) > 200:
            out[key] = value[:200] + "...[%d]" % len(value)
        else:
            out[key] = value
    return out


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "paperclip-gladosd"

    # BaseHTTPRequestHandler logs to stderr per request; we have our own log.
    def log_message(self, fmt, *args):
        pass

    # ---- helpers -----------------------------------------------------------
    def _send_json(self, code, payload):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self):
        length = self.headers.get("Content-Length")
        if length:
            return self.rfile.read(int(length))
        if (self.headers.get("Transfer-Encoding") or "").lower() == "chunked":
            chunks = []
            while True:
                size_line = self.rfile.readline().strip()
                size = int(size_line.split(b";")[0], 16)
                if size == 0:
                    self.rfile.readline()
                    break
                chunks.append(self.rfile.read(size))
                self.rfile.readline()
            return b"".join(chunks)
        return b""

    # ---- dispatch ----------------------------------------------------------
    def do_GET(self):
        if self.path.split("?")[0] in ("/health", "/healthz"):
            return self._health()
        if self.path.split("?")[0] == "/capabilities":
            return self._capabilities()
        if self.path == "/":
            data = b"Ollama is running"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        return self._proxy(b"")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_DELETE(self):
        return self._proxy(self._read_body())

    def do_POST(self):
        body = self._read_body()
        path = self.path.split("?")[0]
        if path in ("/api/chat", "/api/generate", "/v1/chat/completions"):
            return self._inference(path, body)
        return self._proxy(body)

    def _health(self):
        backend_ok, models, err = False, [], None
        try:
            conn = HTTPConnection(CFG["backend"], timeout=5)
            conn.request("GET", "/api/tags")
            resp = conn.getresponse()
            payload = json.loads(resp.read() or b"{}")
            models = sorted(m.get("name", "") for m in payload.get("models", []))
            backend_ok = resp.status == 200
        except Exception as exc:  # noqa: BLE001 - health must never raise
            err = "%s: %s" % (type(exc).__name__, exc)
        loaded = []
        try:
            conn = HTTPConnection(CFG["backend"], timeout=5)
            conn.request("GET", "/api/ps")
            loaded = [
                {"model": m.get("name"), "size_vram": m.get("size_vram"), "size": m.get("size")}
                for m in json.loads(conn.getresponse().read() or b"{}").get("models", [])
            ]
        except Exception:  # noqa: BLE001
            pass
        self._send_json(200 if backend_ok else 503, {
            "status": "ok" if backend_ok else "degraded",
            "service": "paperclip-gladosd",
            "issue": "IST-270",
            "uptime_s": round(time.time() - STARTED, 1),
            "backend": CFG["backend"],
            "backend_error": err,
            "listen_ports": CFG["listen_ports"],
            "models_present": models,
            "models_loaded": loaded,
            "placement": {
                "text": "cpu (options.num_gpu=%s, num_thread=%s, keep_alive=%s)" % (
                    CFG["text_num_gpu"], CFG["text_num_thread"],
                    CFG["text_keep_alive"]),
                "vision": "gpu0 (keep_alive capped at %ss)" % CFG["vision_keep_alive_max_s"],
                "vision_model_prefixes": CFG["vision_model_prefixes"],
            },
            "mood": mood.health(CFG),
            "ingest": afferent.health(CFG),
            "ha_ingest": ha_afferent.health(CFG),
            "senses": senses.health(CFG),
            "counters": dict(COUNTERS),
        })

    def _capabilities(self):
        """What `glados` is, in one place, with the borrowing stated.

        Exists because "one model" is a claim that should be checkable from
        outside: this lists every engine behind the name and which part of her
        each one supplies, including the two we did not build.
        """
        multimodal = bool(CFG.get("multimodal"))
        self._send_json(200, {
            "model": (CFG.get("multimodal_models") or ["glados"])[0],
            "service": "paperclip-gladosd",
            "issue": "IST-270",
            "one_model": multimodal,
            "honest_note": "One name and one API over five engines, not one "
                           "set of weights - those architectures cannot be "
                           "merged without training, which this host cannot do.",
            "engines": [
                {"part": "state", "engine": "drosophila connectome (164,587 "
                 "neurons)", "where": CFG.get("mood_url"), "device": "2x T400",
                 "ours": True, "enabled": bool(CFG.get("mood_coupling"))},
                {"part": "words", "engine": "qwen2.5:7b-instruct",
                 "where": CFG.get("backend"), "device": "cpu/10 threads",
                 "ours": False, "enabled": True},
                {"part": "sight", "engine": CFG.get("vision_delegate_model"),
                 "where": CFG.get("backend"), "device": "gpu0",
                 "ours": False, "enabled": multimodal},
                {"part": "hearing", "engine": "whisper large-v3",
                 "where": CFG.get("stt_url"), "device": "gpu0",
                 "ours": False, "enabled": multimodal},
                {"part": "voice", "engine": "kokoro",
                 "where": CFG.get("tts_url"), "device": "gpu0",
                 "ours": False, "enabled": multimodal,
                 "default_voice": CFG.get("glados_voice")},
            ],
            "request_extensions": {
                "audio": "list of base64 RIFF/WAVE clips, top level "
                         "(/api/generate) or on a message (/api/chat); "
                         "replaced by its transcript before generation",
                "images": "already standard; for `glados` they are captioned "
                          "by moondream and folded in as text",
                "speak": "true -> the reply is rendered and returned as "
                         "`audio` (base64 WAV). Requires stream:false.",
                "voice": "a Kokoro voice name; default %s" % CFG.get("glados_voice"),
                "speed": "0.5-2.0, default %s" % CFG.get("glados_speed"),
                "language": "whisper language hint, default %s"
                            % CFG.get("stt_language_default"),
            },
            "fenced_to": CFG.get("multimodal_models"),
            "not_fenced_in": "qwen2.5:7b-instruct - the tag the 22 live n8n "
                             "nodes call keeps plain text behaviour",
            "senses": senses.health(CFG),
        })

    def _inference(self, path, raw):
        rid = uuid.uuid4().hex[:12]
        started = time.time()
        bump("requests")
        try:
            body = json.loads(raw or b"{}")
            if not isinstance(body, dict):
                raise ValueError("body is not a JSON object")
        except Exception as exc:  # noqa: BLE001
            bump("errors")
            log_event(event="bad_request", rid=rid, path=path, port=self.server.server_address[1],
                      error=str(exc))
            return self._send_json(400, {"error": "invalid JSON body: %s" % exc})

        speak, speak_opts = False, {}
        if path == "/v1/chat/completions":
            note = {"model": body.get("model"), "route": "openai-compat-passthrough"}
        else:
            note = {}
            # The senses run FIRST, before placement and before the mood.
            # Hearing a 10 s clip costs seconds, and the fly's state moves in
            # that time: reading the mood afterwards means her state is the one
            # she holds when she starts speaking, not when the audio landed.
            if senses.model_multimodal(body.get("model"), CFG):
                speak = senses.wants_voice(body)
                if speak and bool(body.get("stream")):
                    bump("errors")
                    log_event(event="bad_request", rid=rid, path=path,
                              port=self.server.server_address[1],
                              error="speak with stream:true")
                    return self._send_json(400, {
                        "error": "speak requires stream:false - the spoken WAV "
                                 "is attached to the single response object, "
                                 "and an ndjson stream has nowhere to put it"})
                snote, serr = senses.apply_senses(
                    body, CFG, path, apply_placement_policy)
                note.update(snote)
                speak_opts = senses.voice_options(body)
                removed = senses.strip_sense_keys(body)
                if removed:
                    note["sense_keys_stripped"] = removed
                if serr:
                    bump("errors")
                    log_event(event="sense_failed", rid=rid, path=path,
                              port=self.server.server_address[1],
                              status=serr[0], request=redact(body),
                              latency_ms=round((time.time() - started) * 1000),
                              **note)
                    return self._send_json(*serr)
                note["multimodal"] = True
            body, pnote = apply_placement_policy(body)
            note.update(pnote)
            # She hears every request, whether or not this one is coupled:
            # a fly does not stop hearing the room because the question was
            # addressed to someone else. O(1), no I/O, cannot raise.
            afferent.note_request(body.get("model"))
            # Layer 2. Mutates `body` in place, after placement so the mood
            # has the final word on temperature, and returns {} whenever it
            # changed nothing - including every failure mode it has.
            note.update(mood.apply_mood_policy(
                body, CFG, vision=(note.get("route") == "vision")))
        streaming = bool(body.get("stream"))
        new_raw = json.dumps(body).encode()

        try:
            conn = HTTPConnection(CFG["backend"], timeout=CFG["backend_timeout_s"])
            conn.request("POST", self.path, body=new_raw,
                         headers={"Content-Type": "application/json",
                                  "Content-Length": str(len(new_raw))})
            resp = conn.getresponse()
        except Exception as exc:  # noqa: BLE001
            bump("errors")
            log_event(event="backend_unreachable", rid=rid, path=path,
                      port=self.server.server_address[1], error="%s: %s" % (type(exc).__name__, exc),
                      request=redact(body), latency_ms=round((time.time() - started) * 1000))
            return self._send_json(502, {"error": "gladosd backend unreachable: %s" % exc})

        if streaming:
            self.send_response(resp.status)
            self.send_header("Content-Type", resp.getheader("Content-Type", "application/x-ndjson"))
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            total = 0
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                total += len(chunk)
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            conn.close()
            log_event(event="inference", rid=rid, path=path, port=self.server.server_address[1],
                      client=self.client_address[0], status=resp.status, stream=True,
                      bytes=total, latency_ms=round((time.time() - started) * 1000),
                      request=redact(body), **note)
            return

        payload = resp.read()
        conn.close()

        # Parse before answering, because `speak` has to add the rendered WAV
        # to this object. A body we cannot parse is forwarded untouched - we
        # are a proxy first and ollama's reply is not ours to withhold.
        out = None
        try:
            out = json.loads(payload)
            if not isinstance(out, dict):
                out = None
        except Exception:  # noqa: BLE001
            out = None
        if speak and out is not None and resp.status == 200:
            senses.apply_voice(out, speak_opts, CFG, note)
            payload = json.dumps(out).encode()
        elif speak:
            note["tts_skipped"] = ("unparsable_response" if out is None
                                   else "backend_status_%d" % resp.status)

        self.send_response(resp.status)
        self.send_header("Content-Type", resp.getheader("Content-Type", "application/json"))
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

        fields = {}
        try:
            if out is None:
                raise ValueError("unparsable response")
            text = (out.get("message") or {}).get("content") or out.get("response") or ""
            fields["reply_chars"] = len(text)
            fields["reply"] = text[:200]
            for k in ("prompt_eval_count", "eval_count", "eval_duration",
                      "prompt_eval_duration", "load_duration", "total_duration"):
                if k in out:
                    fields[k] = out[k]
            if out.get("eval_count") and out.get("eval_duration"):
                fields["tok_per_s"] = round(out["eval_count"] / (out["eval_duration"] / 1e9), 2)
            if "error" in out:
                fields["backend_error"] = out["error"]
                bump("errors")
        except Exception:  # noqa: BLE001
            pass
        if resp.status >= 400:
            bump("errors")
        log_event(event="inference", rid=rid, path=path, port=self.server.server_address[1],
                  client=self.client_address[0], status=resp.status, stream=False,
                  latency_ms=round((time.time() - started) * 1000),
                  request=redact(body), **note, **fields)

    def _proxy(self, raw):
        started = time.time()
        try:
            conn = HTTPConnection(CFG["backend"], timeout=CFG["backend_timeout_s"])
            headers = {"Content-Type": self.headers.get("Content-Type", "application/json")}
            if raw:
                headers["Content-Length"] = str(len(raw))
            conn.request(self.command, self.path, body=raw or None, headers=headers)
            resp = conn.getresponse()
            payload = resp.read()
            conn.close()
        except Exception as exc:  # noqa: BLE001
            bump("errors")
            log_event(event="proxy_error", path=self.path, port=self.server.server_address[1],
                      error="%s: %s" % (type(exc).__name__, exc))
            return self._send_json(502, {"error": "gladosd backend unreachable: %s" % exc})
        self.send_response(resp.status)
        self.send_header("Content-Type", resp.getheader("Content-Type", "application/json"))
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        log_event(event="proxy", method=self.command, path=self.path,
                  port=self.server.server_address[1], client=self.client_address[0],
                  status=resp.status, bytes=len(payload),
                  latency_ms=round((time.time() - started) * 1000))


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64


def main():
    servers = []
    for port in CFG["listen_ports"]:
        srv = Server((CFG["listen_host"], port), Handler)
        servers.append(srv)
        threading.Thread(target=srv.serve_forever, name="listen-%d" % port, daemon=True).start()
    ingest = afferent.start(CFG)
    ha_ingest = ha_afferent.start(CFG)
    log_event(event="startup", listen="%s:%s" % (CFG["listen_host"], CFG["listen_ports"]),
              backend=CFG["backend"], pid=os.getpid(),
              mood_coupling=bool(CFG["mood_coupling"]),
              mood_models=CFG["mood_models"] if CFG["mood_coupling"] else None,
              mood_ingest=ingest,
              ingest_site=CFG["ingest_site"] if ingest else None,
              ha_ingest=ha_ingest,
              ha_sites=list(ha_afferent.OWNED) if ha_ingest == "running" else None,
              mood_url=CFG["mood_url"] if CFG["mood_coupling"] else None,
              multimodal=bool(CFG.get("multimodal")),
              multimodal_models=(CFG.get("multimodal_models")
                                 if CFG.get("multimodal") else None),
              senses={"stt": CFG.get("stt_url"), "tts": CFG.get("tts_url"),
                      "vision": CFG.get("vision_delegate_model"),
                      "voice": CFG.get("glados_voice")}
                     if CFG.get("multimodal") else None,
              placement={"text_num_gpu": CFG["text_num_gpu"],
                         "text_keep_alive": CFG["text_keep_alive"],
                         "vision_keep_alive_max_s": CFG["vision_keep_alive_max_s"],
                         "text_num_thread": CFG["text_num_thread"]})
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        log_event(event="shutdown")
        for srv in servers:
            srv.shutdown()


if __name__ == "__main__":
    main()
