"""
Standing the picture up, wherever the camera happens to be mounted.

A phone hung upside down over a cot, or propped on its side against a wall,
sends a picture nobody can read. The turn has to happen on the device and
before anything else looks at the frame, so that the recording, the movement
detection and the live view are all the right way up - turning it only in
the viewer would leave the saved clips sideways, which is the one copy that
matters afterwards.

The trap here is that a quarter turn swaps width and height. Everything
downstream that assumed a fixed shape - the video writer above all - has to
cope, or the clip silently freezes on its first frame.
"""

import os
import sys
import shutil
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "agent"))

import cv2                                     # noqa: E402
import recorder as recorder_mod                # noqa: E402

PASSED = []
FAILED = []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(("PASS  " if ok else "FAIL  ") + name
          + (("  -- " + detail) if detail else ""))
    return bool(ok)


class Turner:
    """Just the rotation part of a camera session, lifted out to test."""

    TURNS = {
        90: cv2.ROTATE_90_CLOCKWISE,
        180: cv2.ROTATE_180,
        270: cv2.ROTATE_90_COUNTERCLOCKWISE,
    }

    def __init__(self, rotate=0):
        self.rotate = self._clean_rotation(rotate)

    @staticmethod
    def _clean_rotation(value):
        try:
            turn = int(value) % 360
        except (TypeError, ValueError):
            return 0
        return turn if turn in (0, 90, 180, 270) else 0

    def upright(self, frame):
        turn = self.TURNS.get(self.rotate)
        return frame if turn is None else cv2.rotate(frame, turn)


def marked_frame(width=160, height=120):
    """A frame with one bright corner, so a turn is visible in the pixels."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:12, :12] = 255              # top-left
    return frame


def corner_of(frame):
    """Which corner the bright patch ended up in."""
    h, w = frame.shape[:2]
    spots = {
        "top-left": frame[:12, :12],
        "top-right": frame[:12, -12:],
        "bottom-right": frame[-12:, -12:],
        "bottom-left": frame[-12:, :12],
    }
    return max(spots, key=lambda k: spots[k].mean())


def test_the_agent_turns_the_picture_the_way_it_says():
    frame = marked_frame()
    wanted = {0: "top-left", 90: "top-right",
              180: "bottom-right", 270: "bottom-left"}
    ok = True
    for turn, corner in wanted.items():
        out = Turner(turn).upright(frame)
        ok &= check(f"{turn} degrees puts the corner {corner}",
                    corner_of(out) == corner, corner_of(out))
    return ok


def test_a_quarter_turn_swaps_the_shape():
    frame = marked_frame(160, 120)
    sideways = Turner(90).upright(frame)
    over = Turner(180).upright(frame)
    ok = check("90 degrees swaps width and height",
               sideways.shape[:2] == (160, 120), str(sideways.shape[:2]))
    ok &= check("180 degrees leaves the shape alone",
                over.shape[:2] == (120, 160), str(over.shape[:2]))
    return ok


def test_only_quarter_turns_are_accepted():
    cases = {0: 0, 90: 90, 180: 180, 270: 270, 360: 0, 450: 90,
             -90: 270, 45: 0, "90": 90, None: 0, "sideways": 0}
    ok = True
    for given, want in cases.items():
        got = Turner(given).rotate
        ok &= check(f"{given!r} becomes {want}", got == want, str(got))
    return ok


def test_the_recorder_starts_a_new_clip_when_the_shape_changes():
    """The one that bites: a writer is opened at one size and only that size.

    Without this the frames after a turn are dropped by OpenCV without a
    word, and the clip sits frozen on whatever it had when the turn
    happened.
    """
    work = tempfile.mkdtemp(prefix="cabinwatch-rotate-")
    try:
        rec = recorder_mod.Recorder(
            "test", work, fps=10.0, segment_seconds=600,
            retention_hours=1, max_disk_gb=1, always_record=True,
            want_audio=False)
        wide = marked_frame(160, 120)
        tall = Turner(90).upright(wide)

        for _ in range(8):
            rec.write(wide)
        first = rec.current
        ok = check("a clip is being written", bool(first), str(first))

        for _ in range(8):
            rec.write(tall)
        second = rec.current
        ok &= check("turning the picture starts a new clip",
                    second and second != first, f"{first} -> {second}")
        ok &= check("and the new one is the new shape",
                    rec._size == (120, 160), str(rec._size))

        rec.stop(timeout=20)

        # Both clips must be playable, not a frozen first frame.
        played = []
        for name in (first, second):
            path = rec.path_of(name)
            if not path or not os.path.exists(path):
                played.append((name, 0))
                continue
            cap = cv2.VideoCapture(path)
            frames = 0
            while True:
                got, _ = cap.read()
                if not got:
                    break
                frames += 1
            cap.release()
            played.append((name, frames))
        ok &= check("both clips hold real frames",
                    all(n > 1 for _, n in played), str(played))
        return ok
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    for test in (test_the_agent_turns_the_picture_the_way_it_says,
                 test_a_quarter_turn_swaps_the_shape,
                 test_only_quarter_turns_are_accepted,
                 test_the_recorder_starts_a_new_clip_when_the_shape_changes):
        test()
    print()
    print(f"rotation: {len(PASSED)} passed, {len(FAILED)} failed")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
