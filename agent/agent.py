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
from recorder import Recorder
from detect import MotionDetector, PersonDetector, SoundDetector

try:
    import websocket          # pip install websocket-client
except ImportError:
    sys.exit("Missing dependency. Run:  pip install -r agent/requirements.txt")

try:
    import sounddevice as sd
except ImportError:
    sd = None

# Mirrors server/protocol.py.
BIN_VIDEO, BIN_AUDIO, BIN_CLIP = 0x01, 0x02, 0x03
AUDIO_RATE = 16000
AUDIO_BLOCK = 1024


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def load_config():
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    try:
        import config as cfg
        return cfg
    except ModuleNotFoundError:
        import config_example as cfg
        print("[config] No agent/config.py found - using the example values.")
        print("[config] Copy agent/config_example.py to agent/config.py and "
              "put your server URL and token in it.")
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
        self._event_times = {}

        # -- recording
        self.rec = Recorder(
            cam_id,
            clips_dir=cfg.CLIPS_DIR,
            fps=self.target_fps,
            segment_seconds=cfg.SEGMENT_SECONDS,
            retention_hours=conf.get("retention_hours", cfg.RETENTION_HOURS),
            event_retention_hours=conf.get("event_retention_hours",
                                           cfg.EVENT_RETENTION_HOURS),
            max_disk_gb=conf.get("max_disk_gb", cfg.MAX_DISK_GB),
            always_record=conf.get("always_record", cfg.ALWAYS_RECORD),
            prebuffer_seconds=cfg.PREBUFFER_SECONDS,
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
        # A wrong token or a bad camera id is refused during the handshake,
        # so it arrives here as an HTTP status rather than a close code. Say
        # so plainly - otherwise all the operator sees is "reconnecting".
        status = getattr(error, "status_code", None)
        if status == 403:
            self._explain(4401)
        elif status in (400, 404):
            self._explain(4400)
        elif status is not None:
            print(f"[{self.cam_id}] server refused the connection "
                  f"(HTTP {status}). Is SERVER_URL correct?")
        elif isinstance(error, (socket.gaierror, ConnectionRefusedError)):
            print(f"[{self.cam_id}] cannot reach {self.server} - "
                  f"check the address and this machine's internet.")

    _explained = set()

    def _explain(self, code):
        # Print each distinct reason once; the loop keeps retrying quietly.
        if code in self._explained:
            return
        self._explained.add(code)
        if code == 4401:
            print(f"[{self.cam_id}] REFUSED: the server did not accept the "
                  f"token.\n"
                  f"    AGENT_TOKEN in agent/config.py must be character for "
                  f"character the same as\n"
                  f"    the AGENT_TOKEN variable on your Railway service.")
        elif code == 4400:
            print(f"[{self.cam_id}] REFUSED: '{self.cam_id}' is not a usable "
                  f"camera id.\n"
                  f"    Use only letters, numbers, - and _ for the keys in "
                  f"CAMERAS.")

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
            self.rec.manual = bool(m.get("on"))
            if self.rec.manual:
                self.rec.mark_event()
            self.push_state()

        elif t == "baby_mode":
            self.baby_mode = bool(m.get("on"))
            self.push_state()

        elif t == "arm":
            self.armed = bool(m.get("on"))
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
    def audio_loop(self):
        if not self.audio_enabled:
            return
        try:
            with sd.InputStream(samplerate=AUDIO_RATE, channels=1,
                                dtype="float32", blocksize=AUDIO_BLOCK,
                                device=self.audio_device) as stream:
                print(f"[{self.cam_id}] microphone open")
                while not self._stop.is_set():
                    block, overflowed = stream.read(AUDIO_BLOCK)
                    mono = block[:, 0]

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

                    if self.send_audio:
                        pcm = np.clip(mono, -1.0, 1.0)
                        self.send_binary(
                            BIN_AUDIO,
                            (pcm * 32767).astype("<i2").tobytes())
        except Exception as e:
            print(f"[{self.cam_id}] microphone unavailable: {e}")
            self.audio_enabled = False

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
    def fire_event(self, kind, detail, frame=None):
        now = time.time()
        if now - self._event_times.get(kind, 0) < self.cfg.EVENT_MIN_GAP:
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
            "ptz": bool(getattr(self.ptz, "available", False)),
            "ptz_presets": list(getattr(self.ptz, "presets", []) or []),
            "lenses": [{"id": str(l.get("id")), "label": l.get("label", "")}
                       for l in self.lenses],
            "active_lens": self.active_lens,
            "zoom": self.zoom,
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


def main():
    ap = argparse.ArgumentParser(description="CabinWatch camera agent")
    ap.add_argument("--only", action="append", default=None,
                    help="run only this camera id from the config "
                         "(can be given more than once)")
    ap.add_argument("--list-cameras", action="store_true",
                    help="show which cameras this machine can open, then exit")
    args = ap.parse_args()

    if args.list_cameras:
        print("Cameras this machine can open:")
        found = probe_cameras()
        if not found:
            print("  none - is another program already using the camera?")
        for i, w, h in found:
            print(f'  source: {i}    {w}x{h}')
        if sd is not None:
            print("\nMicrophones:")
            for i, d in enumerate(sd.query_devices()):
                if d.get("max_input_channels", 0) > 0:
                    print(f'  audio_device: {i}    {d["name"]}')
        return

    cfg = load_config()

    if not getattr(cfg, "SERVER_URL", "") or "YOUR-APP" in cfg.SERVER_URL:
        sys.exit("Set SERVER_URL in agent/config.py to your Railway URL, "
                 "for example https://cabinwatch-production.up.railway.app")
    if not getattr(cfg, "AGENT_TOKEN", ""):
        sys.exit("Set AGENT_TOKEN in agent/config.py to the same value as the "
                 "AGENT_TOKEN variable on the server.")

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

    sessions = []
    for cam_id, conf in wanted.items():
        try:
            s = CameraSession(cam_id, conf, cfg)
            s.start()
            sessions.append(s)
        except Exception as e:
            print(f"[{cam_id}] failed to start: {e}")

    if not sessions:
        sys.exit("No camera could be started.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nstopping...")
        for s in sessions:
            s.stop()
        time.sleep(0.6)


if __name__ == "__main__":
    main()
