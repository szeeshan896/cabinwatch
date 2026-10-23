"""
Push notifications via ntfy.sh.

ntfy is free, needs no account, and has an app for both iPhone and Android.
You pick a secret topic name, subscribe to it in the app, and anything posted
to that topic lands on your phone instantly.

Keep the topic name long and random - anyone who knows a topic name can read
it. Example: cabinwatch-7f3a91c2e84b
"""

import time
import asyncio
import datetime

import httpx

from . import settings


# kind -> (emoji + title prefix, ntfy priority, tags)
# ntfy priorities: 1 min, 3 default, 4 high, 5 max (bypasses phone silent mode)
KINDS = {
    "impact":  ("Thud / fall detected", 5, "rotating_light"),
    "cry":     ("Baby sounds awake",    5, "baby"),
    "sound":   ("Sound detected",       4, "sound"),
    "motion":  ("Motion detected",      4, "eyes"),
    "person":  ("Someone is there",     5, "rotating_light"),
    "moved":   ("Camera was moved",     5, "rotating_light"),
    "battery_low": ("Camera battery low", 4, "battery"),
    "unplugged": ("Camera was unplugged", 5, "electric_plug"),
    "conflict": ("Two cameras, one name", 4, "warning"),
    "offline": ("Camera went offline",  4, "warning"),
    "online":  ("Camera back online",   2, "white_check_mark"),
    "disk":    ("Camera storage low",   3, "floppy_disk"),
    "test":    ("Test notification",    3, "bell"),
}

# These always push, even during quiet hours.
# A camera being interfered with, or about to die, matters at 3am as much
# as at noon - more, in fact.
ALWAYS_PUSH = {"impact", "cry", "person", "offline", "moved", "battery_low",
               "unplugged"}


class Notifier:
    def __init__(self):
        # (camera_id, kind) -> last push timestamp
        self._last = {}
        self._client = None

    async def _http(self):
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- gating --------------------------------------------------------------
    def _in_quiet_hours(self):
        a, b = settings.QUIET_FROM, settings.QUIET_TO
        if a < 0 or b < 0:
            return False
        hour = settings.now().hour
        if a <= b:
            return a <= hour < b
        # window wraps midnight, e.g. 23 -> 7
        return hour >= a or hour < b

    def _should_send(self, camera_id, kind):
        if not settings.NTFY_TOPIC:
            return False
        if self._in_quiet_hours() and kind not in ALWAYS_PUSH:
            return False
        key = (camera_id, kind)
        now = time.time()
        if now - self._last.get(key, 0.0) < settings.NOTIFY_MIN_GAP:
            return False
        self._last[key] = now
        return True

    # -- sending -------------------------------------------------------------
    async def send(self, camera_id, camera_name, kind, detail="",
                   thumb_url=None, force=False):
        """Fire a push. Returns True if it actually went out."""
        if not force and not self._should_send(camera_id, kind):
            return False
        if not settings.NTFY_TOPIC:
            return False

        title, priority, tags = KINDS.get(
            kind, (kind.replace("_", " ").title(), 3, "bell")
        )

        headers = {
            "Title": f"{camera_name}: {title}",
            "Priority": str(priority),
            "Tags": tags,
            "Click": f"{settings.public_url()}/?cam={camera_id}",
        }
        if settings.NTFY_TOKEN:
            headers["Authorization"] = f"Bearer {settings.NTFY_TOKEN}"
        if thumb_url:
            headers["Attach"] = thumb_url

        body = detail or f"{title} on {camera_name}"
        url = f"{settings.NTFY_SERVER}/{settings.NTFY_TOPIC}"

        try:
            client = await self._http()
            r = await client.post(url, content=body.encode("utf-8"),
                                  headers=headers)
            if r.status_code >= 400:
                print(f"[notify] ntfy returned {r.status_code}: {r.text[:200]}")
                return False
            return True
        except Exception as e:
            print(f"[notify] failed: {e}")
            return False

    def send_soon(self, *args, **kwargs):
        """Fire and forget, for use from non-awaiting code paths."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.send(*args, **kwargs))


notifier = Notifier()
