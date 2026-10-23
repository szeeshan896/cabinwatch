"""
A phone left watching a room is flat by morning.

He left one on overnight without watching it, and found it switched off
at zero in the morning. Nothing was wrong: watching a room is what empties
a phone. The camera, the encoder and the microphone run without a pause
whether or not anybody is looking, and a spare phone has one night in it.

A flat phone is worth less than no camera at all - it cannot be woken, it
cannot say where it is, it is simply gone. So: standby, where it holds the
connection and nothing else, wakes when somebody asks to look, and goes
back when they stop. And because the mistake is forgetting the charger,
the server does it on its own before the battery is gone, after warning
while there is still time to walk over with a cable.

The trade is real and these checks hold it to it: in standby there is no
recording and no movement detection, so nothing may put a camera there
quietly.
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

WORK = tempfile.mkdtemp(prefix="cabinwatch-standby-")
os.environ.update(AGENT_TOKEN="demo", VIEW_PASSWORD="demo", SECRET_KEY="f",
                  PORT="8090", NTFY_TOPIC="",
                  EVENT_LOG=os.path.join(WORK, "events.json"),
                  SCHEDULE_FILE=os.path.join(WORK, "schedules.json"),
                  EVIDENCE_DIR=os.path.join(WORK, "evidence"))
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))

import uvicorn                                    # noqa: E402
import websocket                                  # noqa: E402

BASE = "http://127.0.0.1:8090"
WS = "ws://127.0.0.1:8090"

PASSED = []
FAILED = []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(("PASS  " if ok else "FAIL  ") + name
          + (("  -- " + detail) if detail else ""))
    return bool(ok)


class Phone:
    def __init__(self, cam_id="spare", name="Spare Phone",
                 battery=90, charging=True):
        self.state = {"t": "state", "name": name, "status": "live",
                      "armed": True, "battery": battery,
                      "charging": charging, "standby": False,
                      "asleep": False, "has_audio": True}
        self.heard = []
        query = urllib.parse.urlencode(
            {"token": "demo", "id": cam_id, "name": name, "kind": "android"})
        self.ws = websocket.create_connection(f"{WS}/ws/camera?{query}",
                                              timeout=5)
        self.alive = True
        threading.Thread(target=self._listen, daemon=True).start()

    def _listen(self):
        while self.alive:
            try:
                raw = self.ws.recv()
            except Exception:
                return
            if not isinstance(raw, str):
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            self.heard.append(msg)
            # Behave like the real thing: obey what it is told.
            if msg.get("t") == "standby":
                self.state["standby"] = bool(msg.get("on"))
                self._recompute()
            elif msg.get("t") == "stream":
                self.state["_watched"] = bool(msg.get("on"))
                self._recompute()
            elif msg.get("t") == "audio":
                self.state["_listened"] = bool(msg.get("on"))
                self._recompute()

    def _recompute(self):
        watching = self.state.get("_watched") or self.state.get("_listened")
        self.state["asleep"] = bool(self.state["standby"] and not watching)
        self.report()

    def report(self, **changes):
        self.state.update(changes)
        payload = {k: v for k, v in self.state.items()
                   if not k.startswith("_")}
        try:
            self.ws.send(json.dumps(payload))
        except Exception:
            pass

    def told(self, kind):
        return [m for m in self.heard if m.get("t") == kind]

    def close(self):
        self.alive = False
        try:
            self.ws.close()
        except Exception:
            pass


def card(opener, cam_id):
    state = json.loads(opener.open(BASE + "/state", timeout=10).read())
    return next((c for c in state["cameras"] if c["id"] == cam_id), {})


def events(hub, cam_id, kind):
    return [e for e in hub.events
            if e.get("camera") == cam_id and e.get("kind") == kind]


def test_a_sleeping_camera_wakes_when_you_look(opener, hub):
    phone = Phone("spare")
    time.sleep(0.6)

    opener.open(BASE + "/cmd/spare/standby",
                data=json.dumps({"on": True}).encode(), timeout=10)
    time.sleep(0.8)
    ok = check("it goes to standby when told",
               card(opener, "spare").get("asleep") is True,
               str(card(opener, "spare").get("asleep")))

    # Watching it is what wakes it, through the ordinary live view.
    viewer = websocket.create_connection(
        f"{WS}/ws/view?cam=spare&mode=live", timeout=5,
        cookie="; ".join(f"{c.name}={c.value}" for c in opener.jar))
    time.sleep(1.0)
    ok &= check("opening the live view wakes it",
                card(opener, "spare").get("asleep") is False,
                str(card(opener, "spare").get("asleep")))
    ok &= check("and the camera was actually asked for a picture",
                any(m.get("on") for m in phone.told("stream")),
                str(phone.told("stream")))

    viewer.close()
    time.sleep(1.2)
    ok &= check("closing it puts the camera back to sleep",
                card(opener, "spare").get("asleep") is True,
                str(card(opener, "spare").get("asleep")))
    ok &= check("and it stayed in standby throughout",
                card(opener, "spare").get("standby") is True)
    phone.close()
    return ok


def test_a_phone_on_battery_is_warned_then_put_to_sleep(opener, hub):
    phone = Phone("night", "Night Phone", battery=80, charging=True)
    time.sleep(0.5)

    phone.report(charging=False, battery=80)
    time.sleep(0.5)
    ok = check("nothing happens while there is plenty left",
               not events(hub, "night", "battery_low")
               and not events(hub, "night", "standby"))

    phone.report(battery=50)
    time.sleep(0.6)
    warned = events(hub, "night", "battery_low")
    ok &= check("it warns while there is still time to plug it in",
                len(warned) == 1,
                warned[0]["detail"][:70] if warned else "nothing")

    phone.report(battery=45)
    time.sleep(0.5)
    ok &= check("and does not keep saying it",
                len(events(hub, "night", "battery_low")) == 1)

    phone.report(battery=25)
    time.sleep(0.8)
    slept = events(hub, "night", "standby")
    ok &= check("before it dies, it is put to standby", len(slept) == 1,
                slept[0]["detail"][:80] if slept else "nothing")
    ok &= check("the phone was actually told, not just the log",
                any(m.get("on") for m in phone.told("standby")),
                str(phone.told("standby")))
    ok &= check("and what it costs is said plainly",
                slept and "no longer recording" in slept[0]["detail"])

    phone.report(battery=20)
    time.sleep(0.5)
    ok &= check("it is not told twice",
                len(events(hub, "night", "standby")) == 1)
    phone.close()
    return ok


def test_a_charging_camera_is_left_alone(opener, hub):
    """On a charger there is no reason to stop watching the room."""
    phone = Phone("plugged", "Plugged In", battery=11, charging=True)
    time.sleep(0.8)
    ok = check("a camera on charge is never put to sleep",
               not events(hub, "plugged", "standby"))
    ok &= check("and not warned about its battery either",
                not events(hub, "plugged", "battery_low"))
    phone.close()
    return ok


def test_the_agent_knows_when_to_shut_its_eyes():
    """The same decision on the laptop side, where it can be read directly."""
    import agent as A

    class Bare(A.CameraSession):
        def __init__(self):
            self.cam_id = "x"
            self.standby = False
            self.stream_spec = None
            self.stream_lock = threading.Lock()
            self.send_audio = False

    cam = Bare()
    ok = check("awake when standby is off", cam.asleep() is False)
    cam.standby = True
    ok &= check("asleep when standby is on and nobody is watching",
                cam.asleep() is True)
    cam.stream_spec = {"width": 480}
    ok &= check("awake while somebody watches", cam.asleep() is False)
    cam.stream_spec = None
    cam.send_audio = True
    ok &= check("awake while somebody listens", cam.asleep() is False)
    cam.send_audio = False
    ok &= check("and back to sleep when they stop", cam.asleep() is True)
    return ok


def main():
    import server.main as srv
    config = uvicorn.Config(srv.app, host="127.0.0.1", port=8090,
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
    opener.jar = jar

    try:
        test_the_agent_knows_when_to_shut_its_eyes()
        test_a_sleeping_camera_wakes_when_you_look(opener, srv.hub)
        test_a_phone_on_battery_is_warned_then_put_to_sleep(opener, srv.hub)
        test_a_charging_camera_is_left_alone(opener, srv.hub)
        print()
        print(f"standby: {len(PASSED)} passed, {len(FAILED)} failed")
        return 0 if not FAILED else 1
    finally:
        shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
