"""
Two devices, one name - and what happens to a camera that stops.

Both of these came from the same evening. A phone and a laptop had been set
up under the same camera name, so each one kept knocking the other off the
server: their pictures and their alerts arrived under a single name, and
neither stayed connected for long. None of it was visible anywhere - the
losing device simply vanished off the dashboard with no record of when, and
the only clue was that the snapshots did not match the camera they claimed
to come from.

So: a name that is in use by a device that is still talking turns the second
one away instead, loudly; and a camera that stops stays on the dashboard
with the time it was last heard from.
"""

import os
import sys
import json
import time
import shutil
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

# Its own corner of the disk: the server now keeps its history and its
# saved clips, and a test that read another run's would be testing that.
WORK = tempfile.mkdtemp(prefix="cabinwatch-test-")
os.environ.update(AGENT_TOKEN="demo", VIEW_PASSWORD="demo", SECRET_KEY="f",
                  PORT="8097", NTFY_TOPIC="",
                  EVENT_LOG=os.path.join(WORK, "events.json"),
                  EVIDENCE_DIR=os.path.join(WORK, "evidence"),
                  SCHEDULE_FILE=os.path.join(WORK, "schedules.json"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uvicorn                                    # noqa: E402
import websocket                                  # noqa: E402

BASE = "http://127.0.0.1:8097"
WS = "ws://127.0.0.1:8097"

PASSED = []
FAILED = []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(("PASS  " if ok else "FAIL  ") + name
          + (("  -- " + detail) if detail else ""))
    return bool(ok)


def start_server():
    import server.main as srv
    config = uvicorn.Config(srv.app, host="127.0.0.1", port=8097,
                            log_level="error")
    threading.Thread(target=uvicorn.Server(config).run, daemon=True).start()
    for _ in range(80):
        try:
            urllib.request.urlopen(BASE + "/healthz", timeout=1)
            return srv
        except Exception:
            time.sleep(0.25)
    raise SystemExit("the server never came up")


def connect(cam_id, name, keep_talking=True):
    """A camera that behaves like a real one: it keeps reporting."""
    query = urllib.parse.urlencode(
        {"token": "demo", "id": cam_id, "name": name, "kind": "agent"})
    ws = websocket.create_connection(f"{WS}/ws/camera?{query}", timeout=5)
    state = {"t": "state", "name": name, "status": "live", "armed": True}
    ws.send(json.dumps(state))

    stop = threading.Event()

    def pump():
        while not stop.wait(0.5):
            try:
                ws.send(json.dumps(state))
            except Exception:
                return

    if keep_talking:
        threading.Thread(target=pump, daemon=True).start()
    ws.stop = stop
    return ws


def login():
    import http.cookiejar
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar))
    opener.open(BASE + "/login",
                data=urllib.parse.urlencode({"password": "demo"}).encode())
    return opener


def test_a_second_device_with_the_same_name_is_turned_away(hub):
    first = connect("office-room", "Office Room")
    time.sleep(1.0)

    try:
        second = websocket.create_connection(
            f"{WS}/ws/camera?token=demo&id=office-room&name=Office+Room",
            timeout=5)
        second.send(json.dumps({"t": "state", "name": "Office Room"}))
        time.sleep(0.8)
        # Read the raw frame, which is where the reason for a close lives.
        second.settimeout(3)
        refused, closed = False, None
        try:
            frame = second.recv_frame()
            if frame.opcode == websocket.ABNF.OPCODE_CLOSE:
                refused = True
                if len(frame.data) >= 2:
                    closed = int.from_bytes(frame.data[:2], "big")
        except Exception:
            refused = True
    except Exception:
        refused, closed = True, None

    ok = check("a second device on a live name is refused", refused,
               f"close code {closed}")
    ok &= check("and told why, so it is not a mystery", closed == 4409,
                f"close code {closed}")
    ok &= check("the one already connected stays", hub.get("office-room")
                is not None)

    events = [e for e in hub.events if e["kind"] == "conflict"]
    ok &= check("and it says so, by name",
                len(events) == 1 and "Office Room" in events[0]["detail"],
                events[0]["detail"] if events else "no event")

    # The refused device will retry; the warning must not repeat each time.
    for _ in range(3):
        try:
            again = websocket.create_connection(
                f"{WS}/ws/camera?token=demo&id=office-room&name=Office+Room",
                timeout=5)
            again.close()
        except Exception:
            pass
        time.sleep(0.3)
    repeated = [e for e in hub.events if e["kind"] == "conflict"]
    ok &= check("retries do not fill the list with the same warning",
                len(repeated) == 1, f"{len(repeated)} warnings")

    first.stop.set()
    first.close()
    time.sleep(0.8)
    return ok


def test_a_camera_coming_back_is_not_treated_as_a_stranger(hub):
    """The ordinary case: the same camera restarts. It must just reconnect."""
    first = connect("kitchen", "Kitchen", keep_talking=False)
    time.sleep(0.5)
    first.close()                     # gone, with no goodbye
    time.sleep(0.5)

    second = connect("kitchen", "Kitchen")
    time.sleep(1.0)
    back = hub.get("kitchen") is not None
    ok = check("a camera that restarts connects straight back", back)
    ok &= check("no conflict is claimed for it",
                not [e for e in hub.events
                     if e["kind"] == "conflict" and e["camera"] == "kitchen"])
    second.stop.set()
    second.close()
    time.sleep(0.8)
    return ok


def test_a_camera_that_stops_stays_on_the_dashboard(hub):
    cam = connect("baby-room", "Baby Room")
    time.sleep(1.0)
    cam.stop.set()
    cam.close()
    time.sleep(1.0)

    state = hub.list_state()
    entry = next((c for c in state["cameras"] if c["id"] == "baby-room"), None)
    ok = check("it is still listed after it stops", entry is not None)
    if not entry:
        return False
    ok &= check("listed as offline", entry["online"] is False)
    ok &= check("with the time it was last heard from",
                entry.get("last_seen") and entry["offline_for_s"] >= 0,
                f"{entry.get('offline_for_s')}s ago")
    ok &= check("and its name, so it can be recognised",
                entry["name"] == "Baby Room")
    return ok


def test_it_can_be_taken_off_the_list_once_it_is_really_gone(hub):
    opener = login()
    try:
        opener.open(BASE + "/camera/baby-room/forget", data=b"")
        removed = True
    except urllib.error.HTTPError as e:
        removed, detail = False, e.read()
    ok = check("a stopped camera can be removed", removed)
    ok &= check("and then it is gone",
                not [c for c in hub.list_state()["cameras"]
                     if c["id"] == "baby-room"])

    live = connect("still-here", "Still Here")
    time.sleep(0.8)
    try:
        opener.open(BASE + "/camera/still-here/forget", data=b"")
        refused = False
    except urllib.error.HTTPError as e:
        refused = e.code == 409
    ok &= check("a camera that is still connected cannot be removed", refused)
    live.stop.set()
    live.close()
    return ok


def test_times_go_out_as_the_moment_they_happened(hub):
    """The server's clock is not the viewer's - Railway runs on UTC."""
    state = hub.list_state()
    ok = check("the server sends a timestamp, not its own local time",
               isinstance(state["server_time"], (int, float)),
               repr(state["server_time"])[:40])
    if state["events"]:
        e = state["events"][0]
        ok &= check("events carry the moment they happened",
                    isinstance(e.get("at"), (int, float)) and "when" not in e,
                    f"at={e.get('at')} when={e.get('when', 'absent')}")
    return ok


def main():
    srv = start_server()
    hub = srv.hub

    ok = True
    for test in (test_a_second_device_with_the_same_name_is_turned_away,
                 test_a_camera_coming_back_is_not_treated_as_a_stranger,
                 test_a_camera_that_stops_stays_on_the_dashboard,
                 test_it_can_be_taken_off_the_list_once_it_is_really_gone,
                 test_times_go_out_as_the_moment_they_happened):
        ok &= bool(test(hub))

    print()
    print(f"identity: {len(PASSED)} passed, {len(FAILED)} failed")
    return 0 if ok and not FAILED else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
