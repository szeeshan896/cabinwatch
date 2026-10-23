#!/usr/bin/env python3
"""
CabinWatch server.

One small always-on service that your cameras dial out to and that you open in
your phone's browser. Because every camera connects *outbound*, cameras and
viewers can sit on completely different networks, with no port forwarding and
nothing exposed on your home or office router.

Deploy target: Railway (or any host that gives you an HTTPS URL).

    uvicorn server.main:app --host 0.0.0.0 --port $PORT
"""

import json
import time
import hmac
import base64
import hashlib
import asyncio
import contextlib

from fastapi import (FastAPI, WebSocket, WebSocketDisconnect, Request,
                     Response, Form, HTTPException)
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse, PlainTextResponse,
                               StreamingResponse)
from fastapi.templating import Jinja2Templates

from . import settings
from . import protocol
from .hub import hub, Camera, Viewer, reaper, STATE_FIELDS
from .notify import notifier
from .evidence import Store
from .schedule import Schedules

schedules = Schedules()

evidence = Store(on_removed=lambda key: hub.forget_evidence(key))

templates = Jinja2Templates(directory="server/templates")

COOKIE = "cabinwatch"

# Commands a viewer may forward to a camera, mapped to the message the camera
# understands. Anything not listed here is rejected.
SIMPLE_COMMANDS = {
    "record":  "record",        # {"on": bool}
    "baby":    "baby_mode",     # {"on": bool}
    "arm":     "arm",           # {"on": bool}
    "noise":   "noise_filter",  # {"on": bool} - drop steady fan noise
    "lens":    "lens",          # {"id": "<device id>"} - switch front/back
    "zoom":    "zoom",          # {"value": 1.0 .. 4.0} - digital zoom
    "rotate":  "rotate",        # {"value": 0|90|180|270} - stand the view up
    "alarm":   "alarm",         # {"on": bool} - siren and torch, as a scare
    "person":  "person_detection",  # {"on": bool} - only alert on people
    "standby": "standby",       # {"on": bool} - sleep until somebody looks
    "ptz":     "ptz",           # {"action": "left"|"right"|"up"|"down"|
                                #  "stop"|"preset"|"save_preset"|"home",
                                #  "speed": 0..1, "preset": "<name>"}
    "config":  "config",        # {"values": {...}}
    "restart": "restart",
}


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    missing = settings.missing_required()
    if missing:
        print("=" * 62)
        print("  WARNING: these environment variables are not set:")
        for m in missing:
            print(f"    - {m}")
        print("  Logins and camera connections are refused until they are.")
        print("  On Railway: your service -> Variables tab.")
        print("=" * 62)
    print(f"[server] public URL     : {settings.public_url()}")
    print(f"[server] notifications  : "
          f"{'on (ntfy)' if settings.NTFY_TOPIC else 'OFF - set NTFY_TOPIC'}")
    print(f"[server] clock           : {settings.now():%H:%M} "
          f"({settings.TIMEZONE})")
    jobs = [asyncio.create_task(reaper()), asyncio.create_task(keeper())]
    try:
        yield
    finally:
        for job in jobs:
            job.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await job
        await notifier.close()


app = FastAPI(title="CabinWatch", lifespan=lifespan)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """A few headers that cost nothing and close off easy mistakes.

    This server shows live pictures of your home. It should not be
    embeddable in somebody else's page, its address should not travel in a
    referrer to wherever a link goes, and once it has been reached over
    https a browser should refuse to go back to plain http.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if _is_https(request):
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000")
    return response


# ---------------------------------------------------------------------------
# Viewer auth: one password, held as a signed cookie. Enough for a private
# family/office tool, and it keeps the feeds off the open internet.
# ---------------------------------------------------------------------------
def _sign(value: str) -> str:
    sig = hmac.new(settings.SECRET_KEY.encode(), value.encode(),
                   hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{value}:{sig}".encode()).decode()


def _verify(token: str) -> bool:
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        value, sig = raw.rsplit(":", 1)
    except Exception:
        return False
    expect = hmac.new(settings.SECRET_KEY.encode(), value.encode(),
                      hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expect):
        return False
    try:
        return float(value) > time.time()
    except ValueError:
        return False


def _is_https(request: Request) -> bool:
    """Did this request arrive over https?

    Railway and anything else behind a proxy terminate TLS in front of us,
    so the scheme we see is http; the header they set is the only honest
    answer. It is only trusted for deciding to make a cookie *safer*.
    """
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").split(",")[0].strip() \
        == "https"


def logged_in(request: Request) -> bool:
    if not settings.VIEW_PASSWORD:
        return False
    return _verify(request.cookies.get(COOKIE, ""))


def require_login(request: Request):
    if not logged_in(request):
        raise HTTPException(status_code=401, detail="not logged in")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, bad: int = 0):
    return templates.TemplateResponse(
        request, "login.html",
        {"bad": bool(bad), "configured": bool(settings.VIEW_PASSWORD)}
    )


@app.post("/login")
async def do_login(request: Request, password: str = Form("")):
    if not settings.VIEW_PASSWORD or not hmac.compare_digest(
            password, settings.VIEW_PASSWORD):
        await asyncio.sleep(1.0)        # slow down guessing
        return RedirectResponse("/login?bad=1", status_code=303)
    expiry = time.time() + settings.SESSION_DAYS * 86400
    resp = RedirectResponse("/", status_code=303)
    # Marked secure wherever the connection is https, so the cookie that
    # opens your cameras is never put on the wire in the clear. Not over
    # plain http, because then the browser would drop it and nobody could
    # log in to a server running locally.
    resp.set_cookie(COOKIE, _sign(f"{expiry:.0f}"),
                    max_age=settings.SESSION_DAYS * 86400,
                    httponly=True, samesite="lax",
                    secure=_is_https(request))
    return resp


@app.post("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not logged_in(request):
        return RedirectResponse("/login")
    return templates.TemplateResponse(
        request, "dashboard.html",
        {"snapshot_every": settings.SNAPSHOT_EVERY,
         "audio_rate": protocol.AUDIO_RATE,
         "focus": request.query_params.get("cam", "")}
    )


@app.get("/camera", response_class=HTMLResponse)
async def camera_page(request: Request):
    """Open this on a spare phone to turn it into a camera."""
    if not logged_in(request):
        return RedirectResponse("/login")
    return templates.TemplateResponse(
        request, "camera.html",
        {"audio_rate": protocol.AUDIO_RATE,
         "chunk_samples": protocol.AUDIO_CHUNK_SAMPLES,
         "agent_token": settings.AGENT_TOKEN}
    )


@app.get("/state")
async def state(request: Request):
    require_login(request)
    return JSONResponse(hub.list_state())


@app.get("/healthz", response_class=PlainTextResponse)
async def healthz():
    return "ok"


@app.get("/event/{eid}/thumb.jpg")
async def event_thumb(eid: int):
    # Deliberately unauthenticated: ntfy fetches this URL to show the picture
    # inside the notification. It carries no camera id and no token, and the
    # id is only valid while the event is still in the recent log.
    jpeg = hub.thumb(eid)
    if jpeg is None:
        raise HTTPException(status_code=404)
    # Private, because this is a photograph of somebody's home and has no
    # business sitting in a shared cache on the way.
    return Response(content=jpeg, media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=86400"})


# ---------------------------------------------------------------------------
# Recordings that live on the camera device
# ---------------------------------------------------------------------------
@app.get("/clips/{cam_id}")
async def list_clips(cam_id: str, request: Request):
    require_login(request)
    cam = hub.get(cam_id)
    if cam is None:
        raise HTTPException(status_code=404, detail="camera not connected")
    items = await cam.request_clips()
    if items is None:
        raise HTTPException(status_code=504,
                            detail="camera did not answer in time")
    return {"camera": cam_id, "clips": items}


@app.get("/clip/{cam_id}/{filename}")
async def get_clip(cam_id: str, filename: str, request: Request):
    """Pull one recording off the device, through the server, to your phone.

    The file is streamed in chunks and never stored here.
    """
    require_login(request)
    cam = hub.get(cam_id)
    if cam is None:
        raise HTTPException(status_code=404, detail="camera not connected")
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="bad filename")

    xfer = await cam.start_transfer(filename)
    if xfer is None:
        raise HTTPException(status_code=504,
                            detail="camera did not start the transfer")

    async def body():
        try:
            while True:
                chunk = await xfer.queue.get()
                if chunk is None:
                    break
                yield chunk
        finally:
            if xfer.xfer_id in cam.transfers:
                cam.transfers.pop(xfer.xfer_id, None)
                await cam.send({"t": "cancel_clip", "xfer": xfer.xfer_id})

    headers = {"Content-Disposition": f'inline; filename="{filename}"'}
    if xfer.size:
        headers["Content-Length"] = str(xfer.size)
    return StreamingResponse(body(), media_type=xfer.content_type,
                             headers=headers)


@app.post("/camera/{cam_id}/forget")
async def forget_camera(cam_id: str, request: Request):
    """Drop a camera that has stopped reporting off the dashboard.

    Only one that is actually gone - a connected camera is removed by
    turning it off, not from here.
    """
    require_login(request)
    if hub.get(cam_id) is not None:
        raise HTTPException(status_code=409,
                            detail="that camera is still connected")
    if not hub.forget(cam_id):
        raise HTTPException(status_code=404, detail="no such camera")
    await hub.broadcast_state()
    return {"ok": True}


# How long the charger has to stay out before we believe it. Phones
# misreport for a moment, and waking somebody at three in the morning over a
# loose cable is worse than telling them three seconds later.
UNPLUG_SETTLE = 3.0


async def _watch_the_power(cam: Camera):
    """Say so when a camera stops being plugged in.

    Pulling the charger is the first thing anybody does to a phone they are
    taking, and it happens long before the battery gets low enough for the
    usual warning - which would arrive hours later, from a phone that is no
    longer in the building.

    A machine with no battery reports nothing here and is left alone.
    """
    now = cam.state.get("charging")

    if now is not False:
        # Plugged in, or a camera that has no opinion about it. Either way
        # this is the state we want it in, so forget what came before.
        if now is True:
            cam._was_plugged = True
        cam._unplugged_since = None
        cam._unplug_told = False
        return

    if not getattr(cam, "_was_plugged", False):
        return          # it was never on charge; nothing has changed

    since = getattr(cam, "_unplugged_since", None)
    if since is None:
        cam._unplugged_since = time.time()
        return
    if time.time() - since < UNPLUG_SETTLE or getattr(cam, "_unplug_told",
                                                      False):
        return

    cam._unplug_told = True
    cam._was_plugged = False
    battery = cam.state.get("battery")
    detail = (f"{cam.name} has been unplugged"
              + (f" and is on {battery}% battery" if battery is not None
                 else "")
              + ". It was charging a moment ago.")
    ev = hub.add_event(cam.cam_id, cam.name, "unplugged", detail)
    await notifier.send(cam.cam_id, cam.name, "unplugged", detail)
    return ev


async def _watch_the_battery(cam: Camera):
    """Keep a camera alive long enough to be useful.

    A phone left watching a room overnight runs its camera, its encoder
    and its microphone the whole time. On battery alone it does not see
    the morning - and a flat phone is worth nothing at all: it cannot be
    woken, it cannot say where it is, it is simply gone.

    So there are two steps before that. A warning while there is still
    time to walk over with a cable, and then standby, where it holds the
    connection and nothing else. It stops recording and stops watching for
    movement - which is the trade, and why it says so plainly - but it can
    still be woken to look, and it will still be there in the morning.
    """
    level = cam.state.get("battery")
    if level is None or cam.state.get("charging"):
        cam._battery_warned = False
        return

    if (level <= settings.STANDBY_BELOW and not cam.state.get("standby")
            and not getattr(cam, "_standby_asked", False)):
        cam._standby_asked = True
        detail = (f"{cam.name} is down to {level}% and not charging, so it "
                  f"has gone to standby. It is no longer recording or "
                  f"watching for movement, but you can still open it to "
                  f"look - and it will still be there in the morning.")
        await cam.send({"t": "standby", "on": True})
        hub.add_event(cam.cam_id, cam.name, "standby", detail)
        await notifier.send(cam.cam_id, cam.name, "standby", detail)
        return

    if level > settings.STANDBY_BELOW:
        cam._standby_asked = False

    if (level <= settings.BATTERY_WARN_BELOW
            and not getattr(cam, "_battery_warned", False)):
        cam._battery_warned = True
        detail = (f"{cam.name} is on battery at {level}% and is not "
                  f"charging. Watching a room is what empties a phone; "
                  f"plug it in or it will not last the night.")
        hub.add_event(cam.cam_id, cam.name, "battery_low", detail)
        await notifier.send(cam.cam_id, cam.name, "battery_low", detail)


async def _keep_a_copy(cam: Camera, filename: str):
    """Pull one clip off a camera and put it in the evidence store."""
    if "/" in filename or "\\" in filename or ".." in filename:
        return
    if evidence.has(cam.cam_id, filename):
        return
    if not hub.wants_copy(cam.cam_id, filename):
        return

    key, part = evidence.begin(cam.cam_id, cam.name, filename)
    xfer = await cam.start_transfer(filename, timeout=30.0)
    if xfer is None:
        print(f"[evidence] {cam.cam_id} did not send {filename}")
        return

    written = 0
    try:
        with open(part, "wb") as out:
            while True:
                chunk = await asyncio.wait_for(xfer.queue.get(), timeout=120)
                if chunk is None:
                    break
                out.write(chunk)
                written += len(chunk)
    except Exception as e:
        print(f"[evidence] {filename} did not arrive: {e}")
        evidence.abandon(part)
        return

    if written < 1024:
        evidence.abandon(part)
        return

    evidence.finish(key, part, cam.cam_id, cam.name, filename)
    print(f"[evidence] kept {filename} from {cam.cam_id} "
          f"({written / 1048576:.1f} MB)")
    hub.note_evidence(cam.cam_id, filename, key)
    await hub.broadcast_state()


async def keeper():
    """Hold every scheduled camera at the state its window asks for.

    Pushed repeatedly rather than once at the boundary, so a camera that
    was off overnight comes back to the right state by itself instead of
    to whatever it was in when it died.
    """
    while True:
        await asyncio.sleep(settings.SCHEDULE_CHECK_SECONDS)
        for cam in list(hub.cameras.values()):
            try:
                wanted = schedules.should_be_armed(cam.cam_id)
                if wanted is None or cam.state.get("armed") == wanted:
                    continue
                await cam.send({"t": "arm", "on": wanted})
            except Exception as e:
                print(f"[schedule] {cam.cam_id}: {e}")


@app.get("/schedule/{cam_id}")
async def get_schedule(cam_id: str, request: Request):
    require_login(request)
    return {"camera": cam_id, "schedule": schedules.describe(cam_id),
            "timezone": settings.TIMEZONE,
            "clock": settings.now().strftime("%H:%M")}


@app.post("/schedule/{cam_id}")
async def set_schedule(cam_id: str, request: Request):
    """Say when you are normally there, so it stops alerting about you."""
    require_login(request)
    body = {}
    with contextlib.suppress(Exception):
        body = await request.json()

    if not body or not body.get("off_from"):
        schedules.clear(cam_id)
        return {"ok": True, "schedule": None}

    try:
        row = schedules.set(cam_id, body.get("off_from"),
                            body.get("off_until"), body.get("days"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Apply it now rather than up to half a minute later.
    cam = hub.get(cam_id)
    wanted = schedules.should_be_armed(cam_id)
    if cam is not None and wanted is not None:
        await cam.send({"t": "arm", "on": wanted})
    await hub.broadcast_state()
    return {"ok": True, "schedule": dict(row, armed_now=wanted)}


@app.get("/saved")
async def list_saved(request: Request, cam: str = ""):
    """What the server is holding its own copy of."""
    require_login(request)
    return {"clips": evidence.list(cam or None),
            "free_mb": evidence.free_mb(),
            "keeping_days": settings.EVIDENCE_DAYS}


@app.get("/saved/{key}")
async def get_saved(key: str, request: Request):
    """Play or download the server's copy, even if the camera is gone."""
    require_login(request)
    path = evidence.path_of(key)
    if path is None:
        raise HTTPException(status_code=404, detail="no such saved clip")
    return FileResponse(path, media_type="video/mp4",
                        filename=key.split("__", 1)[-1])


# ---------------------------------------------------------------------------
# Commands to a camera
# ---------------------------------------------------------------------------
@app.post("/cmd/{cam_id}/{what}")
async def command(cam_id: str, what: str, request: Request):
    require_login(request)
    cam = hub.get(cam_id)
    if cam is None:
        raise HTTPException(status_code=404, detail="camera not connected")

    body = {}
    with contextlib.suppress(Exception):
        body = await request.json()

    if what == "test":
        sent = await notifier.send(
            cam.cam_id, cam.name, "test",
            f"Test push from {cam.name}. If you can read this, alerts work.",
            force=True)
        if not sent:
            raise HTTPException(
                status_code=400,
                detail="No notification sent - set NTFY_TOPIC on the server.")
        return {"ok": True}

    msg_type = SIMPLE_COMMANDS.get(what)
    if msg_type is None:
        raise HTTPException(status_code=400, detail="unknown command")

    payload = {"t": msg_type}
    payload.update({k: v for k, v in body.items() if k != "t"})
    if not await cam.send(payload):
        raise HTTPException(status_code=502, detail="camera did not accept")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Camera websocket - the side that lives on a different network
# ---------------------------------------------------------------------------
def _valid_cam_id(cam_id: str) -> bool:
    return (0 < len(cam_id) <= 64
            and all(ch.isalnum() or ch in "-_" for ch in cam_id))


@app.websocket("/ws/camera")
async def ws_camera(ws: WebSocket):
    token = ws.query_params.get("token", "")
    cam_id = (ws.query_params.get("id") or "").strip()
    name = (ws.query_params.get("name") or "").strip()
    kind = ws.query_params.get("kind", "agent")

    if not settings.AGENT_TOKEN or not hmac.compare_digest(
            token, settings.AGENT_TOKEN):
        await ws.close(code=4401)
        return
    if not _valid_cam_id(cam_id):
        await ws.close(code=4400)
        return

    await ws.accept()
    cam = Camera(cam_id, name or cam_id, ws, kind=kind)
    if not await hub.register(cam):
        return          # the name is in use by a device that is still here
    print(f"[camera] {cam_id} connected ({kind})")

    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break

            data = msg.get("bytes")
            if data:
                cam.last_seen = time.time()
                tag, payload, xfer = protocol.unpack(data)
                if tag == protocol.BIN_VIDEO:
                    await cam.fanout_video(payload)
                elif tag == protocol.BIN_AUDIO:
                    await cam.fanout_audio(payload)
                elif tag == protocol.BIN_CLIP and xfer is not None:
                    await cam.feed_transfer(xfer, payload)
                continue

            text = msg.get("text")
            if not text:
                continue
            cam.last_seen = time.time()
            await _on_camera_message(cam, text)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[camera] {cam_id} error: {e}")
    finally:
        print(f"[camera] {cam_id} disconnected")
        await hub.unregister(cam)


async def _on_camera_message(cam: Camera, text: str):
    try:
        msg = json.loads(text)
    except Exception:
        return
    t = msg.get("t")

    if t == "state":
        for k in STATE_FIELDS:
            if k in msg:
                cam.state[k] = msg[k]
        if msg.get("name"):
            cam.name = msg["name"]
        await _watch_the_power(cam)
        await _watch_the_battery(cam)
        await hub.broadcast_state()

    elif t == "event":
        kind = msg.get("kind", "motion")
        detail = msg.get("detail", "")
        thumb = None
        if msg.get("thumb"):
            with contextlib.suppress(Exception):
                thumb = base64.b64decode(msg["thumb"])
        ev = hub.add_event(cam.cam_id, cam.name, kind, detail, thumb,
                           extra={"clip": msg.get("clip")})
        thumb_url = (f"{settings.public_url()}{ev['thumb']}"
                     if ev.get("thumb") else None)
        await notifier.send(cam.cam_id, cam.name, kind, detail or None,
                            thumb_url=thumb_url)
        await hub.broadcast_state()

    elif t == "clip_ready":
        # The device has finished a clip an alert was raised during, and is
        # offering it. Take a copy, so the footage does not only exist on
        # the thing that might be what gets taken.
        if settings.EVIDENCE and msg.get("event") and msg.get("file"):
            asyncio.create_task(_keep_a_copy(cam, str(msg["file"])))

    elif t == "clips":
        cam.deliver_clips(msg.get("items") or [])
        await hub.broadcast_state()

    elif t == "clip_start":
        xfer = cam.transfers.get(msg.get("xfer"))
        if xfer is not None:
            xfer.size = msg.get("size")
            if msg.get("type"):
                xfer.content_type = msg["type"]
            xfer.ready.set()

    elif t == "clip_end":
        await cam.end_transfer(msg.get("xfer"))

    elif t == "clip_error":
        await cam.end_transfer(msg.get("xfer"),
                               error=msg.get("detail", "failed"))

    elif t == "log":
        print(f"[camera {cam.cam_id}] {str(msg.get('detail'))[:300]}")


# ---------------------------------------------------------------------------
# Viewer websockets
# ---------------------------------------------------------------------------
@app.websocket("/ws/view")
async def ws_view(ws: WebSocket):
    if not _verify(ws.cookies.get(COOKIE, "")):
        await ws.close(code=4401)
        return

    cam_id = ws.query_params.get("cam", "")
    mode = ws.query_params.get("mode", "live")
    if mode not in ("live", "hd", "snapshot"):
        mode = "live"

    await ws.accept()
    viewer = Viewer(ws, mode=mode)
    cam = await hub.attach_viewer(cam_id, viewer)
    if cam is None:
        with contextlib.suppress(Exception):
            await ws.send_json({"t": "error", "detail": "camera not connected"})
            await ws.close()
        return

    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break

            # Binary from a viewer is always talk-back audio for the camera.
            data = msg.get("bytes")
            if data:
                tag, payload, _ = protocol.unpack(data)
                if tag == protocol.BIN_AUDIO and payload:
                    await cam.send_bytes(protocol.pack_audio(payload))
                continue

            text = msg.get("text")
            if not text:
                continue
            with contextlib.suppress(Exception):
                m = json.loads(text)
                if m.get("mode") in ("live", "hd", "snapshot"):
                    viewer.mode = m["mode"]
                    await cam.apply_stream_mode()
                if "listen" in m:
                    viewer.listen = bool(m["listen"])
                    await cam.apply_audio()
                if "talk" in m:
                    # Let the device open its speaker before audio arrives.
                    await cam.send({"t": "talk", "on": bool(m["talk"])})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await hub.detach_viewer(cam_id, viewer)


@app.websocket("/ws/dash")
async def ws_dash(ws: WebSocket):
    """Live status and events for the dashboard, so it never has to poll."""
    if not _verify(ws.cookies.get(COOKIE, "")):
        await ws.close(code=4401)
        return
    await ws.accept()
    hub.add_dash(ws)
    try:
        await ws.send_json(hub.list_state())
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        hub.remove_dash(ws)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server.main:app", host="0.0.0.0", port=settings.PORT)
