"""Does the dashboard stop destroying the picture and the lens dropdown?"""
import os
import tempfile as _tempfile, sys, json, time, threading, urllib.parse, urllib.request
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
os.environ.update(AGENT_TOKEN="demo", VIEW_PASSWORD="demo", SECRET_KEY="f",
                  PORT="8093", NTFY_TOPIC="")
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

def frame(i):
    img = np.full((360, 640, 3), 40, np.uint8)
    cv2.rectangle(img, (60 + i*7 % 400, 120), (160 + i*7 % 400, 240),
                  (220, 220, 220), -1)
    return cv2.imencode(".jpg", img)[1].tobytes()

import server.main as srv
cfg = uvicorn.Config(srv.app, host="127.0.0.1", port=8093, log_level="error")
threading.Thread(target=uvicorn.Server(cfg).run, daemon=True).start()
for _ in range(60):
    try: urllib.request.urlopen("http://127.0.0.1:8093/healthz", timeout=1); break
    except Exception: time.sleep(0.25)

STATE = {"t":"state","name":"Testing Room","status":"live","recording":True,
         "baby_mode":True,"armed":True,"has_audio":True,"has_speaker":True,
         "battery":76,"clips":4,"disk_free_gb":3.7,"retention_hours":24,
         "quiet_for_s":2,"silent_for_s":0,"audio_level":0.02,
         "lenses":[{"id":"0","label":"Back camera 1"},
                   {"id":"1","label":"Front camera"},
                   {"id":"3","label":"Back camera 3"}],
         "active_lens":"3","zoom":1.0,"ptz":False}

def cam(w):
    w.send(json.dumps(STATE))
    def pump():
        i = 0
        while True:
            i += 1
            try:
                # a state push every second, exactly what caused the churn
                st = dict(STATE); st["quiet_for_s"] = i
                st["audio_level"] = 0.02 + (i % 5) / 100
                st["motion"] = (i % 4 == 0)
                w.send(json.dumps(st))
                w.send(bytes([1]) + frame(i), opcode=websocket.ABNF.OPCODE_BINARY)
            except Exception:
                return
            time.sleep(0.5)
    threading.Thread(target=pump, daemon=True).start()

ws = websocket.WebSocketApp(
    "ws://127.0.0.1:8093/ws/camera?token=demo&id=testing-room"
    "&name=" + urllib.parse.quote("Testing Room") + "&kind=android", on_open=cam)
threading.Thread(target=ws.run_forever, daemon=True).start()
time.sleep(2)

with sync_playwright() as pw:
    b = browser.launch(pw)
    pg = b.new_context(viewport={"width":900,"height":1000}).new_page()
    pg.goto("http://127.0.0.1:8093/login")
    pg.fill("#pw", "demo"); pg.click("button[type=submit]")
    pg.wait_for_selector(".cam", timeout=15000)
    pg.evaluate("() => setMode('testing-room','live')")
    pg.wait_for_timeout(2500)

    # tag the elements, then let several status updates go by
    pg.evaluate("""() => {
      window.__img = document.getElementById('img-testing-room');
      window.__img.dataset.tag = 'original';
      window.__sel = document.querySelector('[data-act=lens]');
      window.__sel.dataset.tag = 'original';
      window.__card = document.querySelector('.cam');
      window.__card.dataset.tag = 'original';
    }""")
    before = pg.evaluate("() => window.__img.src")
    pg.wait_for_timeout(6000)

    check("the picture element survives status updates",
          pg.evaluate("() => document.getElementById('img-testing-room').dataset.tag") == "original")
    check("the lens dropdown survives status updates",
          pg.evaluate("() => document.querySelector('[data-act=lens]').dataset.tag") == "original")
    check("the whole card is not rebuilt",
          pg.evaluate("() => document.querySelector('.cam').dataset.tag") == "original")
    check("frames are still arriving",
          pg.evaluate("() => window.__img.src") != before)

    # the lens the camera reports should be preselected
    check("the dropdown shows the lens actually in use",
          pg.evaluate("() => document.querySelector('[data-act=lens]').value") == "3",
          pg.evaluate("() => document.querySelector('[data-act=lens]').value"))

    # choosing a different lens must stick, not be reset by the next tick
    sent = []
    pg.expose_function("__spy", lambda u: sent.append(u))
    pg.evaluate("""() => {
      const orig = window.fetch;
      window.fetch = (u, o) => { window.__spy(String(u)); return orig(u, o); };
    }""")
    pg.select_option("[data-act=lens]", "1")
    pg.wait_for_timeout(3000)
    check("choosing a lens sends the command",
          any("/cmd/testing-room/lens" in u for u in sent), str(sent))
    check("the choice is not wiped by the next update",
          pg.evaluate("() => document.querySelector('[data-act=lens]').value") == "1",
          pg.evaluate("() => document.querySelector('[data-act=lens]').value"))

    # a phone running the app should not be labelled a laptop
    # once the camera really reports the new lens, the hold must let go
    STATE["active_lens"] = "1"
    pg.wait_for_timeout(2500)
    check("the hold releases when the camera confirms",
          pg.evaluate("() => document.querySelector('[data-act=lens]').value") == "1")
    check("and nothing is left pending",
          pg.evaluate("() => Object.keys((pending['testing-room']||{})).length") == 0)

    stats = pg.evaluate("() => document.querySelector('.stats').textContent")
    check("an app camera is labelled as a phone", "phone (app)" in stats, stats)

    pg.screenshot(path=_os.path.join(_ROOT, "ui-preview") + "/dashboard-steady.png",
                  full_page=True)
    b.close()

print(f"\n=== {len(PASS)} passed, {len(FAIL)} failed ===")
for f in FAIL: print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
