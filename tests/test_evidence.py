"""
Keeping the footage after the camera is gone.

The recordings live on the device that made them. For the cabin that is a
hole straight through the middle of the idea: the thing being recorded is
somebody taking the device, and when they do, the recording of them doing it
goes with it.

So a clip an alert was raised during is offered to the server as soon as it
is finished, and the server keeps a copy. These checks cover what it keeps,
what it refuses to keep, and - the part that matters - that the copy can
still be watched once the camera is no longer there at all.
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
import http.cookiejar

WORK = tempfile.mkdtemp(prefix="cabinwatch-evidence-")
os.environ.update(AGENT_TOKEN="demo", VIEW_PASSWORD="demo", SECRET_KEY="f",
                  PORT="8096", NTFY_TOPIC="",
                  EVIDENCE_DIR=os.path.join(WORK, "evidence"),
                  EVIDENCE_MAX_MB="2")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uvicorn                                    # noqa: E402
import websocket                                  # noqa: E402

BASE = "http://127.0.0.1:8096"
WS = "ws://127.0.0.1:8096"

PASSED = []
FAILED = []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(("PASS  " if ok else "FAIL  ") + name
          + (("  -- " + detail) if detail else ""))
    return bool(ok)


def start_server():
    import server.main as srv
    config = uvicorn.Config(srv.app, host="127.0.0.1", port=8096,
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


class FakeCamera:
    """A camera that holds some clips and hands them over when asked."""

    def __init__(self, cam_id, name, clips):
        self.cam_id = cam_id
        self.name = name
        self.clips = clips                    # {filename: bytes}
        self.asked_for = []
        query = urllib.parse.urlencode(
            {"token": "demo", "id": cam_id, "name": name, "kind": "agent"})
        self.ws = websocket.create_connection(f"{WS}/ws/camera?{query}",
                                              timeout=5)
        self.alive = True
        threading.Thread(target=self._listen, daemon=True).start()
        threading.Thread(target=self._report, daemon=True).start()

    def _report(self):
        state = {"t": "state", "name": self.name, "status": "live",
                 "armed": True, "recording": True}
        while self.alive:
            try:
                self.ws.send(json.dumps(state))
            except Exception:
                return
            time.sleep(0.5)

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
            if msg.get("t") == "get_clip":
                self._send_clip(msg.get("file"), msg.get("xfer"))

    def _send_clip(self, filename, xfer):
        self.asked_for.append(filename)
        body = self.clips.get(filename)
        if body is None:
            self.ws.send(json.dumps({"t": "clip_error", "xfer": xfer,
                                     "detail": "not found"}))
            return
        self.ws.send(json.dumps({"t": "clip_start", "xfer": xfer,
                                 "size": len(body),
                                 "content_type": "video/mp4"}))
        # 0x03, then the transfer id, then the bytes - the wire format the
        # server already speaks for clip chunks.
        for i in range(0, len(body), 32768):
            part = body[i:i + 32768]
            self.ws.send_binary(bytes([3]) + xfer.to_bytes(4, "big") + part)
            time.sleep(0.01)
        self.ws.send(json.dumps({"t": "clip_end", "xfer": xfer}))

    def event(self, kind, clip, detail="something happened"):
        self.ws.send(json.dumps({"t": "event", "kind": kind, "detail": detail,
                                 "clip": clip}))

    def finished(self, clip):
        self.ws.send(json.dumps({"t": "clip_ready", "file": clip,
                                 "event": True}))

    def close(self):
        self.alive = False
        try:
            self.ws.close()
        except Exception:
            pass


def a_clip(megabytes=0.3, seed=b"x"):
    """Something clip-shaped and compressible-resistant."""
    import hashlib
    out = bytearray(b"\x00\x00\x00\x1cftypisom")
    block = hashlib.sha256(seed).digest()
    while len(out) < megabytes * 1048576:
        block = hashlib.sha256(block).digest()
        out += block
    return bytes(out)


def wait_for(fn, seconds=20):
    end = time.time() + seconds
    while time.time() < end:
        got = fn()
        if got:
            return got
        time.sleep(0.25)
    return fn()


def saved_list(opener, cam=None):
    url = BASE + "/saved" + (f"?cam={cam}" if cam else "")
    return json.loads(opener.open(url, timeout=10).read())


def test_a_clip_with_a_real_alert_is_kept(opener):
    body = a_clip(0.3, b"theft")
    cam = FakeCamera("cabin", "Cabin", {"night.mp4": body})
    time.sleep(1.0)
    cam.event("person", "night.mp4", "Somebody is in the cabin.")
    time.sleep(0.5)
    cam.finished("night.mp4")

    rows = wait_for(lambda: saved_list(opener, "cabin")["clips"])
    ok = check("a clip a person was seen in is kept", bool(rows),
               str([r["file"] for r in rows]))
    if not rows:
        cam.close()
        return False
    ok &= check("the camera was asked for it", "night.mp4" in cam.asked_for,
                str(cam.asked_for))

    got = opener.open(BASE + "/saved/" + rows[0]["key"], timeout=20).read()
    ok &= check("and the copy is the whole clip, byte for byte", got == body,
                f"{len(got)} of {len(body)} bytes")

    # The point of all this: it is still here with the camera gone.
    cam.close()
    time.sleep(1.5)
    got = opener.open(BASE + "/saved/" + rows[0]["key"], timeout=20).read()
    ok &= check("and it still plays after the camera disappears",
                len(got) == len(body), f"{len(got)} bytes")
    return ok


def test_ordinary_movement_is_not_kept(opener):
    """A room somebody lives in would fill the store in an hour."""
    cam = FakeCamera("baby", "Baby Room", {"quiet.mp4": a_clip(0.2, b"m")})
    time.sleep(1.0)
    cam.event("motion", "quiet.mp4", "Your baby moved.")
    time.sleep(0.5)
    cam.finished("quiet.mp4")
    time.sleep(3.0)

    rows = saved_list(opener, "baby")["clips"]
    ok = check("a clip with only movement in it is not kept", not rows,
               str([r["file"] for r in rows]))
    ok &= check("and the camera was never asked for it",
                "quiet.mp4" not in cam.asked_for, str(cam.asked_for))
    cam.close()
    return ok


def test_a_clip_nothing_was_raised_in_is_refused(opener):
    """An offer is not an instruction - the server decides."""
    cam = FakeCamera("gate", "Gate", {"nothing.mp4": a_clip(0.2, b"n")})
    time.sleep(1.0)
    cam.finished("nothing.mp4")           # offered with no event at all
    time.sleep(3.0)
    ok = check("an unexplained offer is refused",
               not saved_list(opener, "gate")["clips"] and
               "nothing.mp4" not in cam.asked_for, str(cam.asked_for))
    cam.close()
    return ok


def test_the_store_stays_within_its_limit(opener):
    """It is evidence, not an archive, and the disk is not ours to fill."""
    cam = FakeCamera("store", "Store Room",
                     {f"clip{i}.mp4": a_clip(0.6, f"s{i}".encode())
                      for i in range(6)})
    time.sleep(1.0)
    for i in range(6):
        cam.event("impact", f"clip{i}.mp4", "A thud.")
        time.sleep(0.3)
        cam.finished(f"clip{i}.mp4")
        time.sleep(1.5)

    time.sleep(3.0)
    body = saved_list(opener, "store")
    total = sum(r["size_mb"] for r in body["clips"])
    ok = check("the store holds what it can", bool(body["clips"]),
               f"{len(body['clips'])} clips, {total:.1f} MB")
    ok &= check("and does not grow past its limit", total <= 2.4,
                f"{total:.1f} MB of a 2 MB limit")

    # The newest must be the ones that survived.
    kept = {r["file"] for r in body["clips"]}
    ok &= check("keeping the newest, not the first it happened to get",
                "clip5.mp4" in kept and "clip0.mp4" not in kept, str(sorted(kept)))
    cam.close()
    return ok


def test_the_event_points_at_the_copy(opener):
    """From the dashboard, the alert itself has to lead to the footage."""
    import server.main as srv
    found = [e for e in srv.hub.events
             if e.get("camera") == "cabin" and e.get("saved")]
    ok = check("the alert carries a link to the saved copy", bool(found),
               str([e.get("saved") for e in found]))
    if found:
        got = opener.open(BASE + found[0]["saved"], timeout=20).read()
        ok &= check("and that link really is the clip", len(got) > 1024,
                    f"{len(got)} bytes")
    return ok


def test_a_dropped_copy_leaves_no_dead_link(opener):
    """Once the store drops a clip, the alert must stop pointing at it.

    Only found because the limit test ran first and quietly evicted the
    clip the earlier test had linked - which is exactly what would happen
    on a busy night.
    """
    import server.main as srv
    dead = []
    for ev in srv.hub.events:
        link = ev.get("saved")
        if not link:
            continue
        try:
            opener.open(BASE + link, timeout=10).read(1)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                dead.append(link)
    return check("no alert points at a copy that has been dropped",
                 not dead, str(dead))


def main():
    start_server()
    opener = login()
    try:
        for test in (test_a_clip_with_a_real_alert_is_kept,
                     test_the_event_points_at_the_copy,
                     test_ordinary_movement_is_not_kept,
                     test_a_clip_nothing_was_raised_in_is_refused,
                     test_the_store_stays_within_its_limit,
                     test_a_dropped_copy_leaves_no_dead_link):
            test(opener)
        print()
        print(f"evidence: {len(PASSED)} passed, {len(FAILED)} failed")
        return 0 if not FAILED else 1
    finally:
        shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
