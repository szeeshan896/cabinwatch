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

import re
import sys
import time
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
                 pan_range=None, tilt_range=None,
                 invert_pan=False, invert_tilt=False):
        self.device = device
        # Which way a positive number turns the camera is not agreed between
        # makers - on an OBSBOT a bigger pan number turns it to *your* left -
        # so the direction is a setting rather than an assumption. Get it
        # wrong and every arrow on the dashboard does the opposite.
        self.sign_pan = -1 if invert_pan else 1
        self.sign_tilt = -1 if invert_tilt else 1
        self.cap = cap
        self.step = int(step)
        self.pan = 0
        self.tilt = 0
        self.presets = []
        self._saved = {}
        self._v4l2 = shutil.which("v4l2-ctl")
        self.available = bool(self._v4l2) or cap is not None
        if not self.available:
            print("[ptz] uvc backend needs v4l2-ctl (Linux) or an open "
                  "OpenCV capture (Windows)")

        # How far the motor can actually turn differs between cameras, so ask
        # the camera rather than assuming. The numbers below are only used
        # when it cannot be asked - they are a typical gimbal webcam's range.
        found = uvc_controls(device) if self._v4l2 else {}
        self.pan_range = pan_range or self._range(found, "pan",
                                                  (-468000, 468000))
        self.tilt_range = tilt_range or self._range(found, "tilt",
                                                    (-324000, 324000))
        if found.get("pan") and found["pan"].get("step"):
            self.step = int(step or found["pan"]["step"])

    @staticmethod
    def _range(found, axis, fallback):
        ctrl = found.get(axis)
        if ctrl and ctrl.get("min") is not None and ctrl.get("max") is not None:
            return (int(ctrl["min"]), int(ctrl["max"]))
        return fallback

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

    @staticmethod
    def _clamp(value, limits):
        low, high = min(limits), max(limits)
        return max(low, min(high, int(value)))

    def move(self, action, speed=0.5):
        if not self.available:
            return False
        step = int(self.step * max(0.1, min(1.0, float(speed))) * 2)
        if action in ("left", "right"):
            delta = step if action == "right" else -step
            self.pan = self._clamp(self.pan + delta * self.sign_pan,
                                   self.pan_range)
        elif action in ("up", "down"):
            delta = step if action == "up" else -step
            self.tilt = self._clamp(self.tilt + delta * self.sign_tilt,
                                    self.tilt_range)
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


# ---------------------------------------------------------------------------
# Finding out what a camera can actually do
#
# A gimbal webcam is a lottery. Some expose the motor as a standard camera
# control, which is what the "uvc" backend drives. Others only move from the
# maker's own app, over a private channel we cannot speak. The box never
# says which, so ask the camera.
# ---------------------------------------------------------------------------
CONTROL_NAMES = {
    "pan": ("pan_absolute", "pan_speed", "pan_relative"),
    "tilt": ("tilt_absolute", "tilt_speed", "tilt_relative"),
    "zoom": ("zoom_absolute", "zoom_continuous", "zoom_relative"),
}

_CTRL_LINE = re.compile(
    r"^\s*(?P<name>[a-z0-9_]+)\s+0x[0-9a-f]+\s+\((?P<type>\w+)\)"
    r"\s*:\s*(?P<rest>.*)$")


def uvc_controls(device="/dev/video0"):
    """Which pan/tilt/zoom controls a camera exposes, by axis (Linux only)."""
    v4l2 = shutil.which("v4l2-ctl")
    if not v4l2:
        return {}
    try:
        out = subprocess.run([v4l2, "-d", str(device), "--list-ctrls"],
                             capture_output=True, text=True, timeout=8).stdout
    except Exception:
        return {}

    found = {}
    for line in out.splitlines():
        m = _CTRL_LINE.match(line)
        if not m:
            continue
        name = m.group("name")
        for axis, names in CONTROL_NAMES.items():
            if name not in names:
                continue
            fields = dict(re.findall(r"(\w+)=(-?\d+)", m.group("rest")))
            found[axis] = {
                "control": name,
                "min": int(fields["min"]) if "min" in fields else None,
                "max": int(fields["max"]) if "max" in fields else None,
                "step": int(fields["step"]) if "step" in fields else None,
                "value": int(fields["value"]) if "value" in fields else None,
            }
    return found


def _probe_opencv(source):
    """Try the motor through OpenCV, which is the only route on Windows.

    OpenCV answering True does not prove anything moved - it often reports
    success for a control the camera ignores - so this reports what it saw
    and leaves the verdict to whoever is watching the camera.
    """
    try:
        import cv2
    except Exception as e:
        print(f"  OpenCV is not available here: {e}")
        return {}

    cap = cv2.VideoCapture(int(source) if str(source).isdigit() else source)
    if not cap.isOpened():
        print(f"  could not open camera {source} - is it in use?")
        return {}
    # Some cameras only answer control requests once they are streaming.
    cap.read()

    axes = {"pan": UvcPTZ.CV_PAN, "tilt": UvcPTZ.CV_TILT,
            "zoom": UvcPTZ.CV_ZOOM}
    found = {}
    try:
        for axis, prop in axes.items():
            before = cap.get(prop)
            nudge = (before or 0) + (3600 if axis != "zoom" else 10)
            accepted = bool(cap.set(prop, nudge))
            after = cap.get(prop)
            moved = accepted and after != before
            print(f"  {axis:<5} accepted={accepted}  "
                  f"value {before} -> {after}"
                  + ("   <-- it moved" if moved else ""))
            if moved:
                found[axis] = {"control": f"opencv:{prop}", "value": before}
                cap.set(prop, before)        # put it back
    finally:
        cap.release()
    return found


def _set_control(device, control, value):
    """Set one UVC control, the Linux way."""
    v4l2 = shutil.which("v4l2-ctl")
    if not v4l2:
        return False
    try:
        done = subprocess.run(
            [v4l2, "-d", str(device), "--set-ctrl", f"{control}={int(value)}"],
            capture_output=True, text=True, timeout=5)
        return done.returncode == 0
    except Exception:
        return False


def _ask_direction(device, found):
    """Move the camera and ask which way it went.

    Nobody should have to read a datasheet to find out that their arrows are
    reversed, and nothing but the camera itself can answer it.
    """
    answers = {}
    questions = (
        ("pan", "pan_absolute", "left", "right", "l", "r"),
        ("tilt", "tilt_absolute", "down", "up", "d", "u"),
    )
    for axis, control, low_word, high_word, low_key, high_key in questions:
        info = found.get(axis) or {}
        if info.get("control") != control or info.get("max") is None:
            continue

        here = int(info.get("value") or 0)
        there = int(info["max"] * 0.35)
        if there == here:
            there = int(info["min"] * 0.35)

        print(f"\n  Watch the camera. Turning it on the {axis} axis now.")
        if not _set_control(device, control, there):
            print(f"  could not set {control} - skipping the {axis} check")
            continue
        time.sleep(2.0)
        _set_control(device, control, here)

        prompt = (f"  Which way did it go? "
                  f"[{low_key}] {low_word}  [{high_key}] {high_word}  "
                  f"[s] skip : ")
        try:
            said = input(prompt).strip().lower()[:1]
        except (EOFError, KeyboardInterrupt):
            print()
            return answers
        if said == low_key:
            # A bigger number moved it the way we call the lower one, so
            # every arrow for this axis has to be sent the other way round.
            answers[axis] = True
            print(f"  noted - the {axis} arrows need inverting")
        elif said == high_key:
            answers[axis] = False
            print(f"  noted - the {axis} arrows are the right way round")
    return answers


def probe(source=0, device=None, ask=None):
    """Print what this camera's motor exposes, and the config block for it."""
    device = device or (f"/dev/video{source}" if str(source).isdigit()
                        else source)

    print(f"Looking for a motor on camera {source}")
    found = uvc_controls(device)
    if found:
        print(f"  v4l2 controls on {device}:")
        for axis, c in sorted(found.items()):
            print(f"    {axis:<5} {c['control']:<18} "
                  f"min={c['min']} max={c['max']} step={c['step']} "
                  f"now={c['value']}")
    elif shutil.which("v4l2-ctl"):
        print(f"  v4l2 reports no pan/tilt/zoom control on {device}")
    else:
        print("  v4l2-ctl is not on this machine (normal on Windows), "
              "trying OpenCV instead")

    if not found:
        print("  asking OpenCV - watch the camera while this runs:")
        found = _probe_opencv(source)

    directions = {}
    if found and (ask if ask is not None else sys.stdin.isatty()):
        directions = _ask_direction(device, found)

    print()
    if "pan" in found or "tilt" in found:
        pan = found.get("pan", {})
        tilt = found.get("tilt", {})
        print("This camera's motor can be driven directly. Put this in the"
              " camera's config block:")
        print('    "ptz": {')
        print('        "type": "uvc",')
        print(f'        "device": "{device}",')
        if pan.get("step"):
            print(f'        "step": {pan["step"]},')
        if pan.get("min") is not None:
            print(f'        "pan_range": [{pan["min"]}, {pan["max"]}],')
        if tilt.get("min") is not None:
            print(f'        "tilt_range": [{tilt["min"]}, {tilt["max"]}],')
        for axis in ("pan", "tilt"):
            if directions.get(axis):
                print(f'        "invert_{axis}": True,')
        print('    },')
        if found and not directions:
            print()
            print("The arrows may come out reversed - which way a positive"
                  " number turns")
            print("a camera is not agreed between makers. If right goes left,"
                  " add")
            print('"invert_pan": True (or "invert_tilt": True) to that block.')
        return found

    print("No motor control that we can drive.")
    print()
    print("That is not a fault - plenty of gimbal webcams only move from")
    print("their maker's own app, over a private channel. What still works:")
    print("  - the camera's own tracking, if it has it. It follows a face or")
    print("    a person by itself, with nothing for us to send.")
    print("  - everything else in CabinWatch: recording, alerts, listening,")
    print("    talking. Only the arrows on the dashboard go away.")
    print("Leave the camera out of the ptz block, or set \"type\": \"none\".")
    return {}


def build(conf, cap=None):
    """Make the PTZ controller described by a camera's config block."""
    kind = (conf or {}).get("type", "none").lower()

    if kind == "onvif":
        return OnvifPTZ(conf.get("host", ""), conf.get("port", 80),
                        conf.get("user", ""), conf.get("password", ""))
    if kind == "uvc":
        rng = conf.get("pan_range")
        tlt = conf.get("tilt_range")
        return UvcPTZ(conf.get("device", "/dev/video0"), cap=cap,
                      step=conf.get("step", 3600),
                      pan_range=tuple(rng) if rng else None,
                      tilt_range=tuple(tlt) if tlt else None,
                      invert_pan=bool(conf.get("invert_pan", False)),
                      invert_tilt=bool(conf.get("invert_tilt", False)))
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
