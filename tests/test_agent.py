"""Runs the REAL agent against the real server, using a synthetic video."""
import os
import tempfile as _tempfile, sys, json, time, shutil, threading, urllib.request, http.cookiejar
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))

os.environ.update(AGENT_TOKEN="tok-abc", VIEW_PASSWORD="pw",
                  SECRET_KEY="fixed", PORT="8098")
# Keep this run's history and saved clips out of every other run's.
os.environ.setdefault("EVENT_LOG", _tempfile.mkdtemp(
    prefix="cabinwatch-state-") + "/events.json")
os.environ["EVIDENCE_DIR"] = os.path.dirname(os.environ["EVENT_LOG"]) + "/clips"
os.environ["SCHEDULE_FILE"] = os.path.dirname(os.environ["EVENT_LOG"]) + "/s.json"

sys.path.insert(0, _ROOT)
sys.path.insert(0, _os.path.join(_ROOT, "agent"))

import cv2, numpy as np, uvicorn, websocket

SP = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(SP, "agent_run")
shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK)

PASS, FAIL = [], []
def check(n, ok, extra=""):
    (PASS if ok else FAIL).append(n)
    print(f"  {'PASS' if ok else 'FAIL'}  {n}" + (f"   {extra}" if extra else ""))

# ---- a moving test video so motion detection has something to find -------
VID = os.path.join(WORK, "scene.mp4")
w, h, fps = 640, 480, 15
vw = cv2.VideoWriter(VID, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
for i in range(fps * 25):
    f = np.full((h, w, 3), 40, dtype=np.uint8)
    # a bright block that sits still, then starts moving at second 3
    x = 60 if i < fps * 3 else 60 + int((i - fps * 3) * 6) % 400
    cv2.rectangle(f, (x, 180), (x + 110, 300), (230, 230, 230), -1)
    vw.write(f)
vw.release()
check("test video written", os.path.getsize(VID) > 10000)

# ---- boot the server -----------------------------------------------------
import server.main as srv
cfg = uvicorn.Config(srv.app, host="127.0.0.1", port=8098, log_level="error")
threading.Thread(target=uvicorn.Server(cfg).run, daemon=True).start()
for _ in range(60):
    try: urllib.request.urlopen("http://127.0.0.1:8098/healthz", timeout=1); break
    except Exception: time.sleep(0.25)

jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
op.open("http://127.0.0.1:8098/login", data=b"password=pw").read()
ck = "cabinwatch=" + next(c for c in jar if c.name == "cabinwatch").value

# every page must render
for path in ("/", "/camera", "/login"):
    r = op.open(urllib.request.Request("http://127.0.0.1:8098" + path,
                                       headers={"Cookie": ck}))
    body = r.read().decode()
    check(f"page {path} renders", r.getcode() == 200 and len(body) > 1500,
          f"{len(body)} bytes")
check("dashboard has no unrendered template tags",
      "{{" not in op.open(urllib.request.Request(
          "http://127.0.0.1:8098/", headers={"Cookie": ck})).read().decode())
check("camera page has no unrendered template tags",
      "{{" not in op.open(urllib.request.Request(
          "http://127.0.0.1:8098/camera", headers={"Cookie": ck})).read().decode())

# ---- write a config and start the real agent ----------------------------
import agent as agent_mod

class Cfg:
    SERVER_URL = "http://127.0.0.1:8098"
    AGENT_TOKEN = "tok-abc"
    VERIFY_TLS = True
    CLIPS_DIR = os.path.join(WORK, "clips")
    SEGMENT_SECONDS = 4           # short so the test sees files roll
    RETENTION_HOURS = 72
    EVENT_RETENTION_HOURS = 336
    MAX_DISK_GB = 1.0
    ALWAYS_RECORD = True
    PREBUFFER_SECONDS = 3
    CAPTURE_FPS = 15.0
    MOTION_MIN_AREA = 1200
    MOTION_THRESHOLD = 25
    SOUND_SENSITIVITY = 1.0
    CRY_SECONDS = 1.2
    EVENT_MIN_GAP = 5.0
    AUDIO_DEVICE = None
    SPEAKER_DEVICE = None
    SSD_PROTO = SSD_MODEL = None
    CAMERAS = {}

conf = {"name": "Baby Room", "source": VID, "baby_mode": True,
        "audio": False, "speaker": False, "fps": 15.0}
sess = agent_mod.CameraSession("baby", conf, Cfg)
sess.start()
time.sleep(4)

st = json.loads(op.open(urllib.request.Request(
    "http://127.0.0.1:8098/state", headers={"Cookie": ck})).read())
cams = st["cameras"]
check("real agent registered with the server", len(cams) == 1, str([c["id"] for c in cams]))
if cams:
    check("agent reports itself live", cams[0]["status"] == "live",
          cams[0]["status"])
    check("agent is recording to its own disk", cams[0]["recording"] is True)
    check("agent reports free disk", cams[0]["disk_free_gb"] is not None)

# ---- viewer watches: frames must actually arrive -------------------------
frames = []
vw2 = websocket.WebSocket()
vw2.connect("ws://127.0.0.1:8098/ws/view?cam=baby&mode=live",
            header=[f"Cookie: {ck}"])
def rd():
    while True:
        try: d = vw2.recv()
        except Exception: return
        if isinstance(d, bytes) and d[:1] == b"\x01": frames.append(d[1:])
threading.Thread(target=rd, daemon=True).start()
time.sleep(3.5)
check("live frames arrive from the real agent", len(frames) >= 4,
      f"{len(frames)} frames")
if frames:
    arr = cv2.imdecode(np.frombuffer(frames[-1], np.uint8), cv2.IMREAD_COLOR)
    check("frames decode as real JPEG images", arr is not None,
          f"{arr.shape if arr is not None else None}")
    check("frame was scaled down to the live width",
          arr is not None and arr.shape[1] <= 480, f"width {arr.shape[1]}")

# ---- motion should have fired an event ----------------------------------
time.sleep(2)
st = json.loads(op.open(urllib.request.Request(
    "http://127.0.0.1:8098/state", headers={"Cookie": ck})).read())
mot = [e for e in st["events"] if e["kind"] in ("motion", "person")]
check("movement in the video raised an event", len(mot) >= 1,
      f"{len(mot)} events")
if mot:
    check("event carries a thumbnail", bool(mot[0]["thumb"]))
    img = op.open("http://127.0.0.1:8098" + mot[0]["thumb"]).read()
    dec = cv2.imdecode(np.frombuffer(img, np.uint8), cv2.IMREAD_COLOR)
    check("thumbnail decodes as an image", dec is not None,
          f"{dec.shape if dec is not None else None}")

# ---- clips really exist on the agent's disk -----------------------------
time.sleep(5)
cdir = os.path.join(Cfg.CLIPS_DIR, "baby")
on_disk = sorted(f for f in os.listdir(cdir) if f.endswith(".mp4"))
check("clips were written to the agent's own disk", len(on_disk) >= 2,
      f"{len(on_disk)} files: {on_disk[:3]}")

lst = json.loads(op.open(urllib.request.Request(
    "http://127.0.0.1:8098/clips/baby", headers={"Cookie": ck})).read())
check("dashboard can list the clips on the device",
      len(lst["clips"]) >= 1, f"{len(lst['clips'])} listed")
check("an event clip is marked to be kept longer",
      any(c["keep"] for c in lst["clips"]),
      str([(c["file"], c["keep"]) for c in lst["clips"]][:4]))

if lst["clips"]:
    name = lst["clips"][0]["file"]
    got = op.open(urllib.request.Request(
        f"http://127.0.0.1:8098/clip/baby/{name}",
        headers={"Cookie": ck})).read()
    disk = open(os.path.join(cdir, name), "rb").read()
    check("clip pulled off the device matches the file byte for byte",
          got == disk, f"{len(got)} vs {len(disk)} bytes")
    tmp = os.path.join(WORK, "roundtrip.mp4")
    open(tmp, "wb").write(got)
    cap = cv2.VideoCapture(tmp)
    ok, fr = cap.read(); cap.release()
    check("downloaded clip is a playable video", ok and fr is not None)

# ---- viewer leaves: the agent must stop encoding ------------------------
vw2.close(); time.sleep(1.5)
check("agent stopped streaming once nobody watched",
      sess.stream_spec is None)

sess.stop(); time.sleep(0.6)
print(f"\n=== {len(PASS)} passed, {len(FAIL)} failed ===")
for f in FAIL: print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
