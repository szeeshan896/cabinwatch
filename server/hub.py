"""
The hub: keeps track of connected cameras and viewers, and relays everything
between them.

Nothing is stored on disk here. Recordings live on the camera device; the
server only passes media through while you are actually watching or
listening, plus a small in-memory log of recent events with thumbnails.

What flows through the hub
-------------------------
  video    camera -> viewers        only while someone is watching
  audio    camera -> viewers        only while someone taps Listen
  audio    viewer -> camera         while someone holds Talk
  clips    camera -> HTTP response  when you open a recording from the device
  control  viewer -> camera         record, baby mode, PTZ, lens switch
"""

import os
import json
import time
import asyncio
import datetime
from collections import deque

from . import settings
from . import protocol
from .notify import notifier


# Everything a camera may tell us about itself. The server keeps this in one
# place because it used to live here as defaults AND again in main.py as an
# allow-list, and the two drifted apart the first time a field was added.
STATE_FIELDS = (
    "status", "recording", "paused", "motion", "sound", "audio_level",
    "baby_mode", "armed", "disk_free_gb", "clips", "retention_hours",
    "quiet_for_s", "silent_for_s", "moved_ago_s", "battery", "charging",
    "location", "noise_filter", "ptz",
    "ptz_presets", "lenses", "active_lens", "zoom", "rotate", "alarm",
    "has_siren", "has_audio",
    "has_speaker",
)


class ClipTransfer:
    """One recording being pulled off a camera device, chunk by chunk."""

    def __init__(self, xfer_id, filename):
        self.xfer_id = xfer_id
        self.filename = filename
        self.queue = asyncio.Queue(maxsize=64)
        self.started = time.time()
        self.size = None
        self.content_type = "video/mp4"
        self.ready = asyncio.Event()
        self.error = None


class Camera:
    """One connected camera client (a laptop agent or a phone browser)."""

    def __init__(self, cam_id, name, ws, kind="agent"):
        self.cam_id = cam_id
        self.name = name or cam_id
        self.ws = ws
        self.kind = kind                 # "agent" (python) | "browser" (phone)

        self.connected_at = time.time()
        self.last_seen = time.time()

        # Everything the camera tells us about itself.
        self.state = {
            "status": "starting",
            "recording": False,
            "paused": False,
            "motion": False,
            "sound": False,
            "audio_level": 0.0,
            "baby_mode": False,
            "noise_filter": True,
            "armed": True,
            "disk_free_gb": None,
            "clips": 0,
            "retention_hours": None,
            "quiet_for_s": None,         # seconds since last motion
            "silent_for_s": None,        # seconds since last sound
            "moved_ago_s": None,         # seconds since the camera was moved
            "battery": None,             # phone battery percent
            "charging": None,
            "location": None,            # {lat, lon, accuracy_m, at} if known
            "ptz": False,                # camera can pan/tilt
            "ptz_presets": [],
            "lenses": [],                # selectable cameras on the device
            "active_lens": None,
            "zoom": 1.0,
            "rotate": 0,                 # degrees to stand the picture up
            "alarm": False,              # siren and torch going
            "has_siren": False,          # this device can make a noise
            "has_audio": False,
            "has_speaker": False,
        }

        self.last_jpeg = None
        self.last_jpeg_at = 0.0

        self.viewers = set()
        self.stream_mode = None          # None | "live" | "hd" | "snapshot"
        self.audio_on = False            # camera is sending us its mic

        self.transfers = {}              # xfer_id -> ClipTransfer
        self._xfer_seq = 0
        self.clip_list = None            # cached listing from the device
        self.clip_list_at = 0.0
        self._clips_event = asyncio.Event()

        self._send_lock = asyncio.Lock()

    # -- outbound to the camera ---------------------------------------------
    async def send(self, msg):
        try:
            async with self._send_lock:
                await self.ws.send_json(msg)
            return True
        except Exception:
            return False

    async def send_bytes(self, data):
        try:
            async with self._send_lock:
                await self.ws.send_bytes(data)
            return True
        except Exception:
            return False

    def is_online(self):
        return (time.time() - self.last_seen) < settings.AGENT_TIMEOUT

    def describe(self):
        d = dict(self.state)
        d.update({
            "id": self.cam_id,
            "name": self.name,
            "kind": self.kind,
            "online": self.is_online(),
            "streaming": self.stream_mode,
            "listening": self.audio_on,
            "viewers": len(self.viewers),
            "uptime_s": int(time.time() - self.connected_at),
            "last_frame_age": (round(time.time() - self.last_jpeg_at, 1)
                               if self.last_jpeg_at else None),
        })
        return d

    # -- video stream control ------------------------------------------------
    def wanted_mode(self):
        """The heaviest mode any current viewer is asking for."""
        if not self.viewers:
            return None
        modes = {v.mode for v in self.viewers}
        for m in ("hd", "live", "snapshot"):
            if m in modes:
                return m
        return "snapshot"

    async def apply_stream_mode(self):
        """Tell the camera to start, change, or stop sending frames."""
        want = self.wanted_mode()
        if want != self.stream_mode:
            self.stream_mode = want
            if want is None:
                await self.send({"t": "stream", "on": False})
            else:
                await self.send({"t": "stream", "on": True, "mode": want,
                                 **_mode_spec(want)})
        await self.apply_audio()

    async def apply_audio(self):
        """Camera only sends its mic while at least one viewer is listening."""
        want = any(v.listen for v in self.viewers)
        if want != self.audio_on:
            self.audio_on = want
            await self.send({"t": "audio", "on": want,
                             "rate": protocol.AUDIO_RATE})

    # -- fanout --------------------------------------------------------------
    async def fanout_video(self, jpeg: bytes):
        self.last_jpeg = jpeg
        self.last_jpeg_at = time.time()
        if not self.viewers:
            return
        packet = protocol.pack_video(jpeg)
        await self._fanout(packet, want=lambda v: True)

    async def fanout_audio(self, pcm: bytes):
        if not self.viewers:
            return
        packet = protocol.pack_audio(pcm)
        await self._fanout(packet, want=lambda v: v.listen)

    async def _fanout(self, packet, want):
        dead = []
        for v in list(self.viewers):
            if not want(v):
                continue
            if not await v.send(packet):
                dead.append(v)
        for v in dead:
            self.viewers.discard(v)
        if dead:
            await self.apply_stream_mode()

    # -- clip transfers ------------------------------------------------------
    async def request_clips(self, timeout=8.0):
        """Ask the device what recordings it is holding."""
        self._clips_event.clear()
        if not await self.send({"t": "list_clips"}):
            return None
        try:
            await asyncio.wait_for(self._clips_event.wait(), timeout)
        except asyncio.TimeoutError:
            return self.clip_list
        return self.clip_list

    def deliver_clips(self, items):
        self.clip_list = items
        self.clip_list_at = time.time()
        self.state["clips"] = len(items or [])
        self._clips_event.set()

    async def start_transfer(self, filename, timeout=15.0):
        """Ask the device to stream one recording to us."""
        self._xfer_seq = (self._xfer_seq + 1) % 0xFFFFFFFF
        xfer = ClipTransfer(self._xfer_seq or 1, filename)
        self.transfers[xfer.xfer_id] = xfer
        ok = await self.send({"t": "get_clip", "file": filename,
                              "xfer": xfer.xfer_id})
        if not ok:
            del self.transfers[xfer.xfer_id]
            return None
        try:
            await asyncio.wait_for(xfer.ready.wait(), timeout)
        except asyncio.TimeoutError:
            self.transfers.pop(xfer.xfer_id, None)
            return None
        if xfer.error:
            self.transfers.pop(xfer.xfer_id, None)
            return None
        return xfer

    async def feed_transfer(self, xfer_id, chunk):
        xfer = self.transfers.get(xfer_id)
        if xfer is None:
            return
        try:
            await asyncio.wait_for(xfer.queue.put(chunk), timeout=30)
        except asyncio.TimeoutError:
            # Viewer stopped reading - abandon it.
            self.transfers.pop(xfer_id, None)
            await self.send({"t": "cancel_clip", "xfer": xfer_id})

    async def end_transfer(self, xfer_id, error=None):
        xfer = self.transfers.pop(xfer_id, None)
        if xfer is None:
            return
        xfer.error = error
        xfer.ready.set()
        await xfer.queue.put(None)


def _mode_spec(mode):
    if mode == "hd":
        return {"width": settings.HD_WIDTH, "fps": settings.HD_FPS,
                "quality": settings.HD_QUALITY}
    if mode == "live":
        return {"width": settings.LIVE_WIDTH, "fps": settings.LIVE_FPS,
                "quality": settings.LIVE_QUALITY}
    return {"width": settings.LIVE_WIDTH,
            "fps": round(1.0 / max(settings.SNAPSHOT_EVERY, 0.2), 3),
            "quality": settings.LIVE_QUALITY}


class Viewer:
    """One browser tab watching one camera."""

    def __init__(self, ws, mode="live"):
        self.ws = ws
        self.mode = mode
        self.listen = False
        self._lock = asyncio.Lock()
        self._last_frame = 0.0

    async def send(self, packet: bytes):
        # Snapshot viewers get frames at their own slower pace even if the
        # camera is pushing faster for somebody else.
        if packet[:1] == bytes([protocol.BIN_VIDEO]) and self.mode == "snapshot":
            if time.time() - self._last_frame < settings.SNAPSHOT_EVERY * 0.9:
                return True
        try:
            async with self._lock:
                await self.ws.send_bytes(packet)
            if packet[:1] == bytes([protocol.BIN_VIDEO]):
                self._last_frame = time.time()
            return True
        except Exception:
            return False


class Hub:
    def __init__(self):
        self.cameras = {}
        self.events = deque(maxlen=settings.EVENT_HISTORY)
        self.thumbs = {}
        self._event_seq = 0
        self._events_path = settings.EVENT_LOG
        self._events_dirty = False
        self._load_events()
        self._dash_clients = set()
        self._conflicts = {}             # cam_id -> when we last complained

        # Cameras that have been here and are not here now. Without this a
        # camera that stops simply vanishes off the dashboard, and the one
        # question you actually have - when did it stop? - has nowhere to be
        # answered. Kept in memory, so a server restart clears it.
        self.offline = {}                # cam_id -> last known description

    # -- cameras -------------------------------------------------------------
    # A camera that is still talking to us has not gone anywhere. Every client
    # pushes its state every couple of seconds, so anything quieter than this
    # is a socket the other end has already walked away from.
    STILL_THERE = 10.0

    async def register(self, camera: Camera):
        """Take a camera on. False means it was refused - see below."""
        old = self.cameras.get(camera.cam_id)

        if old is not None and old is not camera:
            if time.time() - old.last_seen < self.STILL_THERE:
                # Not a reconnection: the one already here is alive and
                # talking. This is a second device set up under the same
                # name, and letting it in starts a fight nobody can see -
                # each one knocks the other off, over and over, and their
                # pictures and alerts land under one name on the dashboard.
                await self._refuse(camera, old)
                return False

            # A camera coming back (restart, new IP, phone woke up). Drop the
            # stale socket but hand its viewers to the fresh connection so the
            # dashboard does not go blank.
            camera.viewers = old.viewers
            old.viewers = set()
            try:
                await old.ws.close()
            except Exception:
                pass

        self.cameras[camera.cam_id] = camera
        self.forget_offline(camera.cam_id)
        await camera.apply_stream_mode()

        if old is None:
            self.add_event(camera.cam_id, camera.name, "online",
                           f"{camera.name} connected")
            await notifier.send(camera.cam_id, camera.name, "online",
                                f"{camera.name} is connected and watching.")
        await self.broadcast_state()
        return True

    async def _refuse(self, newcomer: Camera, holder: Camera):
        """Turn away a second device using a name that is already taken."""
        detail = (f"Two devices are both set up as \"{holder.name}\". The "
                  f"second one was refused so this one keeps working - give "
                  f"it a different name and it will connect.")
        print(f"[camera] refused {newcomer.cam_id}: name already in use by a "
              f"device that is still connected")
        try:
            await newcomer.ws.close(code=4409, reason="name already in use")
        except Exception:
            pass

        # Say it once in a while, not on every retry - the refused device
        # will keep knocking until somebody renames it.
        now = time.time()
        if now - self._conflicts.get(holder.cam_id, 0) < 300:
            return
        self._conflicts[holder.cam_id] = now
        self.add_event(holder.cam_id, holder.name, "conflict", detail)
        await notifier.send(holder.cam_id, holder.name, "conflict", detail)
        await self.broadcast_state()

    async def unregister(self, camera: Camera):
        if self.cameras.get(camera.cam_id) is camera:
            del self.cameras[camera.cam_id]
            self.remember_offline(camera)
            for xfer_id in list(camera.transfers):
                await camera.end_transfer(xfer_id, error="camera disconnected")
            self.add_event(camera.cam_id, camera.name, "offline",
                           f"{camera.name} disconnected")
            await notifier.send(
                camera.cam_id, camera.name, "offline",
                f"{camera.name} stopped reporting. Check its power, "
                f"internet or battery."
            )
            await self.broadcast_state()

    def get(self, cam_id):
        return self.cameras.get(cam_id)

    # -- cameras that are not here any more -----------------------------------
    def remember_offline(self, camera: Camera):
        """Keep a camera on the dashboard after it stops reporting."""
        last = camera.describe()
        last.update({
            "online": False,
            "status": "offline",
            "streaming": None,
            "listening": False,
            "viewers": 0,
            "recording": False,
            "motion": False,
            "sound": False,
            "audio_level": 0.0,
            "last_seen": camera.last_seen,
        })
        self.offline[camera.cam_id] = last

    def forget_offline(self, cam_id):
        self.offline.pop(cam_id, None)

    def forget(self, cam_id):
        """Drop a camera from the dashboard for good, at the viewer's say-so."""
        return self.offline.pop(cam_id, None) is not None

    def list_state(self):
        cams = [c.describe() for c in self.cameras.values()]
        now = time.time()
        for cam_id, last in self.offline.items():
            if cam_id in self.cameras:
                continue
            gone = dict(last)
            gone["offline_for_s"] = int(now - (last.get("last_seen") or now))
            cams.append(gone)
        cams.sort(key=lambda c: c["name"].lower())
        return {
            "cameras": cams,
            "events": list(self.events)[-80:][::-1],
            "server_time": time.time(),
            "notifications": bool(settings.NTFY_TOPIC),
        }

    # -- viewers -------------------------------------------------------------
    async def attach_viewer(self, cam_id, viewer: Viewer):
        cam = self.cameras.get(cam_id)
        if cam is None:
            return None
        cam.viewers.add(viewer)
        await cam.apply_stream_mode()
        # Give the new viewer something to look at immediately.
        if cam.last_jpeg and time.time() - cam.last_jpeg_at < 30:
            await viewer.send(protocol.pack_video(cam.last_jpeg))
        return cam

    async def detach_viewer(self, cam_id, viewer: Viewer):
        cam = self.cameras.get(cam_id)
        if cam is None:
            return
        cam.viewers.discard(viewer)
        await cam.apply_stream_mode()

    # -- events --------------------------------------------------------------
    def add_event(self, cam_id, cam_name, kind, detail="", thumb=None,
                  extra=None):
        self._event_seq += 1
        eid = self._event_seq
        ev = {
            "id": eid,
            "camera": cam_id,
            "camera_name": cam_name,
            "kind": kind,
            "detail": detail,
            # Only the moment it happened goes out. The server runs in
            # whatever timezone its host feels like - UTC on Railway - and
            # showing that to someone in another one is just a wrong clock.
            # Whoever is looking formats it against their own.
            "at": time.time(),
            "thumb": f"/event/{eid}/thumb.jpg" if thumb else None,
        }
        if extra:
            ev.update({k: v for k, v in extra.items() if v is not None})
        self.events.append(ev)
        self._events_dirty = True
        self.save_events()

        if thumb:
            self.thumbs[eid] = thumb
            live = {e["id"] for e in self.events}
            for k in list(self.thumbs):
                if k not in live:
                    del self.thumbs[k]
        return ev

    def wants_copy(self, cam_id, filename):
        """Is this clip worth keeping a copy of here?

        Decided by what was raised while it was being written. Ordinary
        movement is left off that list by default: on a camera watching a
        room somebody is in, it would fill the store inside an hour and
        push out the one clip that mattered.
        """
        for ev in self.events:
            if (ev.get("camera") == cam_id and ev.get("clip") == filename
                    and ev.get("kind") in settings.EVIDENCE_KINDS):
                return True
        return False

    def forget_evidence(self, key):
        """A saved copy has been dropped; stop offering a link to it."""
        link = f"/saved/{key}"
        for ev in self.events:
            if ev.get("saved") == link:
                ev.pop("saved", None)

    def note_evidence(self, cam_id, filename, key):
        """Mark the events that this clip covers as having a kept copy.

        An alert points at the clip that was being written when it fired,
        so once that clip is safely here, every event waiting on it can say
        so - and can still be watched after the camera itself is gone.
        """
        for ev in self.events:
            if ev.get("camera") == cam_id and ev.get("clip") == filename:
                ev["saved"] = f"/saved/{key}"

    # -- the history ---------------------------------------------------------
    # Events lived only in memory, so every redeploy wiped the lot - and on
    # Railway a redeploy is how you change anything at all. The pictures are
    # left out on purpose: they are the bulky part, and knowing that
    # something happened outlives being able to see a thumbnail of it.
    def _load_events(self):
        if not self._events_path:
            return
        try:
            with open(self._events_path) as f:
                rows = json.load(f)
        except FileNotFoundError:
            return
        except Exception as e:
            print(f"[events] could not read the history: {e}")
            return

        for ev in rows[-settings.EVENT_HISTORY:]:
            ev.pop("thumb", None)           # the picture did not survive
            self.events.append(ev)
            try:
                self._event_seq = max(self._event_seq, int(ev.get("id", 0)))
            except (TypeError, ValueError):
                pass
        if self.events:
            print(f"[events] carried {len(self.events)} events over")

    def save_events(self):
        """Write the history out. Small enough to do on every event."""
        if not self._events_path or not self._events_dirty:
            return
        self._events_dirty = False
        try:
            folder = os.path.dirname(os.path.abspath(self._events_path))
            os.makedirs(folder, exist_ok=True)
            tmp = self._events_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump([{k: v for k, v in ev.items() if k != "thumb"}
                           for ev in self.events], f)
            os.replace(tmp, self._events_path)
        except Exception as e:
            print(f"[events] could not write the history: {e}")

    def thumb(self, eid):
        return self.thumbs.get(eid)

    # -- dashboard live updates ---------------------------------------------
    def add_dash(self, ws):
        self._dash_clients.add(ws)

    def remove_dash(self, ws):
        self._dash_clients.discard(ws)

    async def broadcast_state(self):
        if not self._dash_clients:
            return
        payload = self.list_state()
        dead = []
        for ws in list(self._dash_clients):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._dash_clients.discard(ws)


hub = Hub()


async def reaper():
    """Notice cameras that stopped reporting without closing their socket."""
    while True:
        await asyncio.sleep(10)
        for cam in list(hub.cameras.values()):
            if not cam.is_online():
                print(f"[hub] {cam.cam_id} timed out")
                try:
                    await cam.ws.close()
                except Exception:
                    pass
                await hub.unregister(cam)
        await hub.broadcast_state()
