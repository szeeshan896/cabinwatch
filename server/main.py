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
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse,
                               PlainTextResponse, StreamingResponse)
from fastapi.templating import Jinja2Templates

from . import settings
from . import protocol
from .hub import hub, Camera, Viewer, reaper, STATE_FIELDS
from .notify import notifier

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
    task = asyncio.create_task(reaper())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await notifier.close()


app = FastAPI(title="CabinWatch", lifespan=lifespan)


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
async def do_login(password: str = Form("")):
    if not settings.VIEW_PASSWORD or not hmac.compare_digest(
            password, settings.VIEW_PASSWORD):
        await asyncio.sleep(1.0)        # slow down guessing
        return RedirectResponse("/login?bad=1", status_code=303)
    expiry = time.time() + settings.SESSION_DAYS * 86400
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(COOKIE, _sign(f"{expiry:.0f}"),
                    max_age=settings.SESSION_DAYS * 86400,
                    httponly=True, samesite="lax")
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
    return Response(content=jpeg, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=86400"})


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
    await hub.register(cam)
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
