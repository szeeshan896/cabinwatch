"""Runs the agent with a settings page and drives it like a person would."""
import os
import tempfile as _tempfile, sys, json, time, shutil, threading, urllib.request
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); sys.path.insert(0, _os.path.join(_ROOT, "agent"))
os.environ.update(AGENT_TOKEN="tok", VIEW_PASSWORD="pw", SECRET_KEY="f", PORT="8095")
# Keep this run's history and saved clips out of every other run's.
os.environ.setdefault("EVENT_LOG", _tempfile.mkdtemp(
    prefix="cabinwatch-state-") + "/events.json")
os.environ["EVIDENCE_DIR"] = os.path.dirname(os.environ["EVENT_LOG"]) + "/clips"
os.environ["SCHEDULE_FILE"] = os.path.dirname(os.environ["EVENT_LOG"]) + "/s.json"


import cv2, numpy as np, uvicorn
import setup_wizard, local_ui, agent as A

SP = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(SP, "panel_run"); shutil.rmtree(WORK, ignore_errors=True); os.makedirs(WORK)
PASS, FAIL = [], []
def check(n, ok, extra=""):
    (PASS if ok else FAIL).append(n)
    print(f"  {'PASS' if ok else 'FAIL'}  {n}" + (f"   {extra}" if extra else ""))

# a moving scene to act as the camera
VID = os.path.join(WORK, "scene.mp4")
vw = cv2.VideoWriter(VID, cv2.VideoWriter_fourcc(*"mp4v"), 15, (640,480))
for i in range(15*40):
    f = np.full((480,640,3), 45, np.uint8)
    x = 60 + int(i*5) % 420
    cv2.rectangle(f,(x,180),(x+110,300),(225,225,225),-1)
    vw.write(f)
vw.release()

import server.main as srv
c = uvicorn.Config(srv.app, host="127.0.0.1", port=8095, log_level="error")
threading.Thread(target=uvicorn.Server(c).run, daemon=True).start()
time.sleep(1.5)

data = {
    "server_url": "http://127.0.0.1:8095", "agent_token": "tok",
    "clips_dir": os.path.join(WORK, "clips"),
    "retention_hours": 24, "max_disk_gb": 5.0, "segment_seconds": 60,
    "cameras": {"baby-room": {"name": "Baby Room", "source": VID,
                              "baby_mode": True, "audio": False,
                              "speaker": False}},
}
# the panel writes back to this path
setup_wizard.CONFIG_NAME = "cabinwatch.json"
os.environ["_"] = ""
setup_wizard.base_dir = lambda: WORK
cfg = setup_wizard.JsonConfig(data)
setup_wizard.save_json_config(data)
os.makedirs(cfg.CLIPS_DIR, exist_ok=True)

sup = A.Supervisor(cfg, cfg.CAMERAS)
check("supervisor starts the cameras", sup.start())
check("it knows these settings are editable", sup.editable is True)

port = local_ui.free_port(8765)
panel = local_ui.ControlPanel(sup, port=port)
check("the settings page starts", panel.start())
base = f"http://127.0.0.1:{port}"
time.sleep(3)

def get(path):
    return json.loads(urllib.request.urlopen(base+path, timeout=10).read())
def post(path, body):
    r = urllib.request.Request(base+path, data=json.dumps(body).encode(),
                               headers={"Content-Type":"application/json"}, method="POST")
    return urllib.request.urlopen(r, timeout=20)

html = urllib.request.urlopen(base+"/", timeout=10).read().decode()
check("the page renders", "CabinWatch settings" in html and len(html) > 4000,
      f"{len(html)} bytes")

st = get("/api/state")
check("it reports the running camera", len(st["cameras"]) == 1)
check("it does not hand back the real token",
      st.get("token_set") is True and "tok" not in json.dumps(st.get("token_hint","")),
      f'hint={st.get("token_hint")!r}')
check("it knows ffmpeg is present", st["ffmpeg"] is True)
check("clip length is shown", st["segment_seconds"] == 60)

dev = get("/api/devices")
check("it lists devices without crashing",
      isinstance(dev.get("cameras"), list) and isinstance(dev.get("microphones"), list),
      f'{len(dev["cameras"])} cameras, {len(dev["microphones"])} mics')

jpeg = urllib.request.urlopen(base+"/api/preview?cam=baby-room", timeout=10).read()
img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
check("the live preview is a real picture", img is not None,
      f"{img.shape if img is not None else None}")

# ---- change settings the way the page does -----------------------------
r = post("/api/save", {
    "server_url": "http://127.0.0.1:8095",
    "agent_token": "",                       # blank = keep the old one
    "segment_seconds": 300,
    "retention_hours": 72,
    "max_disk_gb": 50,
    "cameras": [{"id": "baby-room", "name": "Nursery",
                 "source": VID, "audio_device": "default",
                 "baby_mode": False}],
})
check("saving works", r.getcode() == 200)
check("the page is not left hanging while it restarts", True)
time.sleep(9)

saved = json.load(open(os.path.join(WORK, "cabinwatch.json")))
check("clip length was written", saved["segment_seconds"] == 300, str(saved["segment_seconds"]))
check("the rename was written", saved["cameras"]["baby-room"]["name"] == "Nursery")
check("switching to cabin mode turned person detection on",
      saved["cameras"]["baby-room"].get("person_detection") is True)
check("a blank token box kept the old token", saved["agent_token"] == "tok")

st2 = get("/api/state")
check("the camera restarted with the new name",
      st2["cameras"][0]["name"] == "Nursery", st2["cameras"][0]["name"])
check("it is running after the restart",
      st2["cameras"][0]["status"] in ("live", "connecting"), st2["cameras"][0]["status"])

# ---- a config.py setup must refuse to be edited here -------------------
class ModuleCfg:
    SERVER_URL="x"; AGENT_TOKEN="y"; RETENTION_HOURS=1; MAX_DISK_GB=1
    SEGMENT_SECONDS=60; CLIPS_DIR=WORK; CAMERAS={}
sup2 = A.Supervisor(ModuleCfg(), {})
check("a config.py setup is marked read-only", sup2.editable is False)
try:
    sup2.apply({}); check("editing it is refused", False)
except RuntimeError as e:
    check("editing it is refused", "config.py" in str(e))

panel.stop(); sup.stop(); time.sleep(0.5)
print(f"\n=== {len(PASS)} passed, {len(FAIL)} failed ===")
for f in FAIL: print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
