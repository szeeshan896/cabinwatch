"""
A new dashboard talking to a camera that has not been updated yet.

This is not a corner case, it is the normal state of things for a day or
two: the server redeploys the moment a change is pushed, and the phone in
the cabin and the laptop in the nursery get updated whenever somebody
gets round to walking over with a cable. In between, a dashboard that
knows about rotation, sirens and person detection is sending those
commands to agents that have never heard of them.

Nothing may fall over in either direction. An old camera has to ignore a
command from the future quietly and carry on watching the room; a new
camera reporting things an old server does not know about has to appear
on the dashboard all the same.
"""

import os
import sys
import json
import time
import shutil
import tempfile
import threading
import urllib.parse
import urllib.request
import http.cookiejar

WORK = tempfile.mkdtemp(prefix="cabinwatch-versions-")
os.environ.update(AGENT_TOKEN="demo", VIEW_PASSWORD="demo", SECRET_KEY="f",
                  PORT="8091", NTFY_TOPIC="",
                  EVENT_LOG=os.path.join(WORK, "events.json"),
                  SCHEDULE_FILE=os.path.join(WORK, "schedules.json"),
                  EVIDENCE_DIR=os.path.join(WORK, "evidence"))
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))

import uvicorn                                    # noqa: E402
import websocket                                  # noqa: E402

BASE = "http://127.0.0.1:8091"
WS = "ws://127.0.0.1:8091"

PASSED = []
FAILED = []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(("PASS  " if ok else "FAIL  ") + name
          + (("  -- " + detail) if detail else ""))
    return bool(ok)


class OldCamera:
    """A camera from before any of this existed.

    It reports only the handful of fields the first version had, and it
    answers nothing it does not recognise.
    """

    def __init__(self, cam_id="old", name="Old Camera"):
        query = urllib.parse.urlencode(
            {"token": "demo", "id": cam_id, "name": name, "kind": "agent"})
        self.ws = websocket.create_connection(f"{WS}/ws/camera?{query}",
                                              timeout=5)
        self.heard = []
        self.alive = True
        self.cam_id = cam_id
        threading.Thread(target=self._listen, daemon=True).start()
        threading.Thread(target=self._report, daemon=True).start()

    def _report(self):
        state = {"t": "state", "name": "Old Camera", "status": "live",
                 "recording": True, "motion": False, "armed": True,
                 "baby_mode": False}
        while self.alive:
            try:
                self.ws.send(json.dumps(state))
            except Exception:
                return
            time.sleep(0.4)

    def _listen(self):
        while self.alive:
            try:
                raw = self.ws.recv()
            except Exception:
                return
            if isinstance(raw, str):
                try:
                    self.heard.append(json.loads(raw))
                except Exception:
                    pass

    def close(self):
        self.alive = False
        try:
            self.ws.close()
        except Exception:
            pass


def test_an_old_agent_ignores_a_command_from_the_future():
    """The agent's own dispatch, fed everything it has never seen."""
    import agent as A

    class Bare(A.CameraSession):
        def __init__(self):
            self.cam_id = "bare"
            self.name = "Bare"
            self.rotate = 0
            self.zoom = 1.0
            self.alarm = False
            self._alarm_until = 0.0
            self.denoise = True
            self.armed = True
            self.baby_mode = False
            self.speaker_enabled = False

            class Person:
                enabled = False
            self.person = Person()

            class Motion:
                def reset(self):
                    pass
            self.motion = Motion()
            self.tamper = None

            class Rec:
                paused = False

                def mark_event(self, *a, **k):
                    pass
            self.rec = Rec()

        def push_state(self):
            pass

        def send(self, message):
            return True

    cam = Bare()
    nonsense = [
        {"t": "rotate", "value": "sideways"},
        {"t": "rotate"},
        {"t": "zoom", "value": None},
        {"t": "alarm", "on": "yes please"},
        {"t": "person_detection"},
        {"t": "a_command_from_a_later_version", "value": 1},
        {"t": ""},
        {},
    ]
    crashed = []
    for message in nonsense:
        try:
            cam._handle(message)
        except Exception as e:
            crashed.append(f"{message} -> {type(e).__name__}: {e}")

    ok = check("nothing it does not understand can bring it down",
               not crashed, "; ".join(crashed)[:120])
    ok &= check("and nonsense does not move the camera",
                cam.rotate == 0 and cam.zoom == 1.0,
                f"rotate {cam.rotate}, zoom {cam.zoom}")
    return ok


def test_a_new_dashboard_against_an_old_camera(opener):
    cam = OldCamera()
    time.sleep(1.0)

    state = json.loads(opener.open(BASE + "/state", timeout=10).read())
    card = next((c for c in state["cameras"] if c["id"] == "old"), None)
    ok = check("an old camera still appears on the dashboard", card is not None)
    if not card:
        cam.close()
        return False

    # Everything the page draws has to have a value, even from a camera
    # that has never heard of it.
    for field in ("rotate", "alarm", "noise_filter", "person_detection",
                  "has_siren", "paused", "battery", "location"):
        ok &= check(f"it has a sensible {field} without reporting one",
                    field in card, f"{field} missing")

    # And the new commands must be deliverable without an error.
    for what, body in (("rotate", {"value": 90}), ("alarm", {"on": True}),
                       ("person", {"on": True}), ("noise", {"on": False})):
        try:
            opener.open(BASE + f"/cmd/old/{what}",
                        data=json.dumps(body).encode(), timeout=10)
            sent = True
        except Exception as e:
            sent = False
        ok &= check(f"sending {what} to it does not fail", sent)

    time.sleep(0.6)
    ok &= check("the camera was sent them and simply carried on",
                len(cam.heard) >= 4, f"{len(cam.heard)} commands received")
    cam.close()
    return ok


def test_a_new_camera_against_fields_nobody_asked_for(opener):
    """A camera is allowed to be newer than the server too."""
    query = urllib.parse.urlencode(
        {"token": "demo", "id": "new", "name": "New Camera", "kind": "agent"})
    ws = websocket.create_connection(f"{WS}/ws/camera?{query}", timeout=5)
    ws.send(json.dumps({
        "t": "state", "name": "New Camera", "status": "live", "armed": True,
        "rotate": 180, "alarm": False,
        # things this server has never heard of
        "thermal_mode": True, "sensor_fusion": {"depth": 3},
        "firmware": "2.9", "frames_dropped": 17,
    }))
    time.sleep(0.8)

    state = json.loads(opener.open(BASE + "/state", timeout=10).read())
    card = next((c for c in state["cameras"] if c["id"] == "new"), None)
    ok = check("a camera reporting unknown things still appears",
               card is not None)
    if card:
        ok &= check("what it says that we do understand is kept",
                    card.get("rotate") == 180, str(card.get("rotate")))
        ok &= check("and what we do not is dropped rather than passed on",
                    "thermal_mode" not in card and "firmware" not in card,
                    str([k for k in card if k in ("thermal_mode",
                                                  "firmware")]))
    ws.close()
    return ok


def main():
    import server.main as srv
    config = uvicorn.Config(srv.app, host="127.0.0.1", port=8091,
                            log_level="error")
    threading.Thread(target=uvicorn.Server(config).run, daemon=True).start()
    for _ in range(80):
        try:
            urllib.request.urlopen(BASE + "/healthz", timeout=1)
            break
        except Exception:
            time.sleep(0.25)

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar))
    opener.open(BASE + "/login",
                data=urllib.parse.urlencode({"password": "demo"}).encode())

    try:
        test_an_old_agent_ignores_a_command_from_the_future()
        test_a_new_dashboard_against_an_old_camera(opener)
        test_a_new_camera_against_fields_nobody_asked_for(opener)
        print()
        print(f"mixed versions: {len(PASSED)} passed, {len(FAILED)} failed")
        return 0 if not FAILED else 1
    finally:
        shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
