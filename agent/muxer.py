"""
Putting the sound back into a recording.

OpenCV can write video and nothing else - it has no concept of an audio
track. That is fine for a shop camera and useless for a baby monitor, where
what you most want from last night is to hear whether he cried.

So each segment is written as two files, a silent .mp4 from OpenCV and a .wav
from the microphone the agent already has open for its alerts, and ffmpeg
joins them when the segment closes. Reusing the open microphone matters:
Windows will not reliably hand the same device to two recorders at once.

Sync is the fiddly part. OpenCV stamps the video with the frame rate we asked
for, but a camera rarely delivers exactly that, so a clip written at a
nominal 15 fps that really ran at 12 plays too fast and drifts away from the
sound. We measure the frames actually written and the time they took, and
stretch the video timestamps to match real time before muxing.
"""

import os
import shutil
import subprocess
import threading


_ffmpeg_path = None
_ffmpeg_checked = False
_warned = False


def ffmpeg() -> str | None:
    """Path to an ffmpeg we can use, or None. Looked up once."""
    global _ffmpeg_path, _ffmpeg_checked
    if _ffmpeg_checked:
        return _ffmpeg_path
    _ffmpeg_checked = True

    # Shipped with the agent, so a standalone build needs nothing installed.
    try:
        import imageio_ffmpeg
        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and os.path.exists(path):
            _ffmpeg_path = path
            return _ffmpeg_path
    except Exception:
        pass

    # Otherwise whatever is on PATH.
    _ffmpeg_path = shutil.which("ffmpeg")
    return _ffmpeg_path


def available() -> bool:
    return ffmpeg() is not None


def warn_once():
    global _warned
    if _warned:
        return
    _warned = True
    print("[rec] ffmpeg not found, so recordings will have no sound.")
    print("[rec] Fix:  pip install imageio-ffmpeg")


def mux(video_path, audio_path, out_path, declared_fps, frames, seconds,
        on_done=None):
    """Join a silent video and a wav into out_path, in the background.

    declared_fps is what the video was written with; frames and seconds are
    what actually happened, and the difference between them is corrected so
    the sound lines up.
    """
    t = threading.Thread(
        target=_mux_worker,
        args=(video_path, audio_path, out_path, declared_fps, frames,
              seconds, on_done),
        daemon=True,
    )
    t.start()
    return t


def _timestamp_scale(declared_fps, frames, seconds):
    """How much to stretch the video's clock so it matches the wall clock."""
    if not frames or not seconds or seconds <= 0 or declared_fps <= 0:
        return 1.0
    actual_fps = frames / seconds
    if actual_fps <= 0:
        return 1.0
    scale = declared_fps / actual_fps
    # Only correct a real discrepancy; refuse a wild one, which would mean
    # our own measurements are wrong and stretching would make it worse.
    if scale < 0.2 or scale > 5.0:
        return 1.0
    return scale


def _repackage(exe, video_path, out_path, scale):
    """Put a video into an mp4 without re-encoding it."""
    cmd = [exe, "-y", "-loglevel", "error", "-nostdin"]
    if abs(scale - 1.0) > 0.01:
        cmd += ["-itsscale", f"{scale:.6f}"]
    cmd += ["-i", video_path, "-c", "copy", "-movflags", "+faststart",
            out_path]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=180)
    except Exception as e:
        print(f"[rec] could not repackage the clip: {e}")
        return False
    if r.returncode == 0 and os.path.isfile(out_path) \
            and os.path.getsize(out_path) > 0:
        return True
    err = (r.stderr or b"").decode("utf-8", "replace")[:300]
    print(f"[rec] could not repackage the clip: {err.strip()}")
    return False


def _mux_worker(video_path, audio_path, out_path, declared_fps, frames,
                seconds, on_done):
    ok = False
    try:
        exe = ffmpeg()
        has_audio = bool(audio_path) and os.path.isfile(audio_path) \
            and os.path.getsize(audio_path) > 1024

        if exe is None or not has_audio:
            if exe is None:
                warn_once()
            if not os.path.isfile(video_path):
                return
            # Nothing to join - but a Matroska temp file still has to be
            # repackaged, because renaming it to .mp4 would hand the
            # dashboard a file that is not the thing its name claims.
            if exe is not None and os.path.splitext(video_path)[1] != ".mp4":
                ok = _repackage(exe, video_path, out_path,
                                _timestamp_scale(declared_fps, frames,
                                                 seconds))
                if ok:
                    return
            os.replace(video_path, out_path)
            ok = True
            return

        scale = _timestamp_scale(declared_fps, frames, seconds)
        cmd = [exe, "-y", "-loglevel", "error", "-nostdin"]
        if abs(scale - 1.0) > 0.01:
            cmd += ["-itsscale", f"{scale:.6f}"]
        cmd += [
            "-i", video_path,
            "-i", audio_path,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "64k", "-ac", "1",
            "-movflags", "+faststart",
            "-shortest",
            out_path,
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=180)
        if r.returncode == 0 and os.path.isfile(out_path) \
                and os.path.getsize(out_path) > 0:
            ok = True
        else:
            err = (r.stderr or b"").decode("utf-8", "replace")[:300]
            print(f"[rec] could not add sound ({err.strip()}), "
                  f"keeping the clip silent")
            if os.path.isfile(video_path):
                os.replace(video_path, out_path)
                ok = True
    except Exception as e:
        print(f"[rec] muxing failed: {e}")
        try:
            if os.path.isfile(video_path):
                os.replace(video_path, out_path)
                ok = True
        except Exception:
            pass
    finally:
        for p in (video_path, audio_path):
            try:
                if p and os.path.isfile(p):
                    os.remove(p)
            except OSError:
                pass
        if on_done is not None:
            try:
                on_done(out_path, ok)
            except Exception:
                pass
