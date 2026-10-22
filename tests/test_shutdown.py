#!/usr/bin/env python3
"""
What happens to footage when the agent stops.

Two different endings, and only one of them is savable:

  Ctrl+C / Stop      the clip is closed properly, so it must be kept. This
                     used to be lost every single time, because joining the
                     video to its sound ran on a daemon thread that died
                     with the process.

  power cut / kill   OpenCV holds an mp4 in memory and only writes its index
                     on release, so a segment interrupted mid-write is 44
                     bytes of nothing. No amount of recovery brings it back;
                     the only lever is how long a segment is.

  killed while joining   the video was already finalised, so this one IS
                     recoverable and must be picked up on the next start.

    python tests/test_shutdown.py
"""

import os
import shutil
import signal
import subprocess
import sys
import textwrap
import time

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.join(HERE, "..", "agent")
sys.path.insert(0, AGENT)

WORK = os.path.join(HERE, "..", ".test-shutdown")
PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {extra}" if extra else ""))


def recorder_script(work, seconds=None):
    stop = "r.stop()" if seconds else "pass"
    loop = (f"while time.time() - t0 < {seconds}:" if seconds else "while True:")
    return textwrap.dedent(f"""
        import sys, time, numpy as np
        sys.path.insert(0, {AGENT!r})
        from recorder import Recorder
        r = Recorder("c", clips_dir={work!r}, fps=15, segment_seconds=600,
                     retention_hours=1, event_retention_hours=1,
                     max_disk_gb=1.0, always_record=True, want_audio=True,
                     audio_rate=16000)
        f = np.full((240, 320, 3), 70, np.uint8)
        t0 = time.time(); phase = 0
        {loop}
            r.write(f)
            n = 16000 // 15
            tt = (np.arange(n) + phase) / 16000; phase += n
            r.write_audio((np.sin(2 * np.pi * 440 * tt) * 0.4 * 32767
                           ).astype("<i2").tobytes())
            time.sleep(1 / 15)
        {stop}
    """)


def clips(work):
    d = os.path.join(work, "c")
    return sorted(f for f in os.listdir(d) if f.endswith(".mp4"))


def temps(work):
    return sorted(os.listdir(os.path.join(work, "c", ".tmp")))


def main():
    shutil.rmtree(WORK, ignore_errors=True)

    # --- a normal stop must keep the footage ------------------------------
    w = os.path.join(WORK, "clean")
    os.makedirs(w)
    subprocess.run([sys.executable, "-c", recorder_script(w, seconds=2.0)],
                   check=True, timeout=90)
    check("Ctrl+C keeps the clip being recorded", bool(clips(w)), str(clips(w)))
    check("and leaves nothing half-written", not temps(w), str(temps(w)))

    # --- a hard kill cannot be saved, but must not leave junk behind ------
    w = os.path.join(WORK, "killed")
    os.makedirs(w)
    p = subprocess.Popen([sys.executable, "-c", recorder_script(w)])
    time.sleep(3.0)
    p.send_signal(signal.SIGKILL)
    p.wait()
    check("a power cut does leave the clip unwritten", not clips(w))
    size = max((os.path.getsize(os.path.join(w, "c", ".tmp", n))
                for n in temps(w) if n.endswith(".video.mp4")), default=0)
    check("the video file is unusable, as mp4 always is mid-write",
          size < 2048, f"{size} bytes - OpenCV writes the index only on close")

    from recorder import Recorder
    r = Recorder("c", clips_dir=w, fps=15, segment_seconds=600,
                 retention_hours=1, event_retention_hours=1, max_disk_gb=1.0,
                 always_record=True, want_audio=False)
    time.sleep(1.0)
    check("starting again clears the unusable pieces", not temps(w), str(temps(w)))
    r.stop()

    # --- killed after the video was finalised: this one IS recoverable ----
    w = os.path.join(WORK, "midjoin")
    os.makedirs(w)
    subprocess.run([sys.executable, "-c", recorder_script(w, seconds=2.0)],
                   check=True, timeout=90)
    done = clips(w)[0]
    stamp = done[:-4]
    # put it back the way a kill during the join would have left it
    tmp = os.path.join(w, "c", ".tmp")
    shutil.move(os.path.join(w, "c", done),
                os.path.join(tmp, f"{stamp}.video.mp4"))
    check("set up: a finalised video waiting to be joined",
          os.path.getsize(os.path.join(tmp, f"{stamp}.video.mp4")) > 2048)

    r = Recorder("c", clips_dir=w, fps=15, segment_seconds=600,
                 retention_hours=1, event_retention_hours=1, max_disk_gb=1.0,
                 always_record=True, want_audio=False)
    for _ in range(60):
        time.sleep(0.25)
        if clips(w):
            break
    check("starting again recovers it", bool(clips(w)), str(clips(w)))
    r.stop()
    check("and tidies up after itself", not temps(w), str(temps(w)))

    shutil.rmtree(WORK, ignore_errors=True)
    print(f"\n=== {len(PASS)} passed, {len(FAIL)} failed ===")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
