"""
The two things that happen first when somebody takes a camera.

They unplug it, and they do it long before the battery gets low enough for
the usual warning - which would arrive hours later, from a phone that is no
longer in the building. So losing power is its own alert, at the priority of
something happening rather than something to look at later.

And the siren: a phone that starts screaming with its torch going is a
deterrent, and it is also the only way to find out whether anyone is there.
It has to stop by itself, because a siren nobody turns off flattens the
battery and takes the camera with it.
"""

import os
import sys
import json
import time
import threading
import urllib.parse
import urllib.request
import http.cookiejar

os.environ.update(AGENT_TOKEN="demo", VIEW_PASSWORD="demo", SECRET_KEY="f",
                  PORT="8095", NTFY_TOPIC="")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uvicorn                                    # noqa: E402
import websocket                                  # noqa: E402

BASE = "http://127.0.0.1:8095"
WS = "ws://127.0.0.1:8095"

PASSED = []
FAILED = []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(("PASS  " if ok else "FAIL  ") + name
          + (("  -- " + detail) if detail else ""))
    return bool(ok)


def start_server():
    import server.main as srv
    config = uvicorn.Config(srv.app, host="127.0.0.1", port=8095,
                            log_level="error")
    threading.Thread(target=uvicorn.Server(config).run, daemon=True).start()
    for _ in range(80):
        try:
            urllib.request.urlopen(BASE + "/healthz", timeout=1)
            return srv
        except Exception:
            time.sleep(0.25)
    raise SystemExit("the server never came up")


def login():
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar))
    opener.open(BASE + "/login",
                data=urllib.parse.urlencode({"password": "demo"}).encode())
    return opener


class Phone:
    """A camera that reports its power, and listens for commands."""

    def __init__(self, cam_id, name, charging=True, battery=90):
        self.state = {"t": "state", "name": name, "status": "live",
                      "armed": True, "charging": charging, "battery": battery,
                      "has_siren": True}
        self.heard = []
        query = urllib.parse.urlencode(
            {"token": "demo", "id": cam_id, "name": name, "kind": "browser"})
        self.ws = websocket.create_connection(f"{WS}/ws/camera?{query}",
                                              timeout=5)
        self.alive = True
        threading.Thread(target=self._listen, daemon=True).start()

    def report(self, times=1, gap=0.4, **changes):
        self.state.update(changes)
        for _ in range(times):
            self.ws.send(json.dumps(self.state))
            time.sleep(gap)

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


def events_of(hub, cam_id, kind):
    return [e for e in hub.events
            if e.get("camera") == cam_id and e.get("kind") == kind]


def test_pulling_the_charger_raises_an_alert(hub):
    phone = Phone("shelf", "Shelf Phone", charging=True, battery=88)
    phone.report(times=3)                        # settled, plugged in

    ok = check("nothing is raised while it stays plugged in",
               not events_of(hub, "shelf", "unplugged"))

    # The charger comes out. It has to stay out for a few seconds before
    # we believe it, so keep reporting across that window.
    phone.report(times=10, charging=False)
    time.sleep(1.0)

    raised = events_of(hub, "shelf", "unplugged")
    ok &= check("unplugging it raises an alert", len(raised) == 1,
                raised[0]["detail"] if raised else "nothing")
    if raised:
        ok &= check("and the alert says how much battery is left",
                    "88%" in raised[0]["detail"], raised[0]["detail"])

    # It must not keep saying it for as long as it stays unplugged.
    phone.report(times=6, gap=0.3)
    time.sleep(0.5)
    ok &= check("and says it once, not on every report",
                len(events_of(hub, "shelf", "unplugged")) == 1,
                f"{len(events_of(hub, 'shelf', 'unplugged'))} alerts")
    phone.close()
    return ok


def test_a_blip_is_not_an_alert(hub):
    """Phones misreport for a moment. Waking somebody for that is worse."""
    phone = Phone("blip", "Blippy", charging=True, battery=70)
    phone.report(times=3)
    phone.ws.send(json.dumps(dict(phone.state, charging=False)))
    time.sleep(0.2)
    phone.report(times=3, charging=True)         # back within a moment
    time.sleep(0.8)
    ok = check("a momentary blip raises nothing",
               not events_of(hub, "blip", "unplugged"),
               str(events_of(hub, "blip", "unplugged")))
    phone.close()
    return ok


def test_a_machine_with_no_battery_is_left_alone(hub):
    """A laptop on mains reports no charging state at all."""
    desk = Phone("desk", "Desk PC", charging=None, battery=None)
    desk.report(times=4)
    ok = check("a camera with no battery never raises it",
               not events_of(hub, "desk", "unplugged"))
    desk.close()
    return ok


def test_the_siren_can_be_sounded_and_stopped(hub):
    opener = login()
    phone = Phone("cabin", "Cabin Phone")
    phone.report(times=2)

    opener.open(BASE + "/cmd/cabin/alarm",
                data=json.dumps({"on": True}).encode(),
                timeout=10)
    time.sleep(0.6)
    told = [m for m in phone.heard if m.get("t") == "alarm"]
    ok = check("the siren command reaches the device",
               told and told[-1].get("on") is True, str(told))

    opener.open(BASE + "/cmd/cabin/alarm",
                data=json.dumps({"on": False}).encode(),
                timeout=10)
    time.sleep(0.6)
    told = [m for m in phone.heard if m.get("t") == "alarm"]
    ok &= check("and so does turning it off",
                told and told[-1].get("on") is False, str(told))

    # What the dashboard shows has to follow the device, not the button.
    phone.report(times=2, alarm=True)
    state = json.loads(opener.open(BASE + "/state", timeout=10).read())
    card = next((c for c in state["cameras"] if c["id"] == "cabin"), {})
    ok &= check("the dashboard shows a siren that is sounding",
                card.get("alarm") is True, str(card.get("alarm")))
    phone.close()
    return ok


def test_an_unknown_command_is_refused(hub):
    """The command list is an allow-list; it has to stay one."""
    opener = login()
    phone = Phone("allow", "Allow List")
    phone.report(times=2)
    try:
        opener.open(BASE + "/cmd/allow/explode", data=b"{}", timeout=10)
        refused, why = False, "it went through"
    except urllib.error.HTTPError as e:
        refused, why = e.code == 400, f"HTTP {e.code}"
    phone.close()
    return check("a command that is not on the list is refused", refused, why)


def main():
    import urllib.error                           # noqa: F401
    srv = start_server()
    hub = srv.hub
    for test in (test_pulling_the_charger_raises_an_alert,
                 test_a_blip_is_not_an_alert,
                 test_a_machine_with_no_battery_is_left_alone,
                 test_the_siren_can_be_sounded_and_stopped,
                 test_an_unknown_command_is_refused):
        test(hub)
    print()
    print(f"theft alerts: {len(PASSED)} passed, {len(FAILED)} failed")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    import urllib.error
    sys.exit(main())
