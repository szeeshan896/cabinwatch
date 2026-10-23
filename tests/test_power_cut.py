"""
What survives when the power goes, mid-recording.

An mp4 is only playable once it has been closed properly: the index that
says where every frame is gets written at the very end. Pull the plug in the
middle and there is no index, so the file is not short - it is unopenable.
"moov atom not found", 44 bytes of nothing, and the whole segment gone. With
five-minute segments that is up to five minutes of exactly the footage
somebody would want after an incident.

Matroska writes as it goes. The same pull now costs the few seconds still
sitting in the write buffer, and the rest plays. The segment is still
delivered as an mp4 - it is repackaged when it closes, or on the next start
if there was no chance to close it.
"""

import os
import sys
import glob
import time
import shutil
import signal
import tempfile
import textwrap
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.join(os.path.dirname(HERE), "agent")
sys.path.insert(0, AGENT)

import muxer                                   # noqa: E402

PASSED = []
FAILED = []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(("PASS  " if ok else "FAIL  ") + name
          + (("  -- " + detail) if detail else ""))
    return bool(ok)


def ffprobe(path, entries="stream=codec_type"):
    """Ask ffmpeg what is actually in a file. Empty means unreadable."""
    exe = shutil.which("ffprobe")
    if exe is None:
        return None                      # cannot tell here; not a failure
    out = subprocess.run(
        [exe, "-v", "error", "-show_entries", entries, "-of", "csv=p=0", path],
        capture_output=True, text=True)
    if out.returncode != 0:
        return []
    return [line for line in out.stdout.split() if line]


def readable_frames(path):
    got = ffprobe(path, "stream=nb_read_frames")
    if got is None:
        return None
    exe = shutil.which("ffprobe")
    out = subprocess.run(
        [exe, "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", path],
        capture_output=True, text=True)
    try:
        return int(out.stdout.strip())
    except ValueError:
        return 0


WRITER = '''
    import sys, time, numpy as np, cv2
    sys.path.insert(0, {agent!r})
    from recorder import Recorder
    r = Recorder("c", clips_dir={work!r}, fps=15, segment_seconds=600,
                 retention_hours=1, max_disk_gb=1.0, always_record=True,
                 want_audio={audio}, audio_rate=16000)
    rng = np.random.default_rng(2)
    base = rng.integers(0, 255, (480, 640, 3), dtype=np.uint8)
    phase, i = 0, 0
    while True:
        frame = base.copy(); i += 1
        cv2.putText(frame, str(i), (30, 300), cv2.FONT_HERSHEY_SIMPLEX, 4,
                    (0, 0, 0), 8)
        r.write(frame)
        if {audio}:
            n = 16000 // 15
            t = (np.arange(n) + phase) / 16000; phase += n
            r.write_audio((np.sin(2 * np.pi * 440 * t) * 0.4 * 32767
                           ).astype("<i2").tobytes())
        time.sleep(1 / 15)
'''


def pull_the_plug(work, seconds=8.0, audio=True):
    """Record for a while, then kill the process outright."""
    code = textwrap.dedent(WRITER).format(agent=AGENT, work=work,
                                          audio=bool(audio))
    proc = subprocess.Popen([sys.executable, "-c", code],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    time.sleep(seconds)
    proc.send_signal(signal.SIGKILL)
    proc.wait()
    return int(seconds * 15)


def test_the_half_written_clip_is_still_readable():
    work = tempfile.mkdtemp(prefix="cabinwatch-cut-")
    try:
        written = pull_the_plug(work, 8.0, audio=False)
        tmp = os.path.join(work, "c", ".tmp")
        left = os.listdir(tmp)
        video = [n for n in left if n.endswith((".mkv", ".mp4"))]
        ok = check("a killed recording leaves its video behind",
                   bool(video), str(left))
        if not video:
            return False

        path = os.path.join(tmp, video[0])
        frames = readable_frames(path)
        if frames is None:
            return check("ffprobe is not here to check with", True, "skipped")

        kept = frames / max(written, 1)
        ok &= check("most of what was recorded can still be read",
                    kept > 0.75,
                    f"{frames} of about {written} frames ({kept * 100:.0f}%)")
        lost = (written - frames) / 15.0
        print(f"      (the power cut cost the last {lost:.1f} seconds)")
        return ok
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_starting_again_turns_it_into_a_playable_clip():
    work = tempfile.mkdtemp(prefix="cabinwatch-cut-")
    try:
        pull_the_plug(work, 8.0, audio=True)
        clips = os.path.join(work, "c")
        before = glob.glob(os.path.join(clips, "*.mp4"))
        ok = check("nothing is playable yet", not before, str(before))

        # Start the agent's recorder again, exactly as it would on boot.
        import recorder as recorder_mod
        rec = recorder_mod.Recorder(
            "c", clips_dir=work, fps=15, segment_seconds=600,
            retention_hours=1, max_disk_gb=1.0, always_record=True,
            want_audio=True, audio_rate=16000)
        found = []
        for _ in range(80):
            time.sleep(0.25)
            found = glob.glob(os.path.join(clips, "*.mp4"))
            if found:
                break
        ok &= check("starting again recovers the footage", bool(found),
                    str([os.path.basename(f) for f in found]))

        if found:
            kinds = ffprobe(found[0])
            if kinds is None:
                print("      (ffprobe is not here, so the contents go "
                      "unchecked)")
            else:
                ok &= check("and the recovered clip really plays",
                            "video" in kinds, str(kinds))
                if muxer.available():
                    ok &= check("with the sound that was recorded with it",
                                "audio" in kinds, str(kinds))

        rec.stop(timeout=30)
        leftover = os.listdir(os.path.join(clips, ".tmp"))
        ok &= check("and nothing is left lying around", not leftover,
                    str(leftover))
        return ok
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_an_mp4_is_what_the_dashboard_gets():
    """Whatever we record into, a clip has to arrive as the mp4 it claims."""
    work = tempfile.mkdtemp(prefix="cabinwatch-cut-")
    try:
        import numpy as np
        import recorder as recorder_mod
        rec = recorder_mod.Recorder(
            "c", clips_dir=work, fps=15, segment_seconds=2,
            retention_hours=1, max_disk_gb=1.0, always_record=True,
            want_audio=False)
        rng = np.random.default_rng(3)
        frame = rng.integers(0, 255, (240, 320, 3), dtype=np.uint8)
        for _ in range(40):
            rec.write(frame)
            time.sleep(0.02)
        rec.stop(timeout=30)

        clips = sorted(glob.glob(os.path.join(work, "c", "*.mp4")))
        ok = check("a finished clip is there", bool(clips),
                   str([os.path.basename(c) for c in clips]))
        if clips:
            with open(clips[0], "rb") as f:
                head = f.read(12)
            # Every mp4 carries "ftyp" in its first box; Matroska starts 1A45.
            ok &= check("and it is a real mp4, not a renamed Matroska",
                        b"ftyp" in head, repr(head))
        return ok
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    print(f"ffmpeg available: {muxer.available()}")
    for test in (test_the_half_written_clip_is_still_readable,
                 test_starting_again_turns_it_into_a_playable_clip,
                 test_an_mp4_is_what_the_dashboard_gets):
        test()
    print()
    print(f"power cut: {len(PASSED)} passed, {len(FAILED)} failed")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
