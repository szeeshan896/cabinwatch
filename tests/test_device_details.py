"""Does the device's own condition reach the dashboard and the alerts?"""
import os
import tempfile as _tempfile, sys, json, time, threading, urllib.parse, urllib.request, http.cookiejar
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
os.environ.update(AGENT_TOKEN="demo", VIEW_PASSWORD="demo", SECRET_KEY="f",
                  PORT="8092", NTFY_TOPIC="")
# Keep this run's history and saved clips out of every other run's.
os.environ.setdefault("EVENT_LOG", _tempfile.mkdtemp(
    prefix="cabinwatch-state-") + "/events.json")
os.environ["EVIDENCE_DIR"] = os.path.dirname(os.environ["EVENT_LOG"]) + "/clips"
os.environ["SCHEDULE_FILE"] = os.path.dirname(os.environ["EVENT_LOG"]) + "/s.json"

sys.path.insert(0, _ROOT)
import cv2, numpy as np, uvicorn, websocket
from playwright.sync_api import sync_playwright
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import browser

PASS, FAIL = [], []
def check(n, ok, extra=""):
    (PASS if ok else FAIL).append(n)
    print(f"  {'PASS' if ok else 'FAIL'}  {n}" + (f"   {extra}" if extra else ""))

import server.main as srv
import server.notify as notify
cfg = uvicorn.Config(srv.app, host="127.0.0.1", port=8092, log_level="error")
threading.Thread(target=uvicorn.Server(cfg).run, daemon=True).start()
for _ in range(60):
    try: urllib.request.urlopen("http://127.0.0.1:8092/healthz", timeout=1); break
    except Exception: time.sleep(0.25)

# a camera that reports where it is, how charged it is, and that it was moved
STATE = {"t":"state","name":"Cabin Camera","status":"live","recording":True,
         "battery":12,"charging":False,"has_audio":True,"has_speaker":True,
         "location":{"lat":19.07598,"lon":72.87765,"accuracy_m":23.4,
                     "at":int(time.time())},
         "moved_ago_s":45,"quiet_for_s":3,"armed":True,"baby_mode":False}

def cam(w):
    w.send(json.dumps(STATE))
    w.send(json.dumps({"t":"event","kind":"moved",
                       "detail":"This camera has been moved or covered."}))
    w.send(json.dumps({"t":"event","kind":"battery_low",
                       "detail":"This camera is down to 12% and is not charging."}))
    def pump():
        while True:
            try: w.send(json.dumps(STATE))
            except Exception: return
            time.sleep(1)
    threading.Thread(target=pump, daemon=True).start()

ws = websocket.WebSocketApp(
    "ws://127.0.0.1:8092/ws/camera?token=demo&id=cabin&name="
    + urllib.parse.quote("Cabin Camera") + "&kind=android", on_open=cam)
threading.Thread(target=ws.run_forever, daemon=True).start()
time.sleep(2.5)

jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
op.open("http://127.0.0.1:8092/login", data=b"password=demo").read()
ck = "cabinwatch=" + next(c for c in jar if c.name=="cabinwatch").value
st = json.loads(op.open(urllib.request.Request(
    "http://127.0.0.1:8092/state", headers={"Cookie": ck})).read())
c = st["cameras"][0]

check("the server carries the location", c.get("location",{}).get("lat") == 19.07598,
      str(c.get("location")))
check("the server carries the battery level", c.get("battery") == 12)
check("the server carries how long ago it was moved", c.get("moved_ago_s") == 45)
kinds = [e["kind"] for e in st["events"]]
check("a 'camera was moved' alert is logged", "moved" in kinds, str(kinds))
check("a low battery alert is logged", "battery_low" in kinds)

check("both are treated as urgent enough to ignore quiet hours",
      {"moved","battery_low"} <= notify.ALWAYS_PUSH)
check("'camera was moved' is sent at the highest priority",
      notify.KINDS["moved"][1] == 5, str(notify.KINDS["moved"]))

with sync_playwright() as pw:
    b = browser.launch(pw)
    pg = b.new_context(viewport={"width":900,"height":1000}).new_page()
    pg.goto("http://127.0.0.1:8092/login")
    pg.fill("#pw","demo"); pg.click("button[type=submit]")
    pg.wait_for_selector(".cam", timeout=15000)
    pg.wait_for_timeout(2000)

    dev = pg.evaluate("() => document.querySelector('.device').textContent")
    check("the card shows the battery", "12%" in dev, dev.strip())
    check("the card warns that it was moved", "moved" in dev.lower(), dev.strip())
    href = pg.evaluate("() => { const a = document.querySelector('.device a'); return a ? a.href : ''; }")
    check("the card links to the location on a map",
          "19.07598" in href and "72.87765" in href, href)
    check("a low battery is called out, not just printed",
          pg.evaluate("() => !!document.querySelector('.device .bat.low')"))
    evs = pg.evaluate("() => document.getElementById('events').textContent")
    check("the activity list names the move in plain words",
          "Camera was moved" in evs)
    check("and the low battery", "Battery low" in evs)
    pg.screenshot(path=_os.path.join(_ROOT, "ui-preview") + "/dashboard-device.png",
                  full_page=True)
    b.close()

print(f"\n=== {len(PASS)} passed, {len(FAIL)} failed ===")
for f in FAIL: print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
