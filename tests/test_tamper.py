#!/usr/bin/env python3
"""
Telling "somebody moved the camera" apart from "somebody walked past it".

This is the alarm that matters when the camera itself is the target: a phone
quietly turned to face a wall protects nobody, and does it silently. But it
has to be the one alarm that is never cried wolf, because an alert that goes
off when a lamp is switched on gets ignored within a night - and then the
real one is ignored too.

Two things make it work. Only a change still there seconds later counts, so
people crossing the room do not trigger it. And the comparison is of the
shape of the scene rather than its brightness, so lights and dusk do not.

    python tests/test_tamper.py
"""

import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "agent"))
from detect import SceneChangeDetector          # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {extra}" if extra else ""))


def room(shift=0, bright=0, contrast=1.0, noise=0.0):
    """A plain room with enough in it to have a recognisable shape."""
    img = np.full((480, 640, 3), 50, np.uint8)
    cv2.rectangle(img, (100 + shift, 150), (300 + shift, 350), (160, 150, 140), -1)
    cv2.rectangle(img, (380 + shift, 80), (560 + shift, 300), (90, 100, 110), -1)
    cv2.circle(img, (200 + shift, 420), 40, (200, 190, 180), -1)
    cv2.putText(img, "SHELF", (110 + shift, 260), cv2.FONT_HERSHEY_SIMPLEX,
                1.2, (60, 60, 60), 3)
    out = img.astype(np.float32) * contrast + bright
    if noise:
        out += np.random.randn(*out.shape) * noise
    return np.clip(out, 0, 255).astype(np.uint8)


def person(x, width=90):
    img = room()
    cv2.rectangle(img, (x, 120), (x + width, 440), (210, 200, 190), -1)
    return img


def blank(level):
    return np.full((480, 640, 3), level, np.uint8)


def play(frames, hold=1.0, step=0.25):
    d = SceneChangeDetector(hold_seconds=hold, settle_seconds=100)
    fired = False
    for f in frames:
        if d.check(f):
            fired = True
        time.sleep(step)
    return fired


def main():
    quiet = [
        ("a still room with sensor noise", [room(noise=2.0)] * 12),
        ("somebody walks across and leaves",
         [room()] * 2 + [person(x) for x in range(40, 470, 55)] + [room()] * 6),
        ("the light is switched on", [room()] * 3 + [room(bright=55)] * 9),
        ("the room dims at dusk",
         [room()] * 3 + [room(bright=-28, contrast=0.7)] * 9),
        ("somebody stands in frame for a while",
         [room()] * 3 + [person(260, 140)] * 8 + [room()] * 3),
        ("a slow brightening over minutes",
         [room(bright=b) for b in range(0, 60, 5)]),
    ]
    for name, frames in quiet:
        check(f"stays quiet: {name}", not play(frames))

    moved = [
        ("the camera is turned to face elsewhere",
         [room()] * 3 + [room(shift=260)] * 10),
        ("the camera is knocked askew", [room()] * 3 + [room(shift=120)] * 10),
        ("the lens is covered", [room()] * 3 + [blank(8)] * 10),
        ("the phone is picked up and pointed at a wall",
         [room()] * 3 + [blank(120) + np.random.randint(
             0, 18, (480, 640, 3), dtype=np.uint8) for _ in range(10)]),
    ]
    for name, frames in moved:
        check(f"raises the alarm: {name}", play(frames))

    # A brief interruption must not be enough, however total it is.
    check("a hand passing over the lens is too brief to count",
          not play([room()] * 3 + [blank(5)] * 2 + [room()] * 8, hold=2.0))

    print(f"\n=== {len(PASS)} passed, {len(FAIL)} failed ===")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
