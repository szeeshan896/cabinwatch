"""
Pan / tilt / zoom control for rotating cameras.

Rotating cameras do not share one protocol, so there are three backends and
you pick the one your camera speaks in config.py:

  "onvif"  Almost every IP camera sold as "PTZ" or "360" - Tapo, Imou,
           CP Plus, Hikvision, Dahua. This is the one to aim for when buying:
           look for "ONVIF" on the box or spec sheet.

  "uvc"    A USB webcam whose motor is exposed as a standard camera control.
           Works on Logitech PTZ-style cameras. On Linux it goes through
           v4l2-ctl, on Windows through the OpenCV property.

  "http"   Anything with its own little web API. You paste URL templates and
           we call them. Use this for cheap no-name cameras.

  "none"   Fixed camera, no motor.

All backends expose the same four calls, so the dashboard does not care which
one is in use.
"""

import shutil
import threading
import subprocess


class PTZBase:
    """A camera with no motor. Also the interface the others implement."""

    available = False
    presets = []

    def move(self, action, speed=0.5):
        return False

    def stop(self):
        return False

    def goto(self, preset):
        return False

    def save(self, preset):
        return False

    def close(self):
        pass


class OnvifPTZ(PTZBase):
    """Standard ONVIF PTZ. This is what most rotating IP cameras speak."""

    def __init__(self, host, port=80, user="", password=""):
        self.available = False
        self.presets = []
        self._lock = threading.Lock()
        self._cam = None
        self._ptz = None
        self._token = None
        self._preset_tokens = {}

        try:
            from onvif import ONVIFCamera
        except ImportError:
            print("[ptz] onvif backend needs: pip install onvif-zeep")
            return

        try:
            self._cam = ONVIFCamera(host, int(port), user, password)
            media = self._cam.create_media_service()
            self._ptz = self._cam.create_ptz_service()
            profile = media.GetProfiles()[0]
            self._token = profile.token
            self.available = True
            self._load_presets()
            print(f"[ptz] onvif connected to {host}:{port}")
        except Exception as e:
            print(f"[ptz] onvif connect failed for {host}:{port}: {e}")

    def _load_presets(self):
        try:
            for p in self._ptz.GetPresets({"ProfileToken": self._token}) or []:
                label = getattr(p, "Name", None) or p.token
                self._preset_tokens[str(label)] = p.token
            self.presets = sorted(self._preset_tokens)
        except Exception:
            self.presets = []

    def move(self, action, speed=0.5):
        if not self.available:
            return False
        speed = max(0.05, min(1.0, float(speed)))
        pan = tilt = zoom = 0.0
        if action == "left":
            pan = -speed
        elif action == "right":
            pan = speed
        elif action == "up":
            tilt = speed
        elif action == "down":
            tilt = -speed
        elif action == "zoom_in":
            zoom = speed
        elif action == "zoom_out":
            zoom = -speed
        else:
            return False
        try:
            with self._lock:
                self._ptz.ContinuousMove({
                    "ProfileToken": self._token,
                    "Velocity": {"PanTilt": {"x": pan, "y": tilt},
                                 "Zoom": {"x": zoom}},
                })
            return True
        except Exception as e:
            print(f"[ptz] move failed: {e}")
            return False

    def stop(self):
        if not self.available:
            return False
        try:
            with self._lock:
                self._ptz.Stop({"ProfileToken": self._token,
                                "PanTilt": True, "Zoom": True})
            return True
        except Exception:
            return False

    def goto(self, preset):
        if not self.available:
            return False
        token = self._preset_tokens.get(str(preset))
        try:
            with self._lock:
                if token:
                    self._ptz.GotoPreset({"ProfileToken": self._token,
                                          "PresetToken": token})
                else:
                    self._ptz.GotoHomePosition({"ProfileToken": self._token})
            return True
        except Exception as e:
            print(f"[ptz] goto failed: {e}")
            return False

    def save(self, preset):
        if not self.available:
            return False
        try:
            with self._lock:
                r = self._ptz.SetPreset({"ProfileToken": self._token,
                                         "PresetName": str(preset)})
            self._preset_tokens[str(preset)] = getattr(r, "PresetToken", r)
            self.presets = sorted(self._preset_tokens)
            return True
        except Exception as e:
            print(f"[ptz] save preset failed: {e}")
            return False


class UvcPTZ(PTZBase):
    """A USB webcam whose pan/tilt motor shows up as a camera control."""

    # OpenCV property ids, used on Windows where v4l2-ctl does not exist.
    CV_PAN, CV_TILT, CV_ZOOM = 33, 34, 27

    def __init__(self, device="/dev/video0", cap=None, step=3600,
                 pan_range=(-468000, 468000), tilt_range=(-324000, 324000)):
        self.device = device
        self.cap = cap
        self.step = int(step)
        self.pan_range = pan_range
        self.tilt_range = tilt_range
        self.pan = 0
        self.tilt = 0
        self.presets = []
        self._saved = {}
        self._v4l2 = shutil.which("v4l2-ctl")
        self.available = bool(self._v4l2) or cap is not None
        if not self.available:
            print("[ptz] uvc backend needs v4l2-ctl (Linux) or an open "
                  "OpenCV capture (Windows)")

    def _apply(self):
        if self._v4l2:
            try:
                subprocess.run(
                    [self._v4l2, "-d", self.device,
                     "--set-ctrl", f"pan_absolute={self.pan}",
                     "--set-ctrl", f"tilt_absolute={self.tilt}"],
                    check=False, capture_output=True, timeout=5)
                return True
            except Exception as e:
                print(f"[ptz] v4l2-ctl failed: {e}")
                return False
        if self.cap is not None:
            try:
                self.cap.set(self.CV_PAN, self.pan)
                self.cap.set(self.CV_TILT, self.tilt)
                return True
            except Exception:
                return False
        return False

    def move(self, action, speed=0.5):
        if not self.available:
            return False
        step = int(self.step * max(0.1, min(1.0, float(speed))) * 2)
        if action == "left":
            self.pan = max(self.pan_range[0], self.pan - step)
        elif action == "right":
            self.pan = min(self.pan_range[1], self.pan + step)
        elif action == "up":
            self.tilt = min(self.tilt_range[1], self.tilt + step)
        elif action == "down":
            self.tilt = max(self.tilt_range[0], self.tilt - step)
        else:
            return False
        return self._apply()

    def stop(self):
        # Absolute positioning: it already stopped where we put it.
        return True

    def goto(self, preset):
        if str(preset) in self._saved:
            self.pan, self.tilt = self._saved[str(preset)]
        else:
            self.pan = self.tilt = 0
        return self._apply()

    def save(self, preset):
        self._saved[str(preset)] = (self.pan, self.tilt)
        self.presets = sorted(self._saved)
        return True


class HttpPTZ(PTZBase):
    """A camera with its own HTTP commands. You supply the URL templates."""

    def __init__(self, urls, auth=None, verify=True):
        self.urls = urls or {}
        self.auth = tuple(auth) if auth else None
        self.verify = verify
        self.presets = sorted(
            k.split("preset:", 1)[1] for k in self.urls if k.startswith("preset:")
        )
        self.available = bool(self.urls)
        self._session = None

    def _get(self, url):
        try:
            import requests
        except ImportError:
            print("[ptz] http backend needs: pip install requests")
            return False
        if self._session is None:
            self._session = requests.Session()
            if self.auth:
                self._session.auth = self.auth
        try:
            r = self._session.get(url, timeout=5, verify=self.verify)
            return r.status_code < 400
        except Exception as e:
            print(f"[ptz] http call failed: {e}")
            return False

    def move(self, action, speed=0.5):
        url = self.urls.get(action)
        if not url:
            return False
        return self._get(url.replace("{speed}", str(int(speed * 100))))

    def stop(self):
        url = self.urls.get("stop")
        return self._get(url) if url else False

    def goto(self, preset):
        url = self.urls.get(f"preset:{preset}") or self.urls.get("home")
        return self._get(url) if url else False

    def save(self, preset):
        # Most of these cameras save presets only in their own app.
        return False


def build(conf, cap=None):
    """Make the PTZ controller described by a camera's config block."""
    kind = (conf or {}).get("type", "none").lower()

    if kind == "onvif":
        return OnvifPTZ(conf.get("host", ""), conf.get("port", 80),
                        conf.get("user", ""), conf.get("password", ""))
    if kind == "uvc":
        return UvcPTZ(conf.get("device", "/dev/video0"), cap=cap,
                      step=conf.get("step", 3600))
    if kind == "http":
        return HttpPTZ(conf.get("urls", {}), conf.get("auth"),
                       conf.get("verify", True))
    return PTZBase()


class AutoStop:
    """Stops a continuous move if the dashboard never sends a stop.

    Pressing an arrow starts the motor and lifting the finger stops it, but a
    dropped network packet would otherwise leave the camera spinning.
    """

    def __init__(self, ptz, after=2.0):
        self.ptz = ptz
        self.after = after
        self._timer = None
        self._lock = threading.Lock()

    def moved(self):
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.after, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def cancel(self):
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None

    def _fire(self):
        try:
            self.ptz.stop()
        except Exception:
            pass
