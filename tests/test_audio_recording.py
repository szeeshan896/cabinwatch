"""Does a saved clip actually contain sound, and is it in sync?"""
import os, sys, json, time, shutil, subprocess
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
sys.path.insert(0, _os.path.join(_ROOT, "agent"))
import numpy as np, cv2
import muxer
from recorder import Recorder

SP = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(SP, "audio_rec"); shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK)

PASS, FAIL = [], []
def check(n, ok, extra=""):
    (PASS if ok else FAIL).append(n)
    print(f"  {'PASS' if ok else 'FAIL'}  {n}" + (f"   {extra}" if extra else ""))

_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import mediainfo

def probe(path):
    # Not ffprobe directly: the agent ships ffmpeg alone, so ffprobe is on a
    # developer's machine and not on a build runner, and a test that needs
    # it passes in one place and fails in the other.
    return mediainfo.describe(path)

FPS = 15.0
rec = Recorder("cam", clips_dir=WORK, fps=FPS, segment_seconds=3,
               retention_hours=24, event_retention_hours=48, max_disk_gb=1.0,
               always_record=True, audio_rate=16000, want_audio=True)

# 3 seconds of video, and 3 seconds of a 440 Hz tone to go with it.
AUDIO_RATE, BLOCK = 16000, 1024
phase = 0
t0 = time.time()
frames = 0
while time.time() - t0 < 3.2:
    img = np.full((360, 640, 3), 30, np.uint8)
    x = int((time.time() - t0) * 150) % 500
    cv2.rectangle(img, (x, 120), (x+100, 240), (220,220,220), -1)
    rec.write(img); frames += 1
    # feed audio at roughly real time alongside the frames
    n = int(AUDIO_RATE / FPS)
    tt = (np.arange(n) + phase) / AUDIO_RATE
    phase += n
    tone = (np.sin(2*np.pi*440*tt) * 0.4 * 32767).astype("<i2")
    rec.write_audio(tone.tobytes())
    time.sleep(1.0 / FPS)

rec._close()
rec.stop()
print(f"  (wrote {frames} frames over {time.time()-t0:.1f}s)")

# muxing happens in the background
for _ in range(60):
    time.sleep(0.25)
    done = [f for f in os.listdir(os.path.join(WORK, "cam")) if f.endswith(".mp4")]
    if done: break

clips = sorted(f for f in os.listdir(os.path.join(WORK,"cam")) if f.endswith(".mp4"))
check("a clip was produced", len(clips) >= 1, str(clips))
if not clips:
    sys.exit(1)

path = os.path.join(WORK, "cam", clips[0])
info = probe(path)
check("the clip is a readable video file", info is not None)
if info:
    kinds = [st["codec_type"] for st in info["streams"]]
    check("it has a VIDEO track", "video" in kinds, str(kinds))
    check("it has an AUDIO track", "audio" in kinds, str(kinds))
    a = next((st for st in info["streams"] if st["codec_type"]=="audio"), None)
    if a:
        check("audio is AAC mono", a["codec_name"]=="aac" and a["channels"]==1,
              f'{a["codec_name"]} / {a["channels"]}ch / {a["sample_rate"]}Hz')
    dur = float(info["duration"])
    check("duration is about 3 seconds", 2.3 < dur < 4.2, f"{dur:.2f}s")

    v = next((st for st in info["streams"] if st["codec_type"]=="video"), None)
    if v and a and v.get("duration") and a.get("duration"):
        vd, ad = float(v["duration"]), float(a["duration"])
        check("sound and picture are the same length (in sync)",
              abs(vd-ad) < 0.5, f"video {vd:.2f}s vs audio {ad:.2f}s")
    elif v and a:
        print("  ..    sync unchecked here - no ffprobe to ask for the "
              "length of each track")

check("no half-written leftovers",
      not os.listdir(os.path.join(WORK,"cam",".tmp")),
      str(os.listdir(os.path.join(WORK,"cam",".tmp"))))

# ---- and the silent path still works -----------------------------------
rec2 = Recorder("mute", clips_dir=WORK, fps=FPS, segment_seconds=2,
                retention_hours=24, event_retention_hours=48, max_disk_gb=1.0,
                always_record=True, want_audio=False)
t0 = time.time()
while time.time() - t0 < 2.2:
    rec2.write(np.full((240,320,3), 60, np.uint8)); time.sleep(1/FPS)
rec2._close(); rec2.stop()
for _ in range(40):
    time.sleep(0.25)
    c2 = [f for f in os.listdir(os.path.join(WORK,"mute")) if f.endswith(".mp4")]
    if c2: break
check("a camera with no microphone still records video", bool(c2), str(c2))
if c2:
    i2 = probe(os.path.join(WORK,"mute",c2[0]))
    check("that one is video-only, and still plays",
          i2 is not None and "audio" not in [st["codec_type"] for st in i2["streams"]])

print(f"\n=== {len(PASS)} passed, {len(FAIL)} failed ===")
for f in FAIL: print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
