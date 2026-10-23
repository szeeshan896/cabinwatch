"""
Only waking somebody for a person.

Movement on its own is a poor burglar alarm. A curtain over an open window,
a ceiling fan, headlights crossing a wall, the sun coming out - they all
move, and after a few nights of being woken by them you stop trusting the
phone. That is worse than having no alarm, because now the real one gets
ignored too.

So in the cabin an alert needs a person in the frame. The phone decides
that itself, on the phone, with its own labeller - no picture is sent
anywhere to answer the question.

The labelling itself cannot be run here; what can, and what would be wrong
on any machine, is the decision around it: which labels mean somebody is
there, that a baby camera is never put behind this check, and that a phone
which cannot answer raises the alert rather than swallowing it.
"""

import os
import re
import sys
import json
import time
import shutil
import tempfile
import threading
import urllib.parse
import urllib.request
import http.cookiejar

WORK = tempfile.mkdtemp(prefix="cabinwatch-person-")
os.environ.update(AGENT_TOKEN="demo", VIEW_PASSWORD="demo", SECRET_KEY="f",
                  PORT="8092", NTFY_TOPIC="",
                  EVENT_LOG=os.path.join(WORK, "events.json"),
                  SCHEDULE_FILE=os.path.join(WORK, "schedules.json"),
                  EVIDENCE_DIR=os.path.join(WORK, "evidence"))
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import uvicorn                                    # noqa: E402
import websocket                                  # noqa: E402

BASE = "http://127.0.0.1:8092"
WS = "ws://127.0.0.1:8092"
KOTLIN = os.path.join(ROOT, "android", "app", "src", "main", "java",
                      "com", "cabinwatch")

PASSED = []
FAILED = []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(("PASS  " if ok else "FAIL  ") + name
          + (("  -- " + detail) if detail else ""))
    return bool(ok)


def read(name):
    with open(os.path.join(KOTLIN, name)) as f:
        return f.read()


def person_words():
    """The label list the phone matches against, read out of the source."""
    block = re.search(r"PERSON_WORDS = setOf\((.*?)\)", read("PersonDetector.kt"),
                      re.S)
    return set(re.findall(r'"([^"]+)"', block.group(1))) if block else set()


def test_the_labels_that_mean_somebody_is_there():
    """A camera on a shelf rarely sees a whole person.

    The general labeller says Person when it can see one, but far more
    often it can only see part of somebody and says Arm, Hair or Shoe. A
    list with only "person" in it would miss most real intrusions.
    """
    words = person_words()
    ok = check("the obvious one is there", "person" in words)
    partial = {"arm", "hand", "hair", "head", "leg", "foot", "face"}
    ok &= check("and the parts of one, which is what a shelf camera sees",
                partial <= words, str(sorted(partial - words)))
    worn = {"shoe", "shirt", "jeans", "footwear"}
    ok &= check("and what people wear", bool(worn & words),
                str(sorted(worn & words)))

    # And it must not match things that are in every empty room.
    source = read("PersonDetector.kt")
    for innocent in ("curtain", "window", "wall", "plant", "ceiling", "light",
                     "fan", "furniture", "table", "floor"):
        ok &= check(f"an empty room's {innocent} is not a person",
                    f'"{innocent}"' not in source)
    return ok


def test_the_check_only_runs_when_something_moved():
    """Running a labeller on every frame would cook the battery by morning."""
    service = read("CameraService.kt")
    asked_inside_motion = re.search(
        r"if \(moved && !motionOn && settings\.armed\) \{\s*raiseMovement",
        service)
    ok = check("the labeller is only asked after movement",
               bool(asked_inside_motion))
    detector = read("PersonDetector.kt")
    ok &= check("and not twice at once, or twice in a moment",
                "if (busy || now - lastAsked < minGapMs) return" in detector)
    return ok


def test_a_phone_that_cannot_answer_still_raises_the_alert():
    """Silence from the labeller must not mean silence from the alarm."""
    detector = read("PersonDetector.kt")
    failures = detector.count("answer.saw(true)")
    ok = check("every way it can fail still raises the alert",
               failures >= 3, f"{failures} such paths")
    ok &= check("and it says why in the log",
                "person detection is not working here" in detector)
    return ok


def test_a_baby_camera_is_never_put_behind_it():
    """A sleeping child is not a person-shaped thing on a camera."""
    service = read("CameraService.kt")
    ok = check("baby mode skips the check entirely",
               "if (settings.babyMode || !settings.personDetection)" in service)
    settings = read("Settings.kt")
    ok &= check("and it defaults off for a baby, on for a cabin",
                "sp.getBoolean(K_PERSON, !babyMode)" in settings)
    return ok


def test_the_switch_works_from_the_dashboard():
    import server.main as srv
    config = uvicorn.Config(srv.app, host="127.0.0.1", port=8092,
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
        {"token": "demo", "id": "cabin", "name": "Cabin", "kind": "android"})
    ws = websocket.create_connection(f"{WS}/ws/camera?{query}", timeout=5)
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
    ws.send(json.dumps({"t": "state", "name": "Cabin", "status": "live",
                        "armed": True, "baby_mode": False,
                        "person_detection": False}))
    time.sleep(0.6)

    opener.open(BASE + "/cmd/cabin/person",
                data=json.dumps({"on": True}).encode(), timeout=10)
    time.sleep(0.6)
    told = [m for m in heard if m.get("t") == "person_detection"]
    ok = check("turning it on reaches the phone",
               told and told[-1].get("on") is True, str(told))

    ws.send(json.dumps({"t": "state", "name": "Cabin", "person_detection": True}))
    time.sleep(0.5)
    state = json.loads(opener.open(BASE + "/state", timeout=10).read())
    card = next((c for c in state["cameras"] if c["id"] == "cabin"), {})
    ok &= check("and the dashboard shows what the phone says",
                card.get("person_detection") is True,
                str(card.get("person_detection")))

    alive[0] = False
    ws.close()
    return ok


def main():
    try:
        for test in (test_the_labels_that_mean_somebody_is_there,
                     test_the_check_only_runs_when_something_moved,
                     test_a_phone_that_cannot_answer_still_raises_the_alert,
                     test_a_baby_camera_is_never_put_behind_it,
                     test_the_switch_works_from_the_dashboard):
            test()
        print()
        print(f"person only: {len(PASSED)} passed, {len(FAILED)} failed")
        return 0 if not FAILED else 1
    finally:
        shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
