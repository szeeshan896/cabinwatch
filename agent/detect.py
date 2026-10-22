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
from collections import deque

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


class SceneChangeDetector:
    """Notices when the camera itself has been moved or covered.

    Someone walking past changes the picture for a moment and then it goes
    back. Moving the camera changes it and it stays changed. That difference
    is the whole test: the current view is compared against a reference of
    how this camera's view normally looks, and only a large change that is
    still there seconds later counts.

    Waiting it out is what keeps a person crossing the room, a door swinging
    or a light being switched on from raising a tamper alarm, and it is why
    this is a separate thing from movement detection rather than a louder
    version of it.
    """

    def __init__(self, changed_fraction=0.5, hold_seconds=4.0,
                 settle_seconds=25.0):
        self.changed_fraction = float(changed_fraction)
        self.hold_seconds = float(hold_seconds)
        self.settle_seconds = float(settle_seconds)

        self._reference = None
        self._reference_at = 0.0
        self._differing_since = 0.0
        self.last_fraction = 0.0

    def reset(self):
        self._reference = None
        self._differing_since = 0.0

    @staticmethod
    def _thumb(frame):
        """A small view of the frame, with brightness taken out of it.

        Switching a light on shifts every pixel at once, which looks exactly
        like a new view if you compare raw brightness - the first version of
        this raised a tamper alarm every time someone turned on the lamp.
        Subtracting the mean and dividing by the spread compares the shape of
        the scene instead, which a lamp does not change and moving the camera
        does.
        """
        small = cv2.resize(frame, (64, 48), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0).astype(np.float32)
        spread = float(gray.std())
        if spread < 4.0:
            # Nothing to compare: a blank view means the lens is covered or
            # the room is pitch dark.
            return None
        return (gray - gray.mean()) / spread

    def check(self, frame):
        """Returns True once, at the moment a move is confirmed."""
        now = time.time()
        thumb = self._thumb(frame)

        if thumb is None:
            # A covered lens is tampering in its own right, and is held for
            # the same time as any other change before it counts.
            self.last_fraction = 1.0
            if not self._differing_since:
                self._differing_since = now
                return False
            if now - self._differing_since >= self.hold_seconds:
                self._reference = None
                self._differing_since = 0.0
                return True
            return False

        if self._reference is None:
            self._reference = thumb
            self._reference_at = now
            self._differing_since = 0.0
            return False

        # How much the two views still look like each other. Counting
        # changed pixels separated a real move from a false one by far too
        # little; correlation puts an unchanged scene at ~0 and a new view
        # near 1 whatever the lighting, so the gap is wide enough to set a
        # threshold without tuning it to one room.
        changed = float(1.0 - np.mean(self._reference * thumb))
        self.last_fraction = max(0.0, min(2.0, changed))

        if changed < self.changed_fraction:
            # Back to normal, so whatever it was has passed.
            self._differing_since = 0.0
            # Let the reference drift with daylight and slow changes.
            if now - self._reference_at > self.settle_seconds:
                self._reference = thumb
                self._reference_at = now
            return False

        if not self._differing_since:
            self._differing_since = now
            return False

        if now - self._differing_since >= self.hold_seconds:
            # Still a different view after waiting: the camera moved.
            self._reference = thumb
            self._reference_at = now
            self._differing_since = 0.0
            return True
        return False


class SoundDetector:
    """Turns microphone samples into a cry, a thud, or ordinary noise.

    Two things here were learned the hard way.

    A cry is not a continuous sound. A baby wails, draws breath, wails again.
    Requiring it to be loud without a break for over a second - which is what
    this did at first - means a real cry resets the counter every time he
    inhales, and no alert is ever sent. So loudness is counted over a window
    instead: loud for enough of the last few seconds, gaps and all.

    And the threshold is relative to what the room normally sounds like,
    which is what lets it work in a nursery and next to a cooler without
    anybody tuning numbers. The absolute floor underneath it only exists to
    stop a silent room from alerting on its own hiss, so it has to stay low
    enough not to become the real threshold once a noise filter has pulled
    the levels down.
    """

    def __init__(self, rate=16000,
                 sensitivity=1.0, cry_seconds=1.0, quiet_floor=0.0008,
                 window_seconds=3.0, absolute_floor=0.0035, clock=None):
        self.rate = int(rate)
        # The clock is a parameter only so that a test can feed in eight
        # seconds of a crying baby without waiting eight seconds.
        self._clock = clock or time.time
        self.sensitivity = max(0.2, float(sensitivity))
        self.cry_seconds = float(cry_seconds)
        self.quiet_floor = float(quiet_floor)
        self.window_seconds = float(window_seconds)
        self.absolute_floor = float(absolute_floor)

        self.baseline = 0.004
        self.level = 0.0
        self.loud = False

        # (when, was_loud, voice_share) for the last few seconds.
        self._recent = deque()
        self._last_impact = 0.0
        self._last_call = 0.0

    def feed(self, samples, count=None):
        """Feed a block of mono samples in -1..1. Returns an event or None.

        Call it as the audio arrives: how long a sound has been going on is
        measured in real time.
        """
        x = np.asarray(samples, dtype=np.float32).reshape(-1)
        if count is not None:
            x = x[:count]
        if x.size == 0:
            return None

        rms = float(np.sqrt(np.mean(x * x)) + 1e-12)
        self.level = rms

        # Learn what quiet sounds like here, but only from quiet blocks.
        if rms < self.baseline * 2.0:
            self.baseline = self.baseline * 0.995 + rms * 0.005
        self.baseline = max(self.baseline, self.quiet_floor)

        bands = self._bands(x)
        low, voice = bands[0], bands[1]

        sens = self.sensitivity
        trigger = max(self.baseline * 4.5 / sens, self.absolute_floor / sens)
        self.loud = rms > trigger
        now = self._clock()

        self._recent.append((now, self.loud, voice))
        cutoff = now - self.window_seconds
        while self._recent and self._recent[0][0] < cutoff:
            self._recent.popleft()

        # A fall: sudden, very loud, and dominated by the low end.
        if (rms > max(self.baseline * 14, 0.085) and low > 0.45
                and now - self._last_impact > 5):
            self._last_impact = now
            self._recent.clear()
            return "impact"

        if not self.loud:
            return None

        loud_time, voice_time = self._loud_span()
        if loud_time < self.cry_seconds:
            return None

        # Was it voice-shaped for most of the time it was loud?
        if voice_time >= loud_time * 0.6:
            self._recent.clear()
            return "cry"
        if loud_time >= self.cry_seconds * 2:
            self._recent.clear()
            return "sound"
        return None

    def _loud_span(self):
        """Seconds of loudness in the window, and how much of it was voice."""
        if len(self._recent) < 2:
            return 0.0, 0.0
        loud_time = voice_time = 0.0
        for i in range(1, len(self._recent)):
            t0, was_loud, share = self._recent[i - 1]
            dt = self._recent[i][0] - t0
            if was_loud:
                loud_time += dt
                if share > 0.42:
                    voice_time += dt
        return loud_time, voice_time

    def _bands(self, x):
        """[below 250 Hz, 250-2500 Hz] as fractions of total energy."""
        n = 1
        while n * 2 <= len(x):
            n *= 2
        n = min(n, 2048)
        if n < 64:
            return 0.0, 0.0

        win = x[:n] * np.hanning(n)
        spec = np.abs(np.fft.rfft(win)) ** 2
        freqs = np.fft.rfftfreq(n, 1.0 / self.rate)
        total = float(spec.sum()) + 1e-12
        low = float(spec[freqs < 250].sum()) / total
        voice = float(spec[(freqs >= 250) & (freqs < 2500)].sum()) / total
        return low, voice
