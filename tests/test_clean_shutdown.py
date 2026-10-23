"""Simulates Ctrl+C right after a segment closes: is the clip saved?"""
import os, sys, time, shutil, subprocess, textwrap
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import tempfile as _tempfile
_WORK = _tempfile.mkdtemp(prefix='cabinwatch-test-')
_AGENT = _os.path.join(_ROOT, "agent")
W = _os.path.join(_WORK, "shutdown")
shutil.rmtree(W, ignore_errors=True); os.makedirs(W)

child = textwrap.dedent(f'''
    import sys, time, numpy as np
    sys.path.insert(0, r"{_AGENT}")
    from recorder import Recorder
    r = Recorder("c", clips_dir=r"{W}", fps=15, segment_seconds=600,
                 retention_hours=1, event_retention_hours=1, max_disk_gb=1.0,
                 always_record=True, want_audio=True, audio_rate=16000)
    f = np.full((240, 320, 3), 70, np.uint8)
    t0 = time.time()
    phase = 0
    while time.time() - t0 < 2.0:
        r.write(f)
        n = 16000 // 15
        tt = (np.arange(n) + phase) / 16000; phase += n
        r.write_audio((np.sin(2*np.pi*440*tt) * 0.4 * 32767).astype("<i2").tobytes())
        time.sleep(1/15)
    # exactly what the agent does on Ctrl+C
    r.stop()
    sys.exit(0)
''')
p = subprocess.run([sys.executable, "-c", child], capture_output=True, text=True, timeout=60)
print("child exited:", p.returncode, (p.stderr or "")[-200:])

d = os.path.join(W, "c")
mp4 = sorted(f for f in os.listdir(d) if f.endswith(".mp4"))
tmp = sorted(os.listdir(os.path.join(d, ".tmp")))
print(f"  finished clips : {mp4}")
print(f"  leftover temps : {tmp}")
if not mp4 and tmp:
    print("  --> THE LAST 2 SECONDS OF FOOTAGE WERE LOST ON SHUTDOWN")
elif mp4:
    print("  --> the clip survived")
