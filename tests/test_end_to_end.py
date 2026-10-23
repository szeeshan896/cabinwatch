"""End-to-end test: server + a simulated camera + a simulated viewer."""
import os, sys, json, time, struct, base64, threading, subprocess, urllib.request, http.cookiejar
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))

os.environ["AGENT_TOKEN"] = "test-token-123"
os.environ["VIEW_PASSWORD"] = "pw123"
os.environ["SECRET_KEY"] = "fixed-secret-for-test"
os.environ["PORT"] = "8099"
os.environ["SNAPSHOT_EVERY"] = "1"

sys.path.insert(0, _ROOT)
sys.path.insert(0, _os.path.join(_ROOT, "agent"))

import uvicorn, websocket

BASE = "http://127.0.0.1:8099"
WSB = "ws://127.0.0.1:8099"
PASS = []
FAIL = []

def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {extra}" if extra else ""))

# ---- boot server -----------------------------------------------------------
import server.main as srv
cfg = uvicorn.Config(srv.app, host="127.0.0.1", port=8099, log_level="error")
server = uvicorn.Server(cfg)
threading.Thread(target=server.run, daemon=True).start()
for _ in range(60):
    try:
        urllib.request.urlopen(BASE + "/healthz", timeout=1); break
    except Exception: time.sleep(0.25)
print("\n=== server up ===")

# ---- viewer logs in --------------------------------------------------------
jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
op.open(BASE + "/login", data=b"password=pw123").read()
cookie = next((c for c in jar if c.name == "cabinwatch"), None)
check("viewer login sets a signed cookie", cookie is not None)

badjar = http.cookiejar.CookieJar()
bad = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(badjar))
bad.open(BASE + "/login", data=b"password=wrong").read()
check("wrong password is rejected",
      not any(c.name == "cabinwatch" for c in badjar))

try:
    urllib.request.urlopen(BASE + "/state", timeout=3)
    check("/state blocks a request with no cookie", False)
except urllib.error.HTTPError as e:
    check("/state blocks a request with no cookie", e.code == 401)

ck = f"cabinwatch={cookie.value}"

# ---- camera with the wrong token is refused -------------------------------
refused = {}
try:
    bw = websocket.WebSocket()
    bw.connect(f"{WSB}/ws/camera?token=nope&id=x")
    refused["ok"] = "connected!"
except Exception as e:
    refused["status"] = getattr(e, "status_code", None) or type(e).__name__
check("camera with a bad token is refused at the handshake",
      refused.get("status") == 403, f"-> {refused}")

badid = {}
try:
    bw2 = websocket.WebSocket()
    bw2.connect(f"{WSB}/ws/camera?token=test-token-123&id=bad%2Fid")
    badid["ok"] = "connected!"
except Exception as e:
    badid["status"] = getattr(e, "status_code", None) or type(e).__name__
check("camera with an unusable id is refused", badid.get("status") == 403,
      f"-> {badid}")

# ---- real camera connects --------------------------------------------------
cam_msgs = []
cam_ws = None
ready = threading.Event()

def cam_open(w):
    w.send(json.dumps({"t": "state", "name": "Baby Room", "status": "live",
                       "baby_mode": True, "has_audio": True,
                       "has_speaker": True, "ptz": True,
                       "ptz_presets": ["Crib", "Door"],
                       "lenses": [{"id": "0", "label": "Back"},
                                  {"id": "1", "label": "Front"}],
                       "quiet_for_s": 1380, "battery": 88}))
    ready.set()

def cam_msg(w, m):
    cam_msgs.append(m if isinstance(m, str) else ("BIN", m[:1]))
    try: d = json.loads(m)
    except Exception: return
    if d.get("t") == "list_clips":
        w.send(json.dumps({"t": "clips", "items": [
            {"file": "2026-10-21_09-00-00.mp4", "size_mb": 4.2,
             "at": int(time.time()), "when": "21 Oct, 09:00:00", "keep": True}]}))
    elif d.get("t") == "get_clip":
        x = d["xfer"]
        w.send(json.dumps({"t": "clip_start", "xfer": x, "size": 9,
                           "type": "video/mp4"}))
        w.send(bytes([3]) + struct.pack(">I", x) + b"MOVIEDATA",
               opcode=websocket.ABNF.OPCODE_BINARY)
        w.send(json.dumps({"t": "clip_end", "xfer": x}))

cam_ws = websocket.WebSocketApp(
    f"{WSB}/ws/camera?token=test-token-123&id=baby&name=Baby%20Room&kind=agent",
    on_open=cam_open, on_message=cam_msg)
threading.Thread(target=cam_ws.run_forever, daemon=True).start()
ready.wait(5); time.sleep(0.8)

st = json.loads(op.open(urllib.request.Request(
    BASE + "/state", headers={"Cookie": ck})).read())
cams = st["cameras"]
check("camera appears in /state", len(cams) == 1 and cams[0]["id"] == "baby")
check("camera reports its own name", cams[0]["name"] == "Baby Room")
check("ptz + presets surfaced to the dashboard",
      cams[0]["ptz"] and cams[0]["ptz_presets"] == ["Crib", "Door"])
check("dual lenses surfaced to the dashboard", len(cams[0]["lenses"]) == 2)
check("still-for value surfaced", cams[0]["quiet_for_s"] == 1380)
check("battery surfaced", cams[0]["battery"] == 88)
check("camera starts with nobody watching", cams[0]["streaming"] is None)
check("an online event was logged",
      any(e["kind"] == "online" for e in st["events"]))

# ---- viewer attaches, camera is told to stream ----------------------------
frames = []
audio_in = []
vw = websocket.WebSocket()
vw.connect(f"{WSB}/ws/view?cam=baby&mode=live", header=[f"Cookie: {ck}"])

def viewer_reader():
    while True:
        try: d = vw.recv()
        except Exception: return
        if isinstance(d, bytes):
            if d[:1] == b"\x01": frames.append(d[1:])
            elif d[:1] == b"\x02": audio_in.append(d[1:])

threading.Thread(target=viewer_reader, daemon=True).start()
time.sleep(0.8)

stream_cmd = [json.loads(m) for m in cam_msgs
              if isinstance(m, str) and '"stream"' in m]
check("camera told to START streaming when watched",
      bool(stream_cmd) and stream_cmd[-1]["on"] is True,
      f"spec={stream_cmd[-1] if stream_cmd else None}")

# camera pushes a jpeg
cam_ws.send(bytes([1]) + b"\xff\xd8JPEGBYTES\xff\xd9",
            opcode=websocket.ABNF.OPCODE_BINARY)
time.sleep(0.6)
check("video frame reached the viewer", len(frames) == 1 and b"JPEGBYTES" in frames[0])

# ---- listen: audio only flows when asked ----------------------------------
cam_ws.send(bytes([2]) + b"\x01\x02\x03\x04", opcode=websocket.ABNF.OPCODE_BINARY)
time.sleep(0.5)
check("camera audio is NOT relayed before Listen", len(audio_in) == 0)

vw.send(json.dumps({"listen": True})); time.sleep(0.5)
audio_cmd = [json.loads(m) for m in cam_msgs
             if isinstance(m, str) and '"audio"' in m]
check("camera told to open its mic on Listen",
      bool(audio_cmd) and audio_cmd[-1]["on"] is True)
cam_ws.send(bytes([2]) + b"\x05\x06\x07\x08", opcode=websocket.ABNF.OPCODE_BINARY)
time.sleep(0.5)
check("camera audio reaches the viewer after Listen",
      len(audio_in) == 1 and audio_in[0] == b"\x05\x06\x07\x08")

# ---- talk: viewer audio reaches the camera --------------------------------
before = len([m for m in cam_msgs if m == ("BIN", b"\x02")])
vw.send_binary(bytes([2]) + b"\x10\x11\x12\x13"); time.sleep(0.6)
after = len([m for m in cam_msgs if m == ("BIN", b"\x02")])
check("talk-back audio reaches the camera", after == before + 1)

# ---- event with a thumbnail ------------------------------------------------
thumb = base64.b64encode(b"\xff\xd8THUMB\xff\xd9").decode()
cam_ws.send(json.dumps({"t": "event", "kind": "cry",
                        "detail": "Crying for over a second.",
                        "thumb": thumb, "clip": "x.mp4"}))
time.sleep(0.7)
st = json.loads(op.open(urllib.request.Request(
    BASE + "/state", headers={"Cookie": ck})).read())
cry = next((e for e in st["events"] if e["kind"] == "cry"), None)
check("cry event logged with a thumbnail url", cry is not None and cry["thumb"])
got = urllib.request.urlopen(BASE + cry["thumb"]).read()
check("thumbnail is fetchable for the notification", got == b"\xff\xd8THUMB\xff\xd9")

# ---- commands --------------------------------------------------------------
def post(path, body):
    r = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                               headers={"Cookie": ck,
                                        "Content-Type": "application/json"},
                               method="POST")
    return op.open(r).getcode()

check("record command accepted", post("/cmd/baby/record", {"on": True}) == 200)
check("ptz command accepted",
      post("/cmd/baby/ptz", {"action": "left", "speed": 0.6}) == 200)
check("lens switch accepted", post("/cmd/baby/lens", {"id": "1"}) == 200)
time.sleep(0.5)
sent = [json.loads(m) for m in cam_msgs if isinstance(m, str)]
check("camera received the ptz move",
      any(d.get("t") == "ptz" and d.get("action") == "left" for d in sent))
check("camera received the lens switch",
      any(d.get("t") == "lens" and d.get("id") == "1" for d in sent))
try:
    post("/cmd/baby/evil", {}); check("unknown command rejected", False)
except urllib.error.HTTPError as e:
    check("unknown command rejected", e.code == 400)

# ---- clip listing and download off the device -----------------------------
lst = json.loads(op.open(urllib.request.Request(
    BASE + "/clips/baby", headers={"Cookie": ck})).read())
check("clip list came from the device",
      len(lst["clips"]) == 1 and lst["clips"][0]["keep"] is True)

body = op.open(urllib.request.Request(
    BASE + "/clip/baby/2026-10-21_09-00-00.mp4",
    headers={"Cookie": ck})).read()
check("clip streamed off the device through the server", body == b"MOVIEDATA",
      f"got {body!r}")

try:
    op.open(urllib.request.Request(BASE + "/clip/baby/..%2Fetc%2Fpasswd",
                                   headers={"Cookie": ck}))
    check("path traversal in a clip name is refused", False)
except urllib.error.HTTPError as e:
    check("path traversal in a clip name is refused", e.code in (400, 404))

# ---- viewer leaves, camera is told to stop --------------------------------
vw.close(); time.sleep(1.0)
stream_cmd = [json.loads(m) for m in cam_msgs
              if isinstance(m, str) and '"stream"' in m]
check("camera told to STOP streaming when nobody watches",
      stream_cmd[-1]["on"] is False)

# ---- disconnect ------------------------------------------------------------
cam_ws.close(); time.sleep(1.2)
st = json.loads(op.open(urllib.request.Request(
    BASE + "/state", headers={"Cookie": ck})).read())
# It stays on the list, marked offline and stamped with when it stopped -
# a camera that simply vanishes leaves you unable to answer the one question
# you have, which is when it went.
gone = st["cameras"][0] if st["cameras"] else {}
check("camera stays listed after it disconnects", len(st["cameras"]) == 1)
check("and is marked offline", gone.get("online") is False)
check("with the time it was last heard from",
      isinstance(gone.get("last_seen"), (int, float)))
check("offline event logged",
      any(e["kind"] == "offline" for e in st["events"]))

print(f"\n=== {len(PASS)} passed, {len(FAIL)} failed ===")
if FAIL:
    for f in FAIL: print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
