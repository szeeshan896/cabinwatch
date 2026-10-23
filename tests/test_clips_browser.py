"""
Finding the clip you want among a day of them.

Round-the-clock recording makes several hundred files a day. A flat list
of filenames is fine with six and useless with six hundred - you cannot
tell a night when nothing happened from a night when something did without
opening them one at a time.

So the page groups them by day, draws the day as twenty-four blocks lit
where something was recorded and coloured where something happened, and
can hide everything that was quiet. None of that touches the server: it
passes on the list the device already keeps and the copies it already has,
and does no searching of its own.

The last check is the one that matters after a theft: the recordings of a
camera that no longer exists are still reachable.
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

WORK = tempfile.mkdtemp(prefix="cabinwatch-clips-")
os.environ.update(AGENT_TOKEN="demo", VIEW_PASSWORD="demo", SECRET_KEY="f",
                  PORT="8093", NTFY_TOPIC="",
                  EVENT_LOG=os.path.join(WORK, "events.json"),
                  SCHEDULE_FILE=os.path.join(WORK, "schedules.json"),
                  EVIDENCE_DIR=os.path.join(WORK, "evidence"))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import uvicorn                                    # noqa: E402
import websocket                                  # noqa: E402
import browser                                    # noqa: E402

BASE = "http://127.0.0.1:8093"
WS = "ws://127.0.0.1:8093"

PASSED = []
FAILED = []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(("PASS  " if ok else "FAIL  ") + name
          + (("  -- " + detail) if detail else ""))
    return bool(ok)


def a_clip(seed=b"x", kb=40):
    import hashlib
    out = bytearray(b"\x00\x00\x00\x1cftypisom")
    block = hashlib.sha256(seed).digest()
    while len(out) < kb * 1024:
        block = hashlib.sha256(block).digest()
        out += block
    return bytes(out)


class Camera:
    """A camera holding a day and a half of recordings."""

    def __init__(self, cam_id, name, clips):
        self.cam_id, self.name, self.clips = cam_id, name, clips
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
            if msg.get("t") == "list_clips":
                self.ws.send(json.dumps({"t": "clips", "items": self.clips}))
            elif msg.get("t") == "get_clip":
                self._send(msg.get("file"), msg.get("xfer"))

    def _send(self, filename, xfer):
        body = a_clip(filename.encode())
        self.ws.send(json.dumps({"t": "clip_start", "xfer": xfer,
                                 "size": len(body),
                                 "content_type": "video/mp4"}))
        self.ws.send_binary(bytes([3]) + xfer.to_bytes(4, "big") + body)
        self.ws.send(json.dumps({"t": "clip_end", "xfer": xfer}))

    def event(self, kind, clip, detail="something"):
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


def build_clips():
    """Recordings placed relative to now, so the data is the same at 3am.

    An earlier version laid them out against fixed hours of the day, which
    meant that running at six in the morning produced a different set from
    running at six in the evening - and the test failed on a build machine
    for reasons that had nothing to do with the code.
    """
    now = time.time()
    today = time.localtime(now)
    midnight = time.mktime((today.tm_year, today.tm_mon, today.tm_mday,
                            0, 0, 0, 0, 0, -1))

    def row(at, keep):
        at = int(at)
        return {"file": f"clip-{at}.mp4", "size_mb": 4.2, "at": at,
                "when": time.strftime("%d %b %Y, %H:%M:%S",
                                      time.localtime(at)),
                "keep": keep}

    # Today: far enough back to be safely in the past whatever the hour,
    # and a mix of event and quiet within it so the filter has work to do.
    rows = []
    for minutes, keep in ((90, True), (75, False), (60, False), (45, True)):
        at = now - minutes * 60
        if at > midnight:
            rows.append(row(at, keep))
    if not rows:                      # the small hours: use just after midnight
        rows = [row(midnight + 60, True), row(midnight + 120, False)]

    # Yesterday, so there is always more than one day to choose between.
    rows.append(row(midnight - 3600, False))
    rows.append(row(midnight - 8 * 3600, True))
    return rows


def start_server():
    import server.main as srv
    config = uvicorn.Config(srv.app, host="127.0.0.1", port=8093,
                            log_level="error")
    threading.Thread(target=uvicorn.Server(config).run, daemon=True).start()
    for _ in range(80):
        try:
            urllib.request.urlopen(BASE + "/healthz", timeout=1)
            return srv
        except Exception:
            time.sleep(0.25)
    raise SystemExit("the server never came up")


def main():
    srv = start_server()
    clips = build_clips()
    cam = Camera("cabin", "Cabin", clips)
    time.sleep(1.0)

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar))
    opener.open(BASE + "/login",
                data=urllib.parse.urlencode({"password": "demo"}).encode())

    # Wait for the camera to be answering with its list before driving the
    # page, rather than racing it and blaming the page for what is missing.
    listed = []
    for _ in range(40):
        try:
            listed = json.loads(
                opener.open(BASE + "/clips/cabin", timeout=10).read()
            ).get("clips", [])
        except Exception:
            listed = []
        if len(listed) == len(clips):
            break
        time.sleep(0.5)
    check("the camera answers with everything it is holding",
          len(listed) == len(clips), f"{len(listed)} of {len(clips)}")

    # One clip also has a copy on the server, through the ordinary path.
    kept = clips[0]["file"]
    cam.event("person", kept, "Someone is there.")
    time.sleep(0.4)
    cam.finished(kept)
    time.sleep(2.5)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = browser.launch(pw)
        page = b.new_context(viewport={"width": 900, "height": 1200}).new_page()
        page.goto(BASE + "/login")
        page.fill("#pw", "demo")
        page.click("button[type=submit]")
        page.wait_for_selector(".cam", timeout=15000)

        page.click('[data-act="clips"]')
        page.wait_for_selector(".strip i", timeout=15000)

        check("the day is drawn as a strip of hours",
              page.locator(".strip i").count() == 24,
              f"{page.locator('.strip i').count()} blocks")

        lit = page.locator(".strip i.on, .strip i.hot").count()
        check("only the hours with recordings are lit", 0 < lit < 24,
              f"{lit} of 24 lit")
        check("and the hours something happened in stand out",
              page.locator(".strip i.hot").count() > 0,
              f"{page.locator('.strip i.hot').count()} marked")

        everything = page.locator(".clip").count()
        check("today's recordings are listed", everything > 0,
              f"{everything} rows")

        page.click('[data-only="1"]')
        page.wait_for_timeout(300)
        only = page.locator(".clip").count()
        check("the filter hides the quiet ones",
              0 < only < everything, f"{only} of {everything}")
        check("and what is left is what happened",
              page.locator(".clip .kp").count() >= only,
              f"{page.locator('.clip .kp').count()} marked")

        page.click('[data-only="0"]')
        page.wait_for_timeout(200)

        # Yesterday is a separate day and must be reachable.
        days = page.locator("[data-day]").count()
        check("each day is its own tab", days >= 2, f"{days} days")
        page.locator("[data-day]").nth(1).click()
        page.wait_for_timeout(300)
        check("yesterday's recordings are there too",
              page.locator(".clip").count() > 0,
              f"{page.locator('.clip').count()} rows")

        page.locator("[data-day]").first.click()
        page.wait_for_timeout(300)
        check("the copy kept on the server is marked as such",
              page.locator(".clip .kp.cp").count() == 1,
              f"{page.locator('.clip .kp.cp').count()} marked")

        page.screenshot(path=os.path.join(WORK, "clips.png"))

        # And the part that matters after a theft.
        page.click("#modal .ghost")
        cam.close()
        page.wait_for_timeout(2000)
        page.wait_for_selector(".cam.offline", timeout=20000)
        check("Clips still works on a camera that has gone",
              not page.locator('[data-act="clips"]').first.is_disabled())
        page.locator('[data-act="clips"]').first.click()
        page.wait_for_selector(".clip", timeout=15000)
        check("and the server's copy is still playable",
              page.locator(".clip").count() >= 1,
              f"{page.locator('.clip').count()} rows")
        src = page.locator(".clip a").first.get_attribute("href")
        check("from the server, not the camera that is not there",
              src.startswith("/saved/"), src)
        b.close()

    print()
    print(f"clips browser: {len(PASSED)} passed, {len(FAILED)} failed")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
