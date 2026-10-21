# How it works

Notes for changing the code later.

## The shape of it

Three parts:

- **server/** — a stateless FastAPI app. Keeps no video and no database. It
  holds a registry of connected cameras, relays media between cameras and
  viewers, and sends push notifications. Restarting it loses only the recent
  event log.
- **agent/** — runs on a machine with a camera. Captures, detects, records to
  local disk, and talks to the server.
- **server/templates/camera.html** — the same agent role, written in the
  browser, so a phone needs no app installed.

The agent connects outbound. That is the whole reason cameras can be on
different networks: no inbound port, no public exposure, and it works behind
mobile data and carrier NAT.

## One socket per camera

Everything for one camera goes over a single WebSocket at `/ws/camera`,
authenticated by `AGENT_TOKEN` in the query string.

Text messages are JSON control messages. Binary messages are tagged by their
first byte (`server/protocol.py`, mirrored in the JS and in `agent/agent.py`):

| Tag | Payload | Direction |
|---|---|---|
| `0x01` | JPEG frame | camera → server → viewers |
| `0x02` | PCM16LE mono @ 16 kHz | both ways |
| `0x03` | `uint32` transfer id + file chunk | camera → server → HTTP response |

Raw PCM rather than a codec is deliberate: the browser and Python can both
produce and consume it with nothing extra installed. At 16 kHz mono it is about
32 KB/s, which is fine for voice and only flows while someone is listening or
talking.

## Media flows only on demand

The server tracks who is watching each camera and tells the camera the heaviest
mode any viewer wants — `hd`, `live`, `snapshot`, or off. A camera with no
viewers encodes nothing. This is the single biggest lever on both hosting
bandwidth and a phone camera's battery, so keep it intact when adding features.

Audio is separate: the camera's microphone only streams while at least one
viewer has pressed Listen (`Camera.apply_audio`).

## Recordings stay put

`agent/recorder.py` writes one file per minute into `clips/<camera id>/`. A
janitor thread deletes the oldest files once they pass the retention window or
the disk budget.

Event clips are the subtlety. When movement or a sound fires, the minute it
happened in is added to a keep-set persisted in `keep.json`, and those files get
`EVENT_RETENTION_HOURS` instead of `RETENTION_HOURS`. Under disk pressure,
ordinary clips are deleted before event clips (`_sweep` sorts by `(keep, mtime)`).
So the boring hours recycle while the moment someone walked in survives.

The phone does the same thing in IndexedDB, with `MediaRecorder` stopped and
restarted each minute so every stored segment is an independently playable file.

## Fetching a clip across networks

The file is on a device the server cannot reach. So:

1. Browser requests `GET /clip/{cam}/{file}`
2. Server allocates a transfer id, asks the camera for it, and returns a
   `StreamingResponse`
3. Camera streams `0x03` frames; the server pushes each chunk into an
   `asyncio.Queue` that the response body drains
4. If the reader goes away, the server sends `cancel_clip` and the camera stops

Nothing is buffered to disk on the server. The agent paces itself and the
browser agent backs off on `ws.bufferedAmount`, so a download never starves the
live view.

## Deciding what counts as an event

Motion alone is a bad alarm — fans, curtains, headlights and sunlight all
trigger it. `agent/detect.py` therefore does three things:

- **Movement** is measured on a blurred 320×240 frame, with optional masked-out
  rectangles. `motion_min_area` is specified for a full-size frame and scaled to
  the work image, so one config value behaves the same on any camera.
- **People** are checked only on frames that already showed movement, so the
  cost is near zero while the room is still. OpenCV's built-in HOG detector
  needs no download; MobileNet-SSD is used instead if the files are configured.
- **Sound** is compared against a baseline the detector learns from the quiet
  stretches, then classified by where its energy sits: a sustained voice-band
  sound is a cry, a sudden low-frequency bang is a thud, anything else held long
  enough is just noise. This is what keeps a ceiling fan from alerting all night
  — it is low-frequency but quiet and steady, so it fails both tests.

Thresholds are relative, not absolute, so a noisy room and a silent nursery both
end up sensible without hand-tuning.

## Pan and tilt

`agent/ptz.py` has three backends behind one interface — `onvif`, `uvc`,
`http` — chosen per camera in config. The dashboard only ever sees
`ptz: true/false` and a list of preset names, so adding a fourth backend needs
no UI change.

Continuous-move cameras keep turning until told to stop, so `AutoStop` cancels
the move after two seconds. Without it, one dropped network packet leaves the
camera spinning.

## Auth

Two separate secrets, deliberately:

- `AGENT_TOKEN` — cameras. Compared with `hmac.compare_digest`. A bad token is
  refused during the WebSocket handshake, before `accept()`, so no session is
  ever established. The agent reads the resulting HTTP 403 and prints the
  reason, because otherwise the only symptom is a silent reconnect loop.
- `VIEW_PASSWORD` — you. Exchanged for an HMAC-signed expiry cookie, checked on
  every page, command and socket.

Event thumbnails at `/event/{id}/thumb.jpg` are the one unauthenticated route:
ntfy's servers fetch that URL to show the picture inside the notification. The
URL carries no camera id and no token, and the id only works while the event is
still in the recent in-memory log.

## Things worth knowing before you change it

- `AUDIO_RATE` appears in `server/protocol.py`, the two templates and
  `agent/agent.py`. Change all four together.
- A phone browser stops the camera when the tab goes to the background. The
  wake lock and the dim-screen cover are what keep a phone camera alive
  overnight; do not replace the cover with something that hides the page.
- Railway's filesystem is ephemeral. Nothing on the server should ever assume a
  file survives a deploy — which is exactly why recordings live on the devices.
- `Camera.apply_stream_mode` is called on every viewer attach and detach. If you
  add a new viewer-side preference, route it through there so a camera is never
  left streaming to nobody.
