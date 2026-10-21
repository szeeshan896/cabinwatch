#!/usr/bin/env python3
"""
CabinWatch - multi-camera CCTV for your own cabin/office.

Sources supported:
  - Laptop webcam            -> source: 0  (or 1, 2 for extra USB cams)
  - Android phone (IP Webcam)-> source: "http://192.168.x.x:8080/video"
  - Any RTSP/MJPEG camera    -> source: "rtsp://..."

Features:
  - Live view from your phone (open the dashboard URL in phone browser)
  - Motion detection per camera
  - Auto-record on motion  (toggle per camera)
  - Manual record ON/OFF   (toggle per camera)
  - Timestamped clips saved to ./clips
  - Optional alarm cast to Google Home / Chromecast on motion
"""

import os
import time
import threading
import datetime
from collections import deque

import cv2
from flask import Flask, Response, render_template, jsonify, send_from_directory, abort

# ----------------------------------------------------------------------------
# Settings are loaded from config.py (your private file), falling back to
# config_example.py so a fresh clone still runs with the laptop webcam.
# Your real phone IPs / device names live in config.py, which is gitignored
# and never gets committed or pushed.
# ----------------------------------------------------------------------------
try:
    import config as cfg
except ModuleNotFoundError:
    import config_example as cfg
    print("[config] No config.py found - using example defaults "
          "(laptop webcam only). Copy config_example.py to config.py to "
          "add your own cameras.")

CAMERAS = cfg.CAMERAS
CLIPS_DIR = cfg.CLIPS_DIR
MOTION_MIN_AREA = cfg.MOTION_MIN_AREA
MOTION_THRESHOLD = cfg.MOTION_THRESHOLD
MOTION_COOLDOWN = cfg.MOTION_COOLDOWN
PRE_MOTION_SECONDS = cfg.PRE_MOTION_SECONDS
RECORD_FPS = cfg.RECORD_FPS
MAX_CLIP_SECONDS = cfg.MAX_CLIP_SECONDS
HOST = cfg.HOST
PORT = cfg.PORT
ALARM_ENABLED = cfg.ALARM_ENABLED
ALARM_DEVICE_NAME = cfg.ALARM_DEVICE_NAME
ALARM_SOUND_URL = cfg.ALARM_SOUND_URL
ALARM_MIN_GAP = cfg.ALARM_MIN_GAP

# ----------------------------------------------------------------------------

os.makedirs(CLIPS_DIR, exist_ok=True)
app = Flask(__name__)


# ----------------------------------------------------------------------------
# Optional Google Home / Chromecast alarm
# ----------------------------------------------------------------------------
class Alarm:
    """Casts a sound to a Google Home / Chromecast device. Fully optional."""

    def __init__(self):
        self.last_fired = 0.0
        self.device = None
        self.available = False
        if not ALARM_ENABLED:
            return
        try:
            import pychromecast  # noqa: F401
            self.available = True
        except ImportError:
            print("[alarm] pychromecast not installed - alarm disabled. "
                  "Install with: pip install PyChromecast")

    def _connect(self):
        import pychromecast
        casts, browser = pychromecast.get_listed_chromecasts(
            friendly_names=[ALARM_DEVICE_NAME]
        )
        if not casts:
            print(f"[alarm] device '{ALARM_DEVICE_NAME}' not found on network")
            return None
        cast = casts[0]
        cast.wait()
        return cast

    def fire(self, camera_name):
        if not self.available:
            return
        now = time.time()
        if now - self.last_fired < ALARM_MIN_GAP:
            return
        self.last_fired = now
        threading.Thread(target=self._fire_worker, args=(camera_name,),
                         daemon=True).start()

    def _fire_worker(self, camera_name):
        try:
            if self.device is None:
                self.device = self._connect()
            if self.device is None:
                return
            self.device.media_controller.play_media(ALARM_SOUND_URL, "audio/ogg")
            self.device.media_controller.block_until_active(timeout=10)
            print(f"[alarm] fired for {camera_name}")
        except Exception as e:
            print(f"[alarm] failed: {e}")
            self.device = None


alarm = Alarm()


# ----------------------------------------------------------------------------
# Camera worker
# ----------------------------------------------------------------------------
class CameraWorker(threading.Thread):
    def __init__(self, cam_id, name, source):
        super().__init__(daemon=True)
        self.cam_id = cam_id
        self.name = name
        self.source = source

        self.lock = threading.Lock()
        self.jpeg = None            # latest encoded frame for streaming

        self.status = "starting"    # starting | live | reconnecting | error
        self.motion = False
        self.last_motion_at = 0.0

        # user-controlled switches
        self.auto_record = True     # record automatically when motion is seen
        self.manual_record = False  # force recording ON regardless of motion

        self.recording = False
        self.current_clip = None
        self._writer = None
        self._record_started_at = 0.0

        # rolling buffer so a clip includes the moments BEFORE motion started
        self._prebuffer = deque(maxlen=int(PRE_MOTION_SECONDS * RECORD_FPS))

        self._prev_gray = None
        self._running = True
        self._frame_size = None

    # -- helpers -------------------------------------------------------------
    def _open(self):
        cap = cv2.VideoCapture(self.source)
        if not cap.isOpened():
            return None
        return cap

    def _detect_motion(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        if self._prev_gray is None:
            self._prev_gray = gray
            return False

        delta = cv2.absdiff(self._prev_gray, gray)
        self._prev_gray = gray

        thresh = cv2.threshold(delta, MOTION_THRESHOLD, 255, cv2.THRESH_BINARY)[1]
        thresh = cv2.dilate(thresh, None, iterations=2)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        return any(cv2.contourArea(c) >= MOTION_MIN_AREA for c in contours)

    def _start_recording(self, frame):
        h, w = frame.shape[:2]
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"{self.cam_id}_{ts}.mp4"
        path = os.path.join(CLIPS_DIR, filename)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self._writer = cv2.VideoWriter(path, fourcc, RECORD_FPS, (w, h))
        if not self._writer.isOpened():
            print(f"[{self.cam_id}] could not open writer for {path}")
            self._writer = None
            return
        self.recording = True
        self.current_clip = filename
        self._record_started_at = time.time()
        print(f"[{self.cam_id}] recording -> {filename}")
        # flush the pre-motion buffer into the clip
        for f in list(self._prebuffer):
            self._writer.write(f)

    def _stop_recording(self):
        if self._writer is not None:
            self._writer.release()
            print(f"[{self.cam_id}] saved {self.current_clip}")
        self._writer = None
        self.recording = False
        self.current_clip = None

    def _annotate(self, frame, motion):
        now = datetime.datetime.now().strftime("%d %b %Y  %H:%M:%S")
        h, w = frame.shape[:2]

        # dark strip at the bottom for the timestamp
        cv2.rectangle(frame, (0, h - 28), (w, h), (0, 0, 0), -1)
        cv2.putText(frame, f"{self.name}  |  {now}", (8, h - 9),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1,
                    cv2.LINE_AA)

        if self.recording:
            cv2.circle(frame, (w - 22, 22), 8, (0, 0, 255), -1)
            cv2.putText(frame, "REC", (w - 70, 28), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, (0, 0, 255), 2, cv2.LINE_AA)
        if motion:
            cv2.putText(frame, "MOTION", (10, 28), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, (0, 215, 255), 2, cv2.LINE_AA)
        return frame

    # -- main loop -----------------------------------------------------------
    def run(self):
        cap = None
        fail_count = 0

        while self._running:
            if cap is None:
                cap = self._open()
                if cap is None:
                    self.status = "reconnecting"
                    time.sleep(3)
                    continue
                self.status = "live"
                self._prev_gray = None

            ok, frame = cap.read()
            if not ok or frame is None:
                fail_count += 1
                if fail_count > 10:
                    self.status = "reconnecting"
                    cap.release()
                    cap = None
                    fail_count = 0
                    time.sleep(2)
                continue

            fail_count = 0
            self.status = "live"
            self._frame_size = frame.shape[:2]

            motion = self._detect_motion(frame)
            now = time.time()
            if motion:
                if not self.motion and self.auto_record:
                    alarm.fire(self.name)
                self.last_motion_at = now
            self.motion = motion

            # decide whether we should be recording right now
            within_cooldown = (now - self.last_motion_at) < MOTION_COOLDOWN
            should_record = self.manual_record or (self.auto_record and within_cooldown)

            # safety cap on clip length
            if self.recording and (now - self._record_started_at) > MAX_CLIP_SECONDS:
                self._stop_recording()

            display = self._annotate(frame.copy(), motion)

            if should_record and not self.recording:
                self._start_recording(display)
            elif not should_record and self.recording:
                self._stop_recording()

            if self.recording and self._writer is not None:
                self._writer.write(display)
            else:
                self._prebuffer.append(display.copy())

            ok, buf = cv2.imencode(".jpg", display,
                                   [int(cv2.IMWRITE_JPEG_QUALITY), 75])
            if ok:
                with self.lock:
                    self.jpeg = buf.tobytes()

            time.sleep(1.0 / (RECORD_FPS * 1.5))

        if cap is not None:
            cap.release()
        self._stop_recording()

    def get_jpeg(self):
        with self.lock:
            return self.jpeg

    def snapshot_state(self):
        return {
            "id": self.cam_id,
            "name": self.name,
            "status": self.status,
            "motion": self.motion,
            "recording": self.recording,
            "auto_record": self.auto_record,
            "manual_record": self.manual_record,
            "clip": self.current_clip,
        }


workers = {}


def start_workers():
    for cam_id, cfg in CAMERAS.items():
        w = CameraWorker(cam_id, cfg["name"], cfg["source"])
        w.start()
        workers[cam_id] = w
        print(f"[init] started {cam_id} ({cfg['name']})")


# ----------------------------------------------------------------------------
# Web routes
# ----------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html",
                           cameras=[w.snapshot_state() for w in workers.values()])


def _mjpeg(worker):
    boundary = b"--frame\r\n"
    while True:
        jpeg = worker.get_jpeg()
        if jpeg is not None:
            yield boundary + b"Content-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
        time.sleep(1.0 / RECORD_FPS)


@app.route("/feed/<cam_id>")
def feed(cam_id):
    w = workers.get(cam_id)
    if w is None:
        abort(404)
    return Response(_mjpeg(w),
                    mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/status")
def status():
    return jsonify({
        "cameras": [w.snapshot_state() for w in workers.values()],
        "clips": _clip_list()[:30],
        "server_time": datetime.datetime.now().strftime("%d %b %Y  %H:%M:%S"),
    })


@app.route("/toggle/<cam_id>/<what>", methods=["POST"])
def toggle(cam_id, what):
    w = workers.get(cam_id)
    if w is None:
        abort(404)
    if what == "manual":
        w.manual_record = not w.manual_record
    elif what == "auto":
        w.auto_record = not w.auto_record
    else:
        abort(400)
    return jsonify(w.snapshot_state())


def _clip_list():
    try:
        files = [f for f in os.listdir(CLIPS_DIR) if f.endswith(".mp4")]
    except FileNotFoundError:
        return []
    files.sort(reverse=True)
    out = []
    for f in files:
        p = os.path.join(CLIPS_DIR, f)
        try:
            size = os.path.getsize(p)
            mtime = os.path.getmtime(p)
        except OSError:
            continue
        out.append({
            "file": f,
            "size_mb": round(size / (1024 * 1024), 1),
            "when": datetime.datetime.fromtimestamp(mtime).strftime("%d %b, %H:%M:%S"),
        })
    return out


@app.route("/clips/<path:filename>")
def clip(filename):
    return send_from_directory(CLIPS_DIR, filename, as_attachment=False)


def _local_ip():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    start_workers()
    ip = _local_ip()
    print("\n" + "=" * 54)
    print("  CabinWatch is running")
    print(f"  On this laptop : http://localhost:{PORT}")
    print(f"  On your phone  : http://{ip}:{PORT}   (same WiFi)")
    print("=" * 54 + "\n")
    app.run(host=HOST, port=PORT, threaded=True, debug=False)
