# CabinWatch

A CCTV system built out of the devices you already own.

Spare Android phones, an old iPhone, a laptop you no longer use, a USB webcam,
or a proper PTZ camera — each becomes a camera you can watch from your own
phone, from anywhere, over the internet.

It was built for two real problems:

- **A baby who climbs out of bed.** You get a push notification the moment he
  makes a sound, you can see at a glance whether he has been still for twenty
  minutes or is moving right now, and you can speak to him through the camera
  without walking into the room.
- **Things going missing from a cabin.** Recording runs around the clock on the
  device itself, movement caught on camera wakes your phone, and the footage is
  still there afterwards even if the internet was down at the time.

---

## The two things that make it different

**Recordings stay on the device that filmed them.** Video is written to that
phone's or laptop's own storage, not uploaded. A power cut at the cabin or an
internet outage does not cost you the evidence, and your hosting bill does not
grow with your footage. When you want a specific clip, you open it from the
dashboard and only that one clip is fetched.

**Every camera can be on a different network.** Cameras dial *out* to the
server, the way a phone app does. So the camera in your baby's room, the one in
your cabin across town, and your own phone can all be on different WiFi or
mobile data, with nothing forwarded on any router and nothing exposed to the
internet.

```
  Baby's room (network A)      Cabin (network B)        You (network C)
  ┌───────────────────┐        ┌───────────────┐        ┌──────────────┐
  │ phone or laptop   │        │ phone/laptop  │        │ your phone   │
  │ • records locally │        │ • records     │        │ • live view  │
  │ • hears crying    │        │ • sees people │        │ • talk back  │
  │ • sees movement   │        │               │        │ • alerts     │
  └─────────┬─────────┘        └───────┬───────┘        └──────▲───────┘
            │  outbound                │  outbound             │
            └──────────────► S E R V E R ◄────────────────────┘
                        (Railway - small and stateless)
```

---

## What you get

| | |
|---|---|
| **Live view** | Off / Snap / Live / HD per camera. Nothing is sent while it is Off. |
| **Push alerts** | On your phone, with a picture of what happened. Free, via ntfy. |
| **Baby sounds** | Tells a cry apart from a fan, and a thud apart from both. |
| **"Still for 23 min"** | One glance tells you whether he is asleep or moving. |
| **Two-way talk** | Hear the room, and speak into it from your phone. |
| **24×7 recording** | On the device, oldest deleted automatically. |
| **Event clips kept longer** | The minute something happened survives days after the ordinary footage is recycled. |
| **Pan / tilt** | Arrow pad and saved positions for rotating cameras. |
| **Dual cameras** | Switch between a phone's front, back and wide lenses remotely. |
| **Digital zoom** | For phones, which have no motor. |
| **Person detection** | For the cabin, so a ceiling fan does not wake you at 3am. |
| **Ignore regions** | Mask out a fan or a window onto the street. |
| **Alerts on/off** | Disarm a camera when you are in the room yourself. |
| **Device health** | Battery, charging state, free space, low-battery warning. |
| **Clip browser** | List, play and save any recording straight off the device. |

---

## Part 1 — Put the server up on Railway

The server is small and holds no video, so the cheapest plan is plenty.

1. Push this repository to your own GitHub account.
2. On [railway.app](https://railway.app): **New Project → Deploy from GitHub
   repo**, and pick it. Railway detects Python and starts it for you.
3. Open your service → **Variables**, and add these:

   | Variable | Value |
   |---|---|
   | `AGENT_TOKEN` | A long random string. Every camera must present this. |
   | `VIEW_PASSWORD` | The password you will type in your browser. |
   | `SECRET_KEY` | Another long random string. Keeps you logged in across redeploys. |
   | `NTFY_TOPIC` | Your private alert channel — see Part 2. |

   Generate the random ones with:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

4. **Settings → Networking → Generate Domain.** You get a URL like
   `https://cabinwatch-production.up.railway.app`. That is your server.
5. Open it in your phone's browser and log in with `VIEW_PASSWORD`.
   Add it to your home screen so it behaves like an app.

Optional variables, all with sensible defaults:

| Variable | Default | What it does |
|---|---|---|
| `LIVE_WIDTH` / `LIVE_FPS` | 480 / 4 | Normal live quality. Raise for a sharper picture and more data. |
| `HD_WIDTH` / `HD_FPS` | 1280 / 12 | What the HD button switches to. |
| `SNAPSHOT_EVERY` | 2 | Seconds between stills in Snap mode. |
| `NOTIFY_MIN_GAP` | 90 | Minimum seconds between two alerts of the same kind. |
| `QUIET_FROM` / `QUIET_TO` | off | Hours in which only serious alerts push, e.g. `23` and `7`. A cry, a thud or a camera dropping offline always gets through. |
| `AGENT_TIMEOUT` | 45 | Seconds of silence before a camera is called offline. |

---

## Part 2 — Alerts on your phone

1. Install **ntfy** — it is free, needs no account, and works on both iPhone
   and Android.
2. Think of a topic name nobody could guess, for example
   `cabinwatch-7f3a91c2e84b`. **Anyone who knows a topic name can read it**, so
   make it long and random.
3. In the ntfy app: **Subscribe to topic**, and enter that name.
4. Put the same name in Railway as `NTFY_TOPIC`, and redeploy.
5. On the dashboard, press **Test alert** on any camera. It should land on your
   phone within a second.

In the ntfy app, turn that topic's priority up so a cry or a thud rings even
when your phone is on silent. Tapping the notification opens that camera.

---

## Part 3 — A spare phone as a camera

This needs no app install and works on Android and iPhone.

1. On your dashboard, press **+ Phone** (or go to `/camera`).
2. Open that same page **on the spare phone** and log in.
3. Name it ("Baby Room"), pick which lens to use, choose what it is watching,
   and how much of the phone's storage it may use.
4. Press **Start camera** and allow camera and microphone access.
5. Plug the phone in to charge, and place it where it can see the bed.
6. Press **Dim screen**. The screen goes black but the camera keeps running, so
   the phone is not glowing in a dark room. Tap to bring it back.

Leave that browser tab open and in the foreground. A phone browser stops the
camera if you switch apps, so give the phone this one job.

A phone's own storage is the limit for how far back it can record, so for a room
you care most about, a laptop is the stronger camera — more space, better
microphone, and it keeps running with the lid closed.

---

## Part 4 — A laptop as a camera

Needs Python 3.10 or newer on that laptop.

```bash
pip install -r agent/requirements.txt
cp agent/config_example.py agent/config.py       # Windows: copy
```

See what the machine can actually use:

```bash
python agent/agent.py --list-cameras
```

Edit `agent/config.py`: put in your `SERVER_URL`, the same `AGENT_TOKEN` as the
server, and your cameras. Then:

```bash
python agent/agent.py
```

On Windows, double-click **`agent\run.bat`** instead — it installs what it needs
and restarts the agent if it ever stops. On Linux or a Raspberry Pi use
`agent/run.sh`.

`agent/config.py` is gitignored, so your token and camera passwords never leave
that machine.

---

## Part 5 — A rotating (PTZ) camera

Works with the gimbal webcam you already have, and with any outdoor PTZ camera
you buy later.

**If you are buying one, the thing to check is that the spec sheet says
ONVIF and RTSP.** Cameras that only work inside their maker's own app cannot be
connected to anything else. Also worth having: a microSD slot, so the camera
records inside itself too, and IP65 or better if it is going outdoors.

A PTZ camera cannot dial out to the server by itself — it only sits on your
local network. So one machine on that same network runs the agent and bridges
it: your laptop, or a Raspberry Pi left plugged in. One agent can bridge several
cameras at once.

```
  Your home WiFi                                      Internet
    PTZ camera ──RTSP──► agent (laptop / Pi) ──outbound──► server ──► your phone
         ▲                  • records locally
         └──── ONVIF PTZ ───┘ • detects people
```

In `agent/config.py`, give the camera its RTSP address and one `ptz` block.
There are three kinds and the file has a ready example of each:

- **`onvif`** — nearly every camera sold as "PTZ" or "360". Try this first.
- **`uvc`** — a USB webcam whose motor appears as a standard camera control.
- **`http`** — a cheap camera with its own web commands; you paste the URLs.

For ONVIF you also need one extra package:

```bash
pip install onvif-zeep
```

Arrow buttons then appear over that camera's picture on the dashboard, along
with any positions the camera has saved ("Gate", "Door").

---

## Using it

Each camera card has four view modes. **Off** means the camera sends nothing at
all — that is the normal state, and it is what keeps both the phone's battery
and the hosting bill down. **Snap** is one still every couple of seconds and is
usually all you need to check on a sleeping child. **Live** is smooth video at
low resolution. **HD** is for when you need to recognise a face.

The line at the top right of each card is the quickest read you have:
*still for 23 min* means nobody has moved in that room for 23 minutes.

**Listen** opens the camera's microphone. **Talk** sends your voice out of the
camera's speaker. **Baby** turns sound alerts on for that camera. **Alerts**
disarms it when you are in the room yourself. **Clips** lists everything the
device has recorded, to play or to save.

### What each alert means

| Alert | What set it off |
|---|---|
| **Baby awake** | A sound in the voice range, held for more than a second. |
| **Thud / fall** | A sudden loud low-frequency bang. Check immediately. |
| **Movement** | Something moved in view. |
| **Person seen** | Movement that was also person-shaped — used for the cabin. |
| **Sound** | Continuous noise that was not clearly a cry. |
| **Camera offline** | It stopped reporting — power, internet or battery. |

Cries, thuds, people and a camera going offline get through quiet hours. The
rest are held back.

### Keeping the data bill small

Video only moves while you are actually looking. At the defaults, Live costs
roughly 150–200 MB per hour of watching and Snap is a small fraction of that;
HD is several times more, so use it in bursts. Closing the dashboard tab, or
even switching away from it, stops the stream by itself.

---

## Privacy

- `config.py` and `agent/config.py` hold your tokens, camera passwords and IP
  addresses. Both are gitignored and stay on your machines.
- Recordings never leave the device unless you open a specific clip.
- The server keeps no video. It holds recent event thumbnails in memory only,
  and a restart clears them.
- The dashboard needs your password. Cameras need the token. Neither is
  reachable without one.
- A laptop's webcam light comes on whenever its camera is in use — that is the
  hardware, not software, and cannot be turned off. For a discreet angle use a
  phone with the screen dimmed.

---

## If something is not working

**A camera never appears.** Look at the agent's output. "REFUSED: the server did
not accept the token" means `AGENT_TOKEN` differs between `agent/config.py` and
Railway. On a phone, the page must be open in the foreground.

**The picture is black on a phone camera.** Another app has the camera. Close
the camera app and reload the page.

**Alerts never arrive.** Press **Test alert**. If nothing lands, `NTFY_TOPIC` is
not set on Railway, or the ntfy app is subscribed to a different topic.

**Too many false alarms.** Turn on `person_detection` for that camera, add
`ignore_regions` over the fan or window, or raise `motion_min_area`.

**It misses the baby waking.** Raise `sound_sensitivity` above 1.0 and lower
`motion_min_area`. The microphone matters more than the camera here — a laptop
usually hears better than a phone across a room.

**Pan and tilt do nothing.** Confirm the camera really is ONVIF, and try port
`2020` or `8000` as well as `80`. Some cameras need RTSP or ONVIF switched on in
their own app first — on TP-Link Tapo you create a "Camera Account".

---

## Also in this repo

`legacy/` holds the first version: a single-machine CCTV where every camera has
to be on the same WiFi and recording happens on the machine running it. It still
works and needs no server at all, so it is worth keeping if you ever want
something on one network with nothing else involved.

```bash
pip install -r legacy/requirements.txt
cp legacy/config_example.py legacy/config.py
python legacy/cctv.py
```

## Built with

Python · FastAPI · OpenCV · WebSockets, and the browser's own camera APIs for
the phone cameras. No paid services beyond the hosting.
