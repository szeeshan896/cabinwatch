"""
Arming the cabin around the hours somebody is in it.

Left to a person, a camera is armed when they remember and disarmed when
they get tired of being alerted about themselves - and it is the second one
that sticks. So the window is written down once, said the way somebody can
actually answer it ("when are you normally there"), and the server holds the
camera at whatever that window asks for.

The clock matters here more than anywhere. The server is in a datacentre and
on Railway it runs on UTC, so "I am there from ten" has to mean ten where the
person is, not ten where the server is. Quiet hours had the same bug and the
same fix.
"""

import os
import sys
import json
import time
import shutil
import datetime
import tempfile
import threading
import urllib.parse
import urllib.request
import http.cookiejar

WORK = tempfile.mkdtemp(prefix="cabinwatch-schedule-")
os.environ.update(AGENT_TOKEN="demo", VIEW_PASSWORD="demo", SECRET_KEY="f",
                  PORT="8094", NTFY_TOPIC="", TIMEZONE="Asia/Kolkata",
                  EVENT_LOG=os.path.join(WORK, "events.json"),
                  EVIDENCE_DIR=os.path.join(WORK, "evidence"),
                  SCHEDULE_FILE=os.path.join(WORK, "schedules.json"),
                  SCHEDULE_CHECK_SECONDS="2")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uvicorn                                    # noqa: E402
import websocket                                  # noqa: E402

from server.schedule import Schedules             # noqa: E402

BASE = "http://127.0.0.1:8094"
WS = "ws://127.0.0.1:8094"

PASSED = []
FAILED = []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(("PASS  " if ok else "FAIL  ") + name
          + (("  -- " + detail) if detail else ""))
    return bool(ok)


def at(day, hour, minute=0):
    """A moment in October 2026. The 19th is a Monday."""
    return datetime.datetime(2026, 10, 19 + day, hour, minute)


def test_the_window_means_what_it_says():
    sched = Schedules(path=os.path.join(WORK, "unit.json"))
    sched.set("cabin", "10:00", "19:00", days=[0, 1, 2, 3, 4, 5])

    cases = [
        (at(0, 9, 30), True, "before you get there"),
        (at(0, 10, 0), False, "the moment you arrive"),
        (at(0, 14, 0), False, "while you are there"),
        (at(0, 18, 59), False, "just before you leave"),
        (at(0, 19, 0), True, "the moment you leave"),
        (at(0, 23, 0), True, "overnight"),
        (at(6, 14, 0), True, "a day you are not there at all"),
    ]
    ok = True
    for when, want, why in cases:
        got = sched.should_be_armed("cabin", when)
        ok &= check(f"armed {str(want).lower()} {why}", got is want,
                    f"{when:%a %H:%M} gave {got}")
    return ok


def test_a_window_that_crosses_midnight():
    """A night shift is the other way round and must not break it."""
    sched = Schedules(path=os.path.join(WORK, "night.json"))
    sched.set("yard", "22:00", "06:00")
    ok = check("armed in the afternoon",
               sched.should_be_armed("yard", at(0, 15, 0)) is True)
    ok &= check("left alone at midnight",
                sched.should_be_armed("yard", at(0, 23, 59)) is False)
    ok &= check("still left alone after midnight",
                sched.should_be_armed("yard", at(1, 2, 0)) is False)
    ok &= check("watching again by breakfast",
                sched.should_be_armed("yard", at(1, 7, 0)) is True)
    return ok


def test_a_camera_with_no_schedule_is_nobody_elses_business():
    sched = Schedules(path=os.path.join(WORK, "none.json"))
    return check("a camera with no window is left entirely alone",
                 sched.should_be_armed("baby") is None)


def test_nonsense_is_refused():
    sched = Schedules(path=os.path.join(WORK, "bad.json"))
    bad = 0
    for pair in (("25:00", "19:00"), ("10:00", "aa:bb"), ("", "19:00"),
                 ("12:00", "12:00")):
        try:
            sched.set("x", *pair)
        except ValueError:
            bad += 1
    return check("times that are not times are refused", bad == 4,
                 f"{bad} of 4")


def test_the_server_holds_the_camera_to_it():
    """The part that matters in practice: it keeps pushing until it agrees."""
    import server.main as srv
    config = uvicorn.Config(srv.app, host="127.0.0.1", port=8094,
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

    heard = []
    query = urllib.parse.urlencode(
        {"token": "demo", "id": "cabin", "name": "Cabin", "kind": "agent"})
    ws = websocket.create_connection(f"{WS}/ws/camera?{query}", timeout=5)
    state = {"t": "state", "name": "Cabin", "status": "live", "armed": True}

    alive = [True]

    def listen():
        while alive[0]:
            try:
                raw = ws.recv()
            except Exception:
                return
            if isinstance(raw, str):
                try:
                    heard.append(json.loads(raw))
                except Exception:
                    pass

    threading.Thread(target=listen, daemon=True).start()
    ws.send(json.dumps(state))
    time.sleep(0.6)

    # A window that is open right now, whatever time the test runs.
    from server import settings
    now = settings.now()
    start = (now - datetime.timedelta(minutes=30)).strftime("%H:%M")
    end = (now + datetime.timedelta(hours=2)).strftime("%H:%M")
    body = json.dumps({"off_from": start, "off_until": end}).encode()
    answer = json.loads(opener.open(BASE + "/schedule/cabin", data=body,
                                    timeout=10).read())
    ok = check("saving a window says what it means right now",
               answer["schedule"]["armed_now"] is False, str(answer))

    time.sleep(0.8)
    told = [m for m in heard if m.get("t") == "arm"]
    ok &= check("and the camera is told at once, not on the next sweep",
                told and told[-1].get("on") is False, str(told))

    # The camera says it is armed anyway - the keeper must correct it.
    heard.clear()
    ws.send(json.dumps(dict(state, armed=True)))
    time.sleep(5)
    told = [m for m in heard if m.get("t") == "arm"]
    ok &= check("a camera that disagrees is put back",
                told and told[-1].get("on") is False, str(told))

    # And it survives a restart of the server.
    again = Schedules()
    ok &= check("the window is still there after a restart",
                again.get("cabin") is not None, str(again.get("cabin")))

    alive[0] = False
    ws.close()
    return ok


def test_quiet_hours_use_the_right_clock():
    """The latent version of the same bug: quiet hours ran on server time."""
    from server import settings
    here = settings.now()
    there = datetime.datetime.now()
    return check("the server knows which clock to answer with",
                 here.strftime("%Z") == "IST"
                 and here.hour != there.hour,
                 f"{here:%H:%M %Z} against the host's {there:%H:%M}")


def main():
    try:
        for test in (test_the_window_means_what_it_says,
                     test_a_window_that_crosses_midnight,
                     test_a_camera_with_no_schedule_is_nobody_elses_business,
                     test_nonsense_is_refused,
                     test_quiet_hours_use_the_right_clock,
                     test_the_server_holds_the_camera_to_it):
            test()
        print()
        print(f"schedule: {len(PASSED)} passed, {len(FAILED)} failed")
        return 0 if not FAILED else 1
    finally:
        shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
