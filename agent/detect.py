"""
Deciding when something actually happened.

Movement alone is a bad alarm: a ceiling fan, a curtain, headlights sweeping a
wall and the sun coming out will all set it off, and after a few nights of
false alarms you stop trusting your phone. So:

  - movement is measured on a blurred, downscaled frame, and you can mask out
    regions (the fan, a window) that should always be ignored
  - for the cabin, movement can additionally be checked for a person shape
    before it is allowed to alert you
  - sound is compared against what "quiet" sounds like in that room, which the
    agent learns by itself, and then split into a baby's cry, a thud, or just
    noise by where its energy sits in the frequency range
"""

import time

import cv2
import numpy as np


class MotionDetector:
    def __init__(self, min_area=1500, threshold=25, mask=None, blur=21):
        self.min_area = int(min_area)
        self.threshold = int(threshold)
        self.blur = int(blur) | 1          # OpenCV wants an odd kernel
        self.mask_spec = mask or []
        self._mask = None
        self._prev = None
        self.last_boxes = []

    def reset(self):
        self._prev = None

    def _build_mask(self, shape):
        """mask_spec is a list of [x, y, w, h] in 0..1 of the frame to ignore."""
        h, w = shape[:2]
        m = np.full((h, w), 255, dtype=np.uint8)
        for region in self.mask_spec:
            try:
                rx, ry, rw, rh = region
                x0, y0 = int(rx * w), int(ry * h)
                x1, y1 = int((rx + rw) * w), int((ry + rh) * h)
                m[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = 0
            except Exception:
                continue
        return m

    def detect(self, frame):
        """Returns (moved, fraction_changed, boxes)."""
        small = cv2.resize(frame, (320, 240), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (self.blur, self.blur), 0)

        if self._prev is None:
            self._prev = gray
            return False, 0.0, []

        delta = cv2.absdiff(self._prev, gray)
        self._prev = gray

        thresh = cv2.threshold(delta, self.threshold, 255, cv2.THRESH_BINARY)[1]
        if self.mask_spec:
            if self._mask is None or self._mask.shape != thresh.shape:
                self._mask = self._build_mask(thresh.shape)
            thresh = cv2.bitwise_and(thresh, self._mask)
        thresh = cv2.dilate(thresh, None, iterations=2)

        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        # min_area is given for a full-size frame; scale it to our 320x240 work
        # image so the same config value behaves the same on any camera.
        scale = (320 * 240) / float(frame.shape[0] * frame.shape[1])
        min_area = max(12, self.min_area * scale)

        boxes = []
        for c in contours:
            if cv2.contourArea(c) >= min_area:
                x, y, w, h = cv2.boundingRect(c)
                boxes.append((x / 320, y / 240, w / 320, h / 240))

        self.last_boxes = boxes
        changed = float(np.count_nonzero(thresh)) / thresh.size
        return bool(boxes), changed, boxes


class PersonDetector:
    """Checks whether a moving thing is person-shaped.

    Uses OpenCV's built-in HOG pedestrian detector, which needs no model
    download, and is only ever run on a frame that already showed movement -
    so it costs almost nothing while the room is still. If you point it at
    MobileNet-SSD files in config it will use those instead, which is both
    faster and more accurate.
    """

    def __init__(self, enabled=False, proto=None, model=None, confidence=0.5):
        self.enabled = bool(enabled)
        self.confidence = float(confidence)
        self.kind = None
        self._hog = None
        self._net = None
        if not self.enabled:
            return

        if proto and model:
            try:
                self._net = cv2.dnn.readNetFromCaffe(proto, model)
                self.kind = "dnn"
                print("[detect] person detection: MobileNet-SSD")
                return
            except Exception as e:
                print(f"[detect] could not load the SSD model ({e}), "
                      f"falling back to the built-in detector")
        if not hasattr(cv2, "HOGDescriptor"):
            # OpenCV 5 removed it from the Python bindings.
            print(f"[detect] person detection needs OpenCV 4.x, but this "
                  f"machine has {cv2.__version__}.")
            print("[detect] This camera will alert on ANY movement, so a fan "
                  "or a curtain can set it off.")
            print("[detect] Fix:  pip install \"opencv-python>=4.9,<5\"")
            self.enabled = False
            return

        try:
            self._hog = cv2.HOGDescriptor()
            self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
            self.kind = "hog"
            print("[detect] person detection: built-in HOG")
        except Exception as e:
            print(f"[detect] person detection unavailable: {e}")
            print("[detect] This camera will alert on ANY movement.")
            self.enabled = False

    def contains_person(self, frame):
        if not self.enabled:
            return False
        try:
            if self.kind == "dnn":
                small = cv2.resize(frame, (300, 300))
                blob = cv2.dnn.blobFromImage(small, 0.007843, (300, 300),
                                             127.5)
                self._net.setInput(blob)
                out = self._net.forward()
                for i in range(out.shape[2]):
                    conf = float(out[0, 0, i, 2])
                    cls = int(out[0, 0, i, 1])
                    if cls == 15 and conf >= self.confidence:   # 15 = person
                        return True
                return False

            small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
            found, _ = self._hog.detectMultiScale(
                small, winStride=(8, 8), padding=(8, 8), scale=1.06)
            return len(found) > 0
        except Exception:
            return False


class SoundDetector:
    """Turns a stream of microphone samples into sound / cry / thud events.

    Thresholds are relative to a baseline the detector learns from the quiet
    stretches, so a noisy room and a silent nursery both end up sensible
    without you tuning numbers.
    """

    def __init__(self, rate=16000, sensitivity=1.0, cry_seconds=1.2,
                 quiet_floor=0.0008):
        self.rate = int(rate)
        self.sensitivity = max(0.2, float(sensitivity))
        self.cry_seconds = float(cry_seconds)
        self.quiet_floor = float(quiet_floor)

        self.baseline = 0.004
        self.level = 0.0
        self.loud = False
        self._loud_since = 0.0
        self._last_impact = 0.0

    def feed(self, samples):
        """Feed a block of float32 mono samples. Returns an event kind or None.

        Possible returns: "impact", "cry", "sound", or None.
        """
        if samples is None or len(samples) == 0:
            return None

        x = np.asarray(samples, dtype=np.float32).reshape(-1)
        rms = float(np.sqrt(np.mean(x * x)) + 1e-12)
        self.level = rms

        # Learn the room's quiet level, but only from quiet blocks.
        if rms < self.baseline * 2.0:
            self.baseline = self.baseline * 0.995 + rms * 0.005
        self.baseline = max(self.baseline, self.quiet_floor)

        # Where is the energy? A cry sits in the voice band; a thud is mostly
        # low frequency; a fan or AC hum is low frequency but steady.
        win = x * np.hanning(len(x))
        spec = np.abs(np.fft.rfft(win)) ** 2
        freqs = np.fft.rfftfreq(len(win), 1.0 / self.rate)
        total = float(spec.sum()) + 1e-12
        low = float(spec[freqs < 250].sum()) / total
        voice = float(spec[(freqs >= 250) & (freqs < 2500)].sum()) / total

        trigger = max(self.baseline * 4.5 / self.sensitivity, 0.010)
        self.loud = rms > trigger
        now = time.time()

        if self.loud:
            if not self._loud_since:
                self._loud_since = now
        else:
            self._loud_since = 0.0
            return None

        # A fall: very sudden, very loud, dominated by the low end.
        if (rms > max(self.baseline * 14, 0.085) and low > 0.45
                and now - self._last_impact > 5):
            self._last_impact = now
            self._loud_since = 0.0
            return "impact"

        held = now - self._loud_since
        if held >= self.cry_seconds and voice > 0.42:
            self._loud_since = 0.0
            return "cry"
        if held >= 2.5:
            self._loud_since = 0.0
            return "sound"
        return None
