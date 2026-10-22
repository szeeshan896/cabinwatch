#!/usr/bin/env python3
"""
CabinWatch camera agent.

Run this on any machine that can see a camera: a spare laptop in your baby's
room, the laptop in your cabin, or a small always-on box (a Raspberry Pi)
sitting on the same WiFi as an ONVIF/RTSP camera.

It dials OUT to your server, so the machine can be behind any router on any
network with nothing forwarded and nothing exposed. One agent can run several
cameras at once, each appearing separately on the dashboard.

    python agent/agent.py                  # uses agent/config.py
    python agent/agent.py --only cabin     # just one camera from the config
    python agent/agent.py --list-cameras   # see what this machine can see

What it does per camera
    - reads frames and writes them to this machine's own disk, one file a
      minute, deleting the oldest by itself
    - watches for movement (and optionally for a person, for the cabin)
    - listens for a baby crying or a thud, using the machine's microphone
    - sends frames up only while somebody is watching the dashboard
    - plays back your voice when you press Talk
    - hands over a clip when you open it from the dashboard
    - drives pan/tilt if the camera has a motor
"""

import os
import sys
import ssl
import json
import time
import base64
import queue
import struct
import socket
import argparse
import threading
import datetime
import urllib.parse

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ptz as ptz_mod
import setup_wizard
import local_ui
from resample import Resampler
from denoise import NoiseSuppressor
from recorder import Recorder
from detect import (MotionDetector, PersonDetector, SoundDetector,
                    SceneChangeDetector)

try:
    import websocket          # pip install websocket-client
except ImportError:
    sys.exit("Missing dependency. Run:  pip install -r agent/requirements.txt")

try:
    import sounddevice as sd
except Exception as _audio_error:
    # Not just ImportError: on a machine with no working audio drivers the
    # import itself raises while loading PortAudio. A camera with no sound is
    # still a useful camera, so carry on without it rather than refusing to
    # start at all.
    sd = None
    print(f"[audio] no microphone or speaker support: {_audio_error}")
    print("[audio] movement detection and live video still work; sound "
          "alerts and talking back do not.")

# Mirrors server/protocol.py.
BIN_VIDEO, BIN_AUDIO, BIN_CLIP = 0x01, 0x02, 0x03
AUDIO_RATE = 16000
AUDIO_BLOCK = 1024


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def load_config(force_setup=False):
    """Find the settings, or ask for them.

    Order of preference:
      1. agent/config.py        - hand-written, wins because it can express
                                  far more than the questions can (several
                                  cameras, ONVIF, ignore regions)
      2. cabinwatch.json        - written by the first-run questions, and what
                                  the standalone .exe uses
      3. ask                    - when there is a console to ask on
      4. the example values     - last resort, so --list-cameras still works
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (here, setup_wizard.base_dir()):
        if path not in sys.path:
            sys.path.insert(0, path)

    if force_setup:
        return setup_wizard.run_wizard(probe_cameras, list_microphones)

    try:
        import config as cfg
        return cfg
    except ModuleNotFoundError:
        pass

    saved = setup_wizard.load_json_config()
    if saved is not None:
        return saved

    if setup_wizard.can_ask():
        return setup_wizard.run_wizard(probe_cameras, list_microphones)

    import config_example as cfg
    print("[config] No settings found - using the example values.")
    print("[config] Run this in a terminal to be asked for them, or copy "
          "agent/config_example.py to agent/config.py and edit it.")
    return cfg


# ---------------------------------------------------------------------------
# One camera
# ---------------------------------------------------------------------------
class CameraSession:
    def __init__(self, cam_id, conf, cfg):
        self.cam_id = cam_id
        self.conf = conf
        self.cfg = cfg
        self.name = conf.get("name", cam_id)
        self.source = conf.get("source", 0)

        self.server = cfg.SERVER_URL.rstrip("/")
        self.token = cfg.AGENT_TOKEN

        # -- capture
        self.cap = None
        self.frame = None
        self.frame_at = 0.0
        self.frame_lock = threading.Lock()
        self.status = "starting"
        self.target_fps = float(conf.get("fps", cfg.CAPTURE_FPS))

        # Selectable cameras on this machine, so the dashboard can flip
        # between a built-in webcam and a USB one.
        self.lenses = conf.get("lenses") or []
        self.active_lens = conf.get("active_lens") or str(self.source)

        # -- detection
        self.motion = MotionDetector(
            min_area=conf.get("motion_min_area", cfg.MOTION_MIN_AREA),
            threshold=conf.get("motion_threshold", cfg.MOTION_THRESHOLD),
            mask=conf.get("ignore_regions", []),
        )
        self.person = PersonDetector(
            enabled=conf.get("person_detection", False),
            proto=getattr(cfg, "SSD_PROTO", None),
            model=getattr(cfg, "SSD_MODEL", None),
        )
        # Somebody turning the camera to face a wall protects nobody, and
        # does it silently - so that is worth its own alarm.
        # getattr, not cfg.X: a config.py written before this option existed
        # is still a valid config.py, and must not crash the agent.
        self.tamper = SceneChangeDetector(
            hold_seconds=conf.get(
                "tamper_hold_seconds",
                getattr(cfg, "TAMPER_HOLD_SECONDS", 4.0)),
        ) if conf.get("tamper_detection", True) else None

        # A cooler or fan never stops, which buries the child both for
        # listening and for the alerts - the detector learns the room's
        # level, the fan lifts it, and a cry then has to be far louder than
        # it should to count. Taking the steady part out fixes both.
        self.denoise = bool(conf.get("noise_filter",
                                     getattr(cfg, "NOISE_FILTER", True)))
        self._suppressor = None

        self.sound = SoundDetector(
            rate=AUDIO_RATE,
            sensitivity=conf.get("sound_sensitivity", cfg.SOUND_SENSITIVITY),
            cry_seconds=conf.get("cry_seconds", cfg.CRY_SECONDS),
        )

        self.baby_mode = bool(conf.get("baby_mode", False))
        self.armed = bool(conf.get("armed", True))
        self.motion_on = False
        self.last_motion_at = 0.0
        self.last_sound_at = 0.0
        self.last_moved_at = 0.0
        self._event_times = {}

        # -- recording
        # Each camera may set its own clip length; the config value is the
        # default for the machine.
        segment_seconds = int(conf.get("segment_seconds",
                                       cfg.SEGMENT_SECONDS))
        want_audio = bool(conf.get("audio", True)) and sd is not None

        self.rec = Recorder(
            cam_id,
            clips_dir=cfg.CLIPS_DIR,
            fps=self.target_fps,
            segment_seconds=segment_seconds,
            retention_hours=conf.get("retention_hours", cfg.RETENTION_HOURS),
            event_retention_hours=conf.get("event_retention_hours",
                                           cfg.EVENT_RETENTION_HOURS),
            max_disk_gb=conf.get("max_disk_gb", cfg.MAX_DISK_GB),
            always_record=conf.get("always_record", cfg.ALWAYS_RECORD),
            prebuffer_seconds=cfg.PREBUFFER_SECONDS,
            audio_rate=AUDIO_RATE,
            want_audio=want_audio,
        )

        # -- audio
        self.audio_device = conf.get("audio_device", cfg.AUDIO_DEVICE)
        self.audio_enabled = conf.get("audio", True) and sd is not None
        self.speaker_device = conf.get("speaker_device", cfg.SPEAKER_DEVICE)
        self.speaker_enabled = conf.get("speaker", True) and sd is not None
        self.send_audio = False
        self.talk_queue = queue.Queue(maxsize=80)

        # -- pan / tilt
        self.ptz = ptz_mod.build(conf.get("ptz"), cap=None)
        self.ptz_autostop = ptz_mod.AutoStop(self.ptz)
        self.zoom = 1.0

        # -- streaming to the dashboard
        self.stream_spec = None
        self.stream_lock = threading.Lock()

        # -- websocket
        self.ws = None
        self.ws_lock = threading.Lock()
        self.connected = False
        self._cancelled_xfers = set()

        self._explained = set()
        self._stop = threading.Event()
        self.threads = []

    # =====================================================================
    # websocket plumbing
    # =====================================================================
    def ws_url(self):
        base = self.server
        if base.startswith("https://"):
            base = "wss://" + base[len("https://"):]
        elif base.startswith("http://"):
            base = "ws://" + base[len("http://"):]
        elif not base.startswith(("ws://", "wss://")):
            base = "wss://" + base
        q = urllib.parse.urlencode({
            "token": self.token, "id": self.cam_id,
            "name": self.name, "kind": "agent",
        })
        return f"{base}/ws/camera?{q}"

    def send(self, obj):
        with self.ws_lock:
            if self.ws is None:
                return False
            try:
                self.ws.send(json.dumps(obj))
                return True
            except Exception:
                return False

    def send_binary(self, tag, payload, xfer=None):
        with self.ws_lock:
            if self.ws is None:
                return False
            try:
                if xfer is None:
                    data = bytes([tag]) + payload
                else:
                    data = bytes([tag]) + struct.pack(">I", xfer) + payload
                self.ws.send(data, opcode=websocket.ABNF.OPCODE_BINARY)
                return True
            except Exception:
                return False

    def ws_loop(self):
        backoff = 2
        while not self._stop.is_set():
            try:
                ws = websocket.WebSocketApp(
                    self.ws_url(),
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_close=self._on_close,
                    on_error=self._on_error,
                )
                with self.ws_lock:
                    self.ws = ws
                opts = {}
                if not self.cfg.VERIFY_TLS:
                    opts["sslopt"] = {"cert_reqs": ssl.CERT_NONE}
                ws.run_forever(ping_interval=20, ping_timeout=10, **opts)
            except Exception as e:
                print(f"[{self.cam_id}] websocket error: {e}")
            with self.ws_lock:
                self.ws = None
            self.connected = False
            if self._stop.is_set():
                break
            print(f"[{self.cam_id}] reconnecting in {backoff}s")
            self._stop.wait(backoff)
            backoff = min(30, backoff * 2)

    def _on_open(self, ws):
        self.connected = True
        print(f"[{self.cam_id}] connected to {self.server}")
        self.push_state()

    def _on_close(self, ws, code, msg):
        self.connected = False
        with self.stream_lock:
            self.stream_spec = None
        self.send_audio = False
        if code in (4401, 4400):
            self._explain(code)

    def _on_error(self, ws, error):
        # The handshake is refused before the websocket opens, so the reason
        # arrives as an HTTP status rather than a close code. Each status
        # means something quite different and sending someone after the wrong
        # one costs them an afternoon, so keep them apart.
        status = getattr(error, "status_code", None)

        if status == 403:
            # The server rejected us before accepting. It does that for a bad
            # token and for a bad camera id alike, and from out here the two
            # look the same, so name both rather than guess.
            self._explain(4401)
        elif status == 404:
            # Nothing is serving this path. Almost always the address is
            # wrong, or the host is up but the app is not running yet - a
            # hosting platform answers 404 for a service that never started.
            self._explain_once(
                "404",
                f"[{self.cam_id}] NOT FOUND at {self.server}\n"
                f"    Nothing is answering there yet. Either the address is\n"
                f"    wrong, or the server has not finished deploying.\n"
                f"    Open {self.server}/healthz in a browser: it should say "
                f"'ok'.\n"
                f"    This camera keeps recording locally and will connect by\n"
                f"    itself once the server answers."
            )
        elif status in (500, 502, 503, 504):
            self._explain_once(
                "5xx",
                f"[{self.cam_id}] the server is reachable but not healthy "
                f"(HTTP {status}). It may still be starting up."
            )
        elif status is not None:
            self._explain_once(
                str(status),
                f"[{self.cam_id}] server refused the connection "
                f"(HTTP {status}). Is the address right?"
            )
        elif isinstance(error, (socket.gaierror, ConnectionRefusedError)):
            self._explain_once(
                "unreachable",
                f"[{self.cam_id}] cannot reach {self.server} - check the "
                f"address and this machine's internet."
            )

    def _explain_once(self, key, message):
        # Print each distinct reason once; the retry loop stays quiet after.
        if key in self._explained:
            return
        self._explained.add(key)
        print(message)

    def _explain(self, code):
        if code == 4401:
            self._explain_once(
                "4401",
                f"[{self.cam_id}] REFUSED by the server.\n"
                f"    Either the token does not match, or '{self.cam_id}' is "
                f"not a usable camera id.\n"
                f"    The token must be character for character the same as "
                f"the AGENT_TOKEN\n"
                f"    variable on your server. A camera id may only contain "
                f"letters, numbers,\n"
                f"    - and _ ."
            )
        elif code == 4400:
            self._explain_once(
                "4400",
                f"[{self.cam_id}] REFUSED: '{self.cam_id}' is not a usable "
                f"camera id.\n"
                f"    Use only letters, numbers, - and _ ."
            )

    def _on_message(self, ws, message):
        if isinstance(message, (bytes, bytearray)):
            if message and message[0] == BIN_AUDIO:
                self._on_talk(bytes(message[1:]))
            return
        try:
            m = json.loads(message)
        except Exception:
            return
        self._handle(m)

    # =====================================================================
    # commands from the dashboard
    # =====================================================================
    def _handle(self, m):
        t = m.get("t")

        if t == "stream":
            with self.stream_lock:
                self.stream_spec = ({
                    "width": int(m.get("width", 480)),
                    "fps": float(m.get("fps", 4)),
                    "quality": int(m.get("quality", 55)),
                } if m.get("on") else None)

        elif t == "audio":
            self.send_audio = bool(m.get("on"))

        elif t == "talk":
            if not m.get("on"):
                with self.talk_queue.mutex:
                    self.talk_queue.queue.clear()

        elif t == "record":
            on = bool(m.get("on"))
            self.rec.paused = not on
            if on:
                # Resuming also forces a clip for cameras that normally only
                # record on events, so the button always does something.
                self.rec.manual = not self.rec.always_record
                self.rec.mark_event()
                print(f"[{self.cam_id}] recording resumed")
            else:
                self.rec.manual = False
                print(f"[{self.cam_id}] recording paused")
            self.push_state()

        elif t == "baby_mode":
            self.baby_mode = bool(m.get("on"))
            self.push_state()

        elif t == "arm":
            self.armed = bool(m.get("on"))
            self.push_state()

        elif t == "noise_filter":
            self.denoise = bool(m.get("on"))
            self._suppressor = (NoiseSuppressor(rate=AUDIO_RATE)
                                if self.denoise else None)
            print(f"[{self.cam_id}] noise filter "
                  f"{'on' if self.denoise else 'off'}")
            self.push_state()

        elif t == "lens":
            self.switch_lens(m.get("id"))

        elif t == "zoom":
            try:
                self.zoom = max(1.0, min(4.0, float(m.get("value", 1))))
            except (TypeError, ValueError):
                pass

        elif t == "ptz":
            self._do_ptz(m)

        elif t == "config":
            self._apply_config(m.get("values") or {})

        elif t == "list_clips":
            self.send({"t": "clips", "items": self.rec.list_clips()})

        elif t == "get_clip":
            threading.Thread(target=self._send_clip, daemon=True,
                             args=(m.get("file"), m.get("xfer"))).start()

        elif t == "cancel_clip":
            self._cancelled_xfers.add(m.get("xfer"))

        elif t == "restart":
            print(f"[{self.cam_id}] restart requested")
            os._exit(3)      # a supervisor / run script brings us back

    def _do_ptz(self, m):
        action = m.get("action")
        speed = float(m.get("speed", 0.5) or 0.5)
        if action == "stop":
            self.ptz_autostop.cancel()
            self.ptz.stop()
        elif action == "home":
            self.ptz.goto(None)
        elif action == "preset":
            self.ptz.goto(m.get("preset"))
        elif action == "save_preset":
            if self.ptz.save(m.get("preset")):
                self.push_state()
        else:
            if self.ptz.move(action, speed):
                self.ptz_autostop.moved()

    def _apply_config(self, v):
        if "motion_min_area" in v:
            self.motion.min_area = int(v["motion_min_area"])
        if "motion_threshold" in v:
            self.motion.threshold = int(v["motion_threshold"])
        if "sound_sensitivity" in v:
            self.sound.sensitivity = max(0.2, float(v["sound_sensitivity"]))
        if "retention_hours" in v:
            self.rec.retention_hours = float(v["retention_hours"])
        if "max_disk_gb" in v:
            self.rec.max_disk_bytes = float(v["max_disk_gb"]) * 1024 ** 3
        if "ignore_regions" in v:
            self.motion.mask_spec = v["ignore_regions"]
            self.motion._mask = None
        print(f"[{self.cam_id}] config updated")
        self.push_state()

    # =====================================================================
    # capture
    # =====================================================================
    def open_capture(self, source):
        src = source
        if isinstance(src, str) and src.isdigit():
            src = int(src)
        cap = cv2.VideoCapture(src)
        if not cap.isOpened():
            return None
        # Small buffer keeps a network camera close to live instead of
        # replaying a backlog after a hiccup.
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
        except Exception:
            pass
        return cap

    def switch_lens(self, lens_id):
        if lens_id is None:
            return
        target = lens_id
        for l in self.lenses:
            if str(l.get("id")) == str(lens_id):
                target = l.get("source", l.get("id"))
                break
        print(f"[{self.cam_id}] switching to lens {lens_id}")
        new = self.open_capture(target)
        if new is None:
            print(f"[{self.cam_id}] could not open lens {lens_id}")
            return
        old = self.cap
        self.cap = new
        self.active_lens = str(lens_id)
        self.motion.reset()
        if old is not None:
            try:
                old.release()
            except Exception:
                pass
        self.push_state()

    def capture_loop(self):
        fail = 0
        period = 1.0 / max(1.0, self.target_fps)
        last_person_check = 0.0

        while not self._stop.is_set():
            if self.cap is None:
                self.status = "connecting"
                self.cap = self.open_capture(self.source)
                if self.cap is None:
                    self.status = "no camera"
                    self._stop.wait(3)
                    continue
                self.motion.reset()

            ok, frame = self.cap.read()
            if not ok or frame is None:
                fail += 1
                if fail > 10:
                    self.status = "reconnecting"
                    try:
                        self.cap.release()
                    except Exception:
                        pass
                    self.cap = None
                    fail = 0
                    self._stop.wait(2)
                continue

            fail = 0
            self.status = "live"
            now = time.time()

            if self.tamper is not None and self.tamper.check(frame):
                self.last_moved_at = now
                self.fire_event(
                    "moved",
                    "This camera has been moved or covered - it is no "
                    "longer watching what it was.",
                    frame)

            moved, changed, boxes = self.motion.detect(frame)
            if moved:
                self.last_motion_at = now

            if moved and not self.motion_on and self.armed:
                # For the cabin, only a person-shaped thing should wake you;
                # for a baby, any movement is worth knowing about.
                alert = True
                if self.person.enabled and not self.baby_mode:
                    if now - last_person_check > 1.0:
                        last_person_check = now
                        alert = self.person.contains_person(frame)
                    else:
                        alert = False
                if alert:
                    kind = "motion" if self.baby_mode else (
                        "person" if self.person.enabled else "motion")
                    detail = ("Your baby moved." if self.baby_mode
                              else "Movement in view of this camera.")
                    self.fire_event(kind, detail, frame)
            self.motion_on = moved

            display = self.annotate(frame, moved)
            self.rec.write(display)

            with self.frame_lock:
                self.frame = display
                self.frame_at = now

            time.sleep(period)

        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
        self.rec.stop()

    def annotate(self, frame, moved):
        out = frame.copy()
        h, w = out.shape[:2]
        when = datetime.datetime.now().strftime("%d %b %Y  %H:%M:%S")

        bar = max(22, int(h * 0.05))
        cv2.rectangle(out, (0, h - bar), (w, h), (0, 0, 0), -1)
        cv2.putText(out, f"{self.name}  |  {when}", (8, h - int(bar * 0.3)),
                    cv2.FONT_HERSHEY_SIMPLEX, max(0.4, h / 1100),
                    (255, 255, 255), 1, cv2.LINE_AA)

        if self.rec.recording:
            cv2.circle(out, (w - 20, 20), 7, (0, 0, 255), -1)
        if moved:
            cv2.putText(out, "MOTION", (10, 26), cv2.FONT_HERSHEY_SIMPLEX,
                        max(0.45, h / 1300), (0, 215, 255), 2, cv2.LINE_AA)
        return out

    # =====================================================================
    # sending frames while the dashboard watches
    # =====================================================================
    def stream_loop(self):
        while not self._stop.is_set():
            with self.stream_lock:
                spec = dict(self.stream_spec) if self.stream_spec else None
            if spec is None:
                self._stop.wait(0.25)
                continue

            with self.frame_lock:
                frame = None if self.frame is None else self.frame
            if frame is None:
                self._stop.wait(0.2)
                continue

            jpeg = self.encode(frame, spec)
            if jpeg is not None:
                self.send_binary(BIN_VIDEO, jpeg)
            self._stop.wait(1.0 / max(0.2, spec["fps"]))

    def encode(self, frame, spec):
        try:
            if self.zoom > 1.01:
                h, w = frame.shape[:2]
                cw, ch = int(w / self.zoom), int(h / self.zoom)
                x0, y0 = (w - cw) // 2, (h - ch) // 2
                frame = frame[y0:y0 + ch, x0:x0 + cw]

            h, w = frame.shape[:2]
            target = min(int(spec["width"]), w)
            if target < w:
                frame = cv2.resize(frame, (target, int(h * target / w)),
                                   interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(
                ".jpg", frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), int(spec["quality"])])
            return buf.tobytes() if ok else None
        except Exception:
            return None

    # =====================================================================
    # microphone and speaker
    # =====================================================================
    def _open_microphone(self):
        """Open the mic, at whatever rate it is willing to give us.

        Plenty of devices - USB headsets especially - refuse 16 kHz outright
        with "Invalid sample rate", so asking for it and giving up means no
        sound alerts at all on that machine. Try ours first, then the
        device's own preferred rate, then the usual suspects, and resample
        whatever we get.
        """
        rates = [AUDIO_RATE]
        try:
            info = sd.query_devices(self.audio_device, "input")
            default_sr = int(round(float(info.get("default_samplerate") or 0)))
            if default_sr and default_sr not in rates:
                rates.append(default_sr)
        except Exception:
            pass
        for sr in (48000, 44100, 32000, 22050, 11025, 8000):
            if sr not in rates:
                rates.append(sr)

        last = None
        for sr in rates:
            try:
                block = max(256, int(AUDIO_BLOCK * sr / AUDIO_RATE))
                stream = sd.InputStream(samplerate=sr, channels=1,
                                        dtype="float32", blocksize=block,
                                        device=self.audio_device)
                stream.start()
                if sr != AUDIO_RATE:
                    print(f"[{self.cam_id}] microphone open at {sr} Hz "
                          f"(it would not give us {AUDIO_RATE} Hz, so the "
                          f"sound is resampled)")
                else:
                    print(f"[{self.cam_id}] microphone open")
                return stream, sr, block
            except Exception as e:
                last = e
        raise last if last else RuntimeError("no microphone")

    def audio_loop(self):
        if not self.audio_enabled:
            return
        try:
            stream, rate, block = self._open_microphone()
            # Taking every third sample would alias everything above 8 kHz
            # down into the voice band - harsh, metallic speech, and energy
            # the cry detector never heard. This filters first.
            resampler = Resampler(rate, AUDIO_RATE)
            self._suppressor = (NoiseSuppressor(rate=AUDIO_RATE)
                                if self.denoise else None)
            if self._suppressor is not None:
                print(f"[{self.cam_id}] noise filter on - steady fan or "
                      f"cooler noise is removed before anything listens")
            with stream:
                while not self._stop.is_set():
                    raw, overflowed = stream.read(block)
                    mono = resampler.process(raw[:, 0])
                    if self._suppressor is not None and mono.size:
                        # Everything downstream - the alerts, the live
                        # listen and the saved clip - gets the cleaned
                        # sound, so they all agree on what was heard.
                        mono = self._suppressor.process(mono)

                    n = len(mono)
                    if n == 0:
                        continue

                    kind = self.sound.feed(mono)
                    if self.sound.loud:
                        self.last_sound_at = time.time()
                    if kind and self.armed:
                        if kind == "cry" and not self.baby_mode:
                            kind = "sound"
                        with self.frame_lock:
                            frame = self.frame
                        self.fire_event(kind, {
                            "impact": "A sudden thud - check right away.",
                            "cry": "Crying or fussing - sounds awake.",
                            "sound": "Continuous sound in the room.",
                        }.get(kind, ""), frame)

                    # One conversion, three uses: the clip being written,
                    # the dashboard when somebody is listening, and the
                    # detector above. The same open device serves all of
                    # them, which is what keeps Windows from refusing a
                    # second recorder.
                    pcm_bytes = (np.clip(mono, -1.0, 1.0) * 32767
                                 ).astype("<i2").tobytes()
                    self.rec.write_audio(pcm_bytes)
                    if self.send_audio:
                        self.send_binary(BIN_AUDIO, pcm_bytes)
        except Exception as e:
            print(f"[{self.cam_id}] microphone unavailable: {e}")
            print(f"[{self.cam_id}] Sound alerts and Listen are off for this "
                  f"camera; video and movement still work.")
            self.audio_enabled = False
            # Stop opening a sound file per clip that nothing will ever write.
            self.rec.want_audio = False

    def _on_talk(self, pcm_bytes):
        if not self.speaker_enabled:
            return
        try:
            self.talk_queue.put_nowait(pcm_bytes)
        except queue.Full:
            pass

    def speaker_loop(self):
        if not self.speaker_enabled:
            return
        try:
            with sd.OutputStream(samplerate=AUDIO_RATE, channels=1,
                                 dtype="float32",
                                 device=self.speaker_device) as out:
                while not self._stop.is_set():
                    try:
                        chunk = self.talk_queue.get(timeout=0.4)
                    except queue.Empty:
                        continue
                    pcm = np.frombuffer(chunk, dtype="<i2").astype("float32")
                    pcm /= 32768.0
                    out.write(pcm.reshape(-1, 1))
        except Exception as e:
            print(f"[{self.cam_id}] speaker unavailable: {e}")
            self.speaker_enabled = False

    # =====================================================================
    # events, clips, state
    # =====================================================================
    # Kinds that get their own, longer gap: these are rare and serious, and
    # should not be throttled by whatever else has been happening.
    SLOW_KINDS = {"moved": 120.0}

    def fire_event(self, kind, detail, frame=None):
        now = time.time()
        gap = self.SLOW_KINDS.get(kind, self.cfg.EVENT_MIN_GAP)
        if now - self._event_times.get(kind, 0) < gap:
            return
        self._event_times[kind] = now
        self.rec.mark_event()

        thumb = None
        if frame is not None:
            try:
                h, w = frame.shape[:2]
                tw = 360
                small = cv2.resize(frame, (tw, int(h * tw / w)))
                ok, buf = cv2.imencode(".jpg", small,
                                       [int(cv2.IMWRITE_JPEG_QUALITY), 62])
                if ok:
                    thumb = base64.b64encode(buf.tobytes()).decode()
            except Exception:
                thumb = None

        print(f"[{self.cam_id}] event: {kind} - {detail}")
        self.send({"t": "event", "kind": kind, "detail": detail,
                   "thumb": thumb, "clip": self.rec.current})

    def _send_clip(self, filename, xfer):
        path = self.rec.path_of(filename)
        if path is None:
            self.send({"t": "clip_error", "xfer": xfer, "detail": "not found"})
            return
        try:
            size = os.path.getsize(path)
            self.send({"t": "clip_start", "xfer": xfer, "size": size,
                       "type": "video/mp4"})
            with open(path, "rb") as f:
                while not self._stop.is_set():
                    if xfer in self._cancelled_xfers:
                        break
                    chunk = f.read(48 * 1024)
                    if not chunk:
                        break
                    if not self.send_binary(BIN_CLIP, chunk, xfer=xfer):
                        break
                    # Gentle pace so a clip download never starves the live
                    # view or fills the socket buffer.
                    time.sleep(0.004)
            self.send({"t": "clip_end", "xfer": xfer})
        except Exception as e:
            self.send({"t": "clip_error", "xfer": xfer, "detail": str(e)})
        finally:
            self._cancelled_xfers.discard(xfer)

    def push_state(self):
        now = time.time()
        self.send({
            "t": "state",
            "name": self.name,
            "status": self.status,
            "recording": self.rec.recording,
            "paused": self.rec.paused,
            "motion": self.motion_on,
            "sound": self.sound.loud,
            "audio_level": round(self.sound.level, 4),
            "baby_mode": self.baby_mode,
            "armed": self.armed,
            "disk_free_gb": self.rec.free_gb(),
            "clips": self.rec.count(),
            "retention_hours": self.rec.retention_hours,
            "quiet_for_s": (round(now - self.last_motion_at)
                            if self.last_motion_at else None),
            "silent_for_s": (round(now - self.last_sound_at)
                             if self.last_sound_at else None),
            "moved_ago_s": (round(now - self.last_moved_at)
                            if self.last_moved_at else None),
            "ptz": bool(getattr(self.ptz, "available", False)),
            "ptz_presets": list(getattr(self.ptz, "presets", []) or []),
            "lenses": [{"id": str(l.get("id")), "label": l.get("label", "")}
                       for l in self.lenses],
            "active_lens": self.active_lens,
            "zoom": self.zoom,
            "noise_filter": bool(self.denoise),
            "has_audio": bool(self.audio_enabled),
            "has_speaker": bool(self.speaker_enabled),
        })

    def state_loop(self):
        while not self._stop.is_set():
            if self.connected:
                self.push_state()
            self._stop.wait(2.0)

    # =====================================================================
    def start(self):
        for fn in (self.ws_loop, self.capture_loop, self.stream_loop,
                   self.state_loop, self.audio_loop, self.speaker_loop):
            t = threading.Thread(target=fn, daemon=True,
                                 name=f"{self.cam_id}-{fn.__name__}")
            t.start()
            self.threads.append(t)
        print(f"[{self.cam_id}] started ({self.name})")

    def join(self, timeout=6.0):
        """Wait for the threads to finish, so the camera is really free.

        Windows will not hand the same webcam to a second opener while the
        first still holds it, so restarting without waiting here leaves the
        new session unable to open anything.
        """
        deadline = time.time() + timeout
        for t in self.threads:
            left = deadline - time.time()
            if left <= 0:
                break
            try:
                t.join(timeout=left)
            except RuntimeError:
                pass

    def stop(self):
        self._stop.set()
        self.ptz_autostop.cancel()
        try:
            self.ptz.close()
        except Exception:
            pass
        with self.ws_lock:
            if self.ws is not None:
                try:
                    self.ws.close()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
def probe_cameras(limit=6):
    """List the camera indexes this machine can actually open."""
    found = []
    for i in range(limit):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            ok, frame = cap.read()
            if ok and frame is not None:
                h, w = frame.shape[:2]
                found.append((i, w, h))
            cap.release()
    return found


class Supervisor:
    """Owns the running cameras so the settings page can restart them.

    Changing which camera or microphone a session uses means opening a
    different device, so there is nothing to patch in place: the session is
    stopped and a fresh one started from the new settings.
    """

    def __init__(self, cfg, wanted):
        self.cfg = cfg
        self.wanted = dict(wanted)
        self.sessions = {}
        self._lock = threading.Lock()

    @property
    def editable(self):
        """Hand-written config.py wins over the file, so do not fight it."""
        return isinstance(self.cfg, setup_wizard.JsonConfig)

    def config_data(self):
        if isinstance(self.cfg, setup_wizard.JsonConfig):
            return dict(self.cfg.raw)
        # A module-based config cannot be written back, but the page should
        # still be able to show what is running.
        return {
            "server_url": getattr(self.cfg, "SERVER_URL", ""),
            "agent_token": getattr(self.cfg, "AGENT_TOKEN", ""),
            "retention_hours": getattr(self.cfg, "RETENTION_HOURS", 72),
            "max_disk_gb": getattr(self.cfg, "MAX_DISK_GB", 20.0),
            "segment_seconds": getattr(self.cfg, "SEGMENT_SECONDS", 300),
            "cameras": dict(self.wanted),
        }

    def start(self):
        with self._lock:
            for cam_id, conf in self.wanted.items():
                try:
                    s = CameraSession(cam_id, conf, self.cfg)
                    s.start()
                    self.sessions[cam_id] = s
                except Exception as e:
                    print(f"[{cam_id}] failed to start: {e}")
        return bool(self.sessions)

    def stop(self, wait=True):
        with self._lock:
            sessions = list(self.sessions.values())
            self.sessions.clear()
        for s in sessions:
            try:
                s.stop()
            except Exception:
                pass
        if wait:
            # Signalling is not enough: the capture thread may be sitting
            # inside a read, still holding the device.
            for s in sessions:
                try:
                    s.join(timeout=6.0)
                except Exception:
                    pass

    def apply(self, data):
        """Save new settings, then restart every camera from them.

        The file is written before returning, so the caller knows it is
        saved, but the restart runs on its own thread: releasing a camera and
        opening another takes seconds, and the page should not sit there
        looking frozen while it happens.
        """
        if not self.editable:
            raise RuntimeError(
                "These settings come from agent/config.py, so they have to be "
                "changed there. Delete config.py to manage them here instead.")

        setup_wizard.save_json_config(data)
        new_cfg = setup_wizard.JsonConfig(data)
        threading.Thread(target=self._restart, args=(new_cfg,),
                         daemon=True).start()

    def _restart(self, new_cfg):
        print("[panel] settings changed - restarting the cameras")
        self.stop()
        self.cfg = new_cfg
        self.wanted = dict(new_cfg.CAMERAS)
        try:
            os.makedirs(new_cfg.CLIPS_DIR, exist_ok=True)
        except Exception as e:
            print(f"[panel] could not use {new_cfg.CLIPS_DIR}: {e}")
        if self.start():
            print("[panel] cameras restarted")
        else:
            print("[panel] no camera could be started with the new settings")


def list_microphones():
    """[(index, name)] for every input device, or [] if audio is unavailable."""
    if sd is None:
        return []
    try:
        return [(i, d["name"]) for i, d in enumerate(sd.query_devices())
                if d.get("max_input_channels", 0) > 0]
    except Exception:
        return []


def main():
    ap = argparse.ArgumentParser(description="CabinWatch camera agent")
    ap.add_argument("--only", action="append", default=None,
                    help="run only this camera id from the config "
                         "(can be given more than once)")
    ap.add_argument("--list-cameras", action="store_true",
                    help="show which cameras this machine can open, then exit")
    ap.add_argument("--setup", action="store_true",
                    help="ask the setup questions again and overwrite "
                         "cabinwatch.json")
    ap.add_argument("--no-panel", action="store_true",
                    help="do not serve the local settings page")
    ap.add_argument("--panel-port", type=int, default=8765,
                    help="port for the local settings page (default 8765)")
    ap.add_argument("--open", action="store_true",
                    help="open the settings page in a browser on start")
    args = ap.parse_args()

    if args.list_cameras:
        print("Cameras this machine can open:")
        found = probe_cameras()
        if not found:
            print("  none - is another program already using the camera?")
        for i, w, h in found:
            print(f'  source: {i}    {w}x{h}')
        mics = list_microphones()
        if mics:
            print("\nMicrophones:")
            for i, label in mics:
                print(f"  audio_device: {i}    {label}")
        elif sd is None:
            print("\nNo microphone support on this machine - see the [audio] "
                  "line above for why.")
        else:
            print("\nNo microphones found.")
        return

    cfg = load_config(force_setup=args.setup)
    if cfg is None:
        sys.exit("Setup was not completed, so there is nothing to run.")

    where = ("cabinwatch.json" if isinstance(cfg, setup_wizard.JsonConfig)
             else "agent/config.py")
    if not getattr(cfg, "SERVER_URL", "") or "YOUR-APP" in cfg.SERVER_URL:
        sys.exit(f"Set SERVER_URL in {where} to your server address, for "
                 f"example https://cabinwatch-production.up.railway.app\n"
                 f"Or run with --setup to be asked again.")
    if not getattr(cfg, "AGENT_TOKEN", ""):
        sys.exit(f"Set AGENT_TOKEN in {where} to the same value as the "
                 f"AGENT_TOKEN variable on the server.\n"
                 f"Or run with --setup to be asked again.")

    os.makedirs(cfg.CLIPS_DIR, exist_ok=True)

    wanted = cfg.CAMERAS
    if args.only:
        wanted = {k: v for k, v in cfg.CAMERAS.items() if k in args.only}
        missing = set(args.only) - set(wanted)
        if missing:
            sys.exit(f"No such camera in the config: {', '.join(missing)}")
    if not wanted:
        sys.exit("No cameras configured. Edit CAMERAS in agent/config.py.")

    print("=" * 62)
    print("  CabinWatch agent")
    print(f"  server   : {cfg.SERVER_URL}")
    print(f"  clips in : {os.path.abspath(cfg.CLIPS_DIR)}")
    print(f"  cameras  : {', '.join(wanted)}")
    print("=" * 62)

    supervisor = Supervisor(cfg, wanted)
    if not supervisor.start():
        sys.exit("No camera could be started.")

    panel = None
    if not args.no_panel:
        port = local_ui.free_port(args.panel_port)
        panel = local_ui.ControlPanel(supervisor, port=port)
        if panel.start(open_browser=args.open):
            print(f"\n  To change the camera or microphone, open "
                  f"{panel.url} on this machine.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nstopping...")
        if panel is not None:
            panel.stop()
        supervisor.stop()
        time.sleep(0.6)


if __name__ == "__main__":
    main()
