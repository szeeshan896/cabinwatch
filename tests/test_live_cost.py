"""
What watching actually costs, in megabytes.

Every frame on the wire is a whole JPEG. Nothing is shared between one
frame and the next, so the bill is simply the size of a frame times the
frame rate - and that scales badly the moment somebody asks for a closer
look. HD was 1280 wide at 12 frames a second, which on a real frame from a
real camera measured at 1.7 GB for an hour of watching, on a phone's data
allowance and through the server both.

These numbers are measured here rather than asserted, so if somebody
raises a default later the test says what it now costs.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))

import cv2                                        # noqa: E402
import numpy as np                                # noqa: E402

os.environ.setdefault("AGENT_TOKEN", "x")
os.environ.setdefault("VIEW_PASSWORD", "y")
from server import settings                       # noqa: E402

PASSED = []
FAILED = []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(("PASS  " if ok else "FAIL  ") + name
          + (("  -- " + detail) if detail else ""))
    return bool(ok)


def a_room(width, height):
    """A room with furniture, texture and a little sensor noise."""
    rng = np.random.default_rng(11)
    frame = np.full((height, width, 3), 72, dtype=np.uint8)
    cv2.rectangle(frame, (int(width * .05), int(height * .25)),
                  (int(width * .45), int(height * .95)), (130, 128, 125), -1)
    cv2.rectangle(frame, (int(width * .55), int(height * .35)),
                  (int(width * .95), int(height * .8)), (80, 65, 58), -1)
    cv2.circle(frame, (int(width * .7), int(height * .5)),
               max(8, height // 10), (190, 185, 175), -1)
    frame = cv2.add(frame, rng.integers(0, 7, frame.shape, dtype=np.uint8))
    return frame


def megabytes_per_hour(width, quality, fps, shape=(16, 9)):
    height = int(width * shape[1] / shape[0])
    frame = a_room(width, height)
    ok, buf = cv2.imencode(".jpg", frame,
                           [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        return None
    return len(buf) * fps * 3600 / 1048576


def test_an_hour_of_live_is_affordable():
    cost = megabytes_per_hour(settings.LIVE_WIDTH, settings.LIVE_QUALITY,
                              settings.LIVE_FPS)
    return check("an hour of Live stays under a quarter of a gigabyte",
                 cost < 250,
                 f"{cost:.0f} MB/hour at {settings.LIVE_WIDTH}px "
                 f"q{settings.LIVE_QUALITY} {settings.LIVE_FPS:g}fps")


def test_an_hour_of_hd_is_not_a_data_plan():
    cost = megabytes_per_hour(settings.HD_WIDTH, settings.HD_QUALITY,
                              settings.HD_FPS)
    ok = check("an hour of HD stays under a gigabyte", cost < 1000,
               f"{cost:.0f} MB/hour at {settings.HD_WIDTH}px "
               f"q{settings.HD_QUALITY} {settings.HD_FPS:g}fps")

    live = megabytes_per_hour(settings.LIVE_WIDTH, settings.LIVE_QUALITY,
                              settings.LIVE_FPS)
    ok &= check("and HD is worth its extra cost, not ten times it",
                cost / live < 6.0, f"{cost / live:.1f} times Live")
    return ok


def test_hd_does_not_stay_on_by_itself():
    """Left on overnight by accident is somebody's whole data allowance."""
    ok = check("HD falls back to Live on its own",
               settings.HD_SECONDS > 0, f"{settings.HD_SECONDS:g}s")
    ok &= check("after long enough to actually look at something",
                30 <= settings.HD_SECONDS <= 600, f"{settings.HD_SECONDS:g}s")

    page = open(os.path.join(ROOT, "server", "templates",
                             "dashboard.html")).read()
    ok &= check("the page acts on it",
                'if (mode === "hd") holdHD(camId)' in page)
    ok &= check("and says so rather than just changing under you",
                "back to Live" in page)
    return ok


def test_what_a_real_codec_would_save():
    """Context for the decision, measured rather than guessed.

    This is not a requirement - it is the number that says whether moving
    the live view to a real video codec is worth the rewrite it would be.
    """
    import shutil
    import tempfile
    import subprocess
    import muxer

    exe = muxer.ffmpeg()
    if not exe:
        return check("a codec comparison needs ffmpeg", True, "skipped")

    width, fps = settings.HD_WIDTH, max(settings.HD_FPS, 1)
    height = int(width * 9 / 16)
    frames = []
    for i in range(int(fps * 6)):
        frame = a_room(width, height)
        cv2.circle(frame, (int(width * (0.2 + 0.5 * i / (fps * 6))),
                           int(height * 0.6)), max(8, height // 12),
                   (210, 200, 190), -1)
        frames.append(frame)

    jpeg = sum(len(cv2.imencode(".jpg", f,
               [int(cv2.IMWRITE_JPEG_QUALITY), settings.HD_QUALITY])[1])
               for f in frames) / len(frames) * fps * 3600 / 1048576

    work = tempfile.mkdtemp()
    try:
        raw = os.path.join(work, "raw")
        with open(raw, "wb") as f:
            for frame in frames:
                f.write(frame.tobytes())
        out = os.path.join(work, "out.mp4")
        subprocess.run([exe, "-y", "-loglevel", "error", "-f", "rawvideo",
                        "-pix_fmt", "bgr24", "-s", f"{width}x{height}",
                        "-r", str(fps), "-i", raw, "-c:v", "libx264",
                        "-preset", "veryfast", "-crf", "28",
                        "-g", str(int(fps * 2)), out],
                       check=True, capture_output=True)
        coded = (os.path.getsize(out) / (len(frames) / fps)) * 3600 / 1048576
    finally:
        shutil.rmtree(work, ignore_errors=True)

    saving = (1 - coded / jpeg) * 100
    print(f"      (HD as frames: {jpeg:.0f} MB/hour; as H.264: "
          f"{coded:.0f} MB/hour - {saving:.0f}% less)")
    return check("sending frames really is the expensive part",
                 coded < jpeg, f"{saving:.0f}% cheaper as video")


def main():
    for test in (test_an_hour_of_live_is_affordable,
                 test_an_hour_of_hd_is_not_a_data_plan,
                 test_hd_does_not_stay_on_by_itself,
                 test_what_a_real_codec_would_save):
        test()
    print()
    print(f"live cost: {len(PASSED)} passed, {len(FAILED)} failed")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
