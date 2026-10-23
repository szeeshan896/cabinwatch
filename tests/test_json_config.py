"""Checks a cabinwatch.json (what the .exe uses) drives the real agent."""
import os
import tempfile as _tempfile, sys, json, time, shutil, threading, urllib.request, http.cookiejar
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))

os.environ.update(AGENT_TOKEN="tok-json", VIEW_PASSWORD="pw",
                  SECRET_KEY="fixed", PORT="8097")
# Keep this run's history and saved clips out of every other run's.
os.environ.setdefault("EVENT_LOG", _tempfile.mkdtemp(
    prefix="cabinwatch-state-") + "/events.json")
os.environ["EVIDENCE_DIR"] = os.path.dirname(os.environ["EVENT_LOG"]) + "/clips"
os.environ["SCHEDULE_FILE"] = os.path.dirname(os.environ["EVENT_LOG"]) + "/s.json"

sys.path.insert(0, _ROOT)
sys.path.insert(0, _os.path.join(_ROOT, "agent"))

import cv2, numpy as np, uvicorn, websocket
import setup_wizard, agent as agent_mod

SP = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(SP, "json_run")
shutil.rmtree(WORK, ignore_errors=True); os.makedirs(WORK)

PASS, FAIL = [], []
def check(n, ok, extra=""):
    (PASS if ok else FAIL).append(n)
    print(f"  {'PASS' if ok else 'FAIL'}  {n}" + (f"   {extra}" if extra else ""))

# a short moving clip to act as the camera
VID = os.path.join(WORK, "scene.mp4")
vw = cv2.VideoWriter(VID, cv2.VideoWriter_fourcc(*"mp4v"), 15, (640, 480))
for i in range(15 * 20):
    f = np.full((480, 640, 3), 40, dtype=np.uint8)
    x = 60 if i < 30 else 60 + int((i - 30) * 7) % 400
    cv2.rectangle(f, (x, 180), (x + 110, 300), (230, 230, 230), -1)
    vw.write(f)
vw.release()

# ---- exactly the JSON the wizard writes ---------------------------------
data = {
    "server_url": "http://127.0.0.1:8097",
    "agent_token": "tok-json",
    "clips_dir": os.path.join(WORK, "clips"),
    "retention_hours": 24,
    "max_disk_gb": 5.0,
    "segment_seconds": 4,
    "event_min_gap": 5.0,
    "cameras": {
        "baby-room": {
            "name": "Baby Room",
            "source": VID,
            "baby_mode": True,
            "audio": False,
            "speaker": False,
            "motion_min_area": 900,
            "sound_sensitivity": 1.3,
        }
    },
}
written = os.path.join(WORK, "cabinwatch.json")
open(written, "w").write(json.dumps(data, indent=2))
cfg = setup_wizard.JsonConfig(json.load(open(written)))
check("cabinwatch.json parses into a usable config", cfg.SERVER_URL.endswith("8097"))
check("camera block survives the round trip",
      cfg.CAMERAS["baby-room"]["baby_mode"] is True)
check("every value the agent reads is present",
      all(hasattr(cfg, k) for k in (
          "SERVER_URL","AGENT_TOKEN","VERIFY_TLS","CLIPS_DIR","SEGMENT_SECONDS",
          "RETENTION_HOURS","EVENT_RETENTION_HOURS","MAX_DISK_GB","ALWAYS_RECORD",
          "PREBUFFER_SECONDS","CAPTURE_FPS","MOTION_MIN_AREA","MOTION_THRESHOLD",
          "SOUND_SENSITIVITY","CRY_SECONDS","EVENT_MIN_GAP","AUDIO_DEVICE",
          "SPEAKER_DEVICE","SSD_PROTO","SSD_MODEL","CAMERAS")))

# ---- server ---------------------------------------------------------------
import server.main as srv
c = uvicorn.Config(srv.app, host="127.0.0.1", port=8097, log_level="error")
threading.Thread(target=uvicorn.Server(c).run, daemon=True).start()
for _ in range(60):
    try: urllib.request.urlopen("http://127.0.0.1:8097/healthz", timeout=1); break
    except Exception: time.sleep(0.25)

jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
op.open("http://127.0.0.1:8097/login", data=b"password=pw").read()
ck = "cabinwatch=" + next(x for x in jar if x.name == "cabinwatch").value

# ---- run the real agent off that config ----------------------------------
os.makedirs(cfg.CLIPS_DIR, exist_ok=True)
sess = agent_mod.CameraSession("baby-room", cfg.CAMERAS["baby-room"], cfg)
sess.start()
time.sleep(4)

st = json.loads(op.open(urllib.request.Request(
    "http://127.0.0.1:8097/state", headers={"Cookie": ck})).read())
check("agent connected using the json config", len(st["cameras"]) == 1,
      str([x["id"] for x in st["cameras"]]))
if st["cameras"]:
    check("it reports itself live", st["cameras"][0]["status"] == "live")
    check("it is recording", st["cameras"][0]["recording"] is True)
    check("baby mode came from the json",
          st["cameras"][0]["baby_mode"] is True)

time.sleep(6)
on_disk = [f for f in os.listdir(os.path.join(cfg.CLIPS_DIR, "baby-room"))
           if f.endswith(".mp4")]
check("clips landed in the folder the json named", len(on_disk) >= 2,
      f"{len(on_disk)} files in {cfg.CLIPS_DIR}")

st = json.loads(op.open(urllib.request.Request(
    "http://127.0.0.1:8097/state", headers={"Cookie": ck})).read())
check("movement raised an event",
      any(e["kind"] in ("motion", "person") for e in st["events"]))

sess.stop(); time.sleep(0.5)
print(f"\n=== {len(PASS)} passed, {len(FAIL)} failed ===")
for f in FAIL: print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
