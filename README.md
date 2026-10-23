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
| **Two ways to use a phone** | A browser page with nothing to install, or a native Android app that runs in the background with the screen off. |
| **A laptop needs no setup either** | A Windows `.exe` that asks a handful of questions the first time. No Python, no editing files. |
| **Push alerts** | On your phone, with a picture of what happened. Free, via ntfy. |
| **Baby sounds** | Tells a cry apart from a fan, and a thud apart from both. |
| **"Still for 23 min"** | One glance tells you whether he is asleep or moving. |
| **Two-way talk** | Hear the room, and speak into it from your phone. |
| **24×7 recording** | On the device, with sound, oldest deleted automatically. |
| **Settings without a console** | The laptop agent serves a settings page on that machine: live preview, microphone meter, device pickers. |
| **Event clips kept longer** | The minute something happened survives days after the ordinary footage is recycled. |
| **Pan / tilt** | Arrow pad and saved positions for rotating cameras. |
| **Dual cameras** | Switch between a phone's front, back and wide lenses remotely. |
| **Digital zoom** | For phones, which have no motor. |
| **Person detection** | For the cabin, so a ceiling fan does not wake you at 3am. |
| **Ignore regions** | Mask out a fan or a window onto the street. |
| **Alerts on/off** | Disarm a camera when you are in the room yourself. |
| **Device health** | Battery, charging, free space, and an alert before it dies. |
| **Tamper alarm** | If a camera is moved, turned or covered, you are told - the phone feels it, every other camera sees its whole view change and stay changed. |
| **Where a phone is** | Its position on the card, if it is carried off. |
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

There are two ways, and they are both worth having.

| | Browser page | Android app |
|---|---|---|
| To set up | nothing to install | install an APK |
| Works on | Android and iPhone | Android only |
| App in the background | ❌ camera stops | ✅ keeps going |
| Screen off | ❌ | ✅ |
| After the phone restarts | ❌ open it again | ✅ comes back by itself |
| Ready in | about 30 seconds | a few minutes |

Use the **browser page** when you want a camera right now, or on an iPhone.
Use the **app** for the cameras you actually depend on — a child's room
overnight, or a cabin you are not in.

### 3a. The browser page — nothing to install

1. On your dashboard, press **+ Phone** (or go to `/camera`).
2. Open that same page **on the spare phone** and log in.
3. Name it ("Baby Room"), pick which lens to use, choose what it is watching,
   and how much of the phone's storage it may use.
4. Press **Start camera** and allow camera and microphone access.
5. Plug the phone in to charge, and place it where it can see the bed.
6. Press **Dim screen**. The screen goes black but the camera keeps running, so
   the phone is not glowing in a dark room. Tap to bring it back.

Leave that browser tab open and in the foreground. A phone browser stops the
camera if you switch apps, so give the phone this one job — or use the app
below, which does not have that problem.

### 3b. The Android app — runs in the background

Spare Android phones are better served by the app. It uses a foreground
service with the camera and microphone types, which is what lets Android keep
the camera running while the app is in the background and the screen is off.

**Get the APK.** You do not need Android Studio. Every push builds one:

1. Go to the **Actions** tab of your GitHub repository.
2. Open the newest **Build Android APK** run.
3. Download the **cabinwatch-camera-apk** artifact and unzip it.
4. Put `app-debug.apk` on the phone and open it. Android will ask you to
   allow installing from outside the Play Store — that is expected for an app
   you built yourself.

If you would rather build it locally, you need Android Studio or the command
line tools, then:

```bash
cd android
./gradlew assembleDebug
# app/build/outputs/apk/debug/app-debug.apk
```

**Set it up on the phone.**

1. Open CabinWatch and fill in your server address and the same `AGENT_TOKEN`.
2. Name the camera, pick which lens, and set how long to keep recordings and
   how much storage the app may use.
3. Turn on **Watching a baby** if it should alert on sounds as well as
   movement.
4. Press **Start camera**, and allow camera, microphone and notifications.
5. Press **Stop Android sleeping this app** when it offers. Do not skip this —
   without it most phones put the app to sleep after a few hours and recording
   quietly stops.
6. Close the app. A small notification stays in the shade while it runs; the
   camera carries on with the screen off.

**Sound in the saved files** is a switch, and it is off on purpose. Android
will not reliably hand the microphone to the recorder and to the alert
detector at the same time, so turning it on can cost you the crying alerts and
the Listen button. Video-only recordings keep everything working, and for
knowing what happened the picture is what matters.

(The laptop agent has no such problem — its recordings always have sound, and
the same open microphone serves the recording, the alerts and the Listen
button at once.)

If a phone cannot record and stream at the same time — a few older ones
cannot — the app falls back to watching only and says so on the dashboard,
rather than looking healthy while saving nothing.

### What about an iPhone as a camera?

It cannot be a 24×7 camera, and that is Apple's rule rather than a gap here:
iOS stops camera capture as soon as an app goes to the background, and there
is no permission that changes it. An iPhone can be a camera while the page is
open in front of you, which is useful for a quick look, but for anything
unattended use an Android phone or a laptop. An iPhone makes a perfectly good
*viewer*, which is the job most people want it for anyway.

## Part 4 — A laptop as a camera

A laptop is the strongest camera of the three: more storage than a phone, a
better microphone, and it keeps running with the lid closed. For a room where
hearing matters — a child's room — it is the one to use.

Two ways again: a ready-made `.exe` that needs nothing installed, or Python if
you would rather have the full config file.

### 4a. The .exe — nothing to install

**Get it.** Same place as the APK — every push builds one:

1. **Actions** tab of your GitHub repository.
2. Newest **Build apps** run.
3. Download the **cabinwatch-laptop-agent-exe** artifact and unzip it.

**Run it.** Double-click `CabinWatch-Agent.exe`. The first time, it asks:

- your server address and the same `AGENT_TOKEN`
- which camera to use — it lists the ones it can actually open
- what it is watching, a baby or a cabin, which sets the sensible defaults
  for each
- which microphone to listen on
- how long to keep recordings and how much disk it may use

Answers are saved to `cabinwatch.json` next to the `.exe`, and recordings go
in a `clips` folder next to it. After that it starts straight away. To change
something later, edit that file, or run `CabinWatch-Agent.exe --setup` to be
asked again.

Keep the console window open — that is the program running. It prints what it
is doing, which is also where you look first if something is wrong.

**To change anything afterwards, you do not have to touch the console or the
file.** While the agent runs it serves a settings page on that machine only:

```
http://127.0.0.1:8765
```

It shows each camera's live picture and a microphone level that moves when you
speak, so you can tell which device is which before committing to it. From
there you can switch camera or microphone, rename a camera, change what it is
watching, set how long each saved file is, how long footage is kept and how
much disk it may use, and point it at a different server. Press **Apply** and
that camera restarts on the new settings.

It listens on `127.0.0.1` and nowhere else — the page holds your token, so it
is only for whoever is already sitting at that machine. Start the agent with
`--open` to have it pop up by itself, `--no-panel` to turn it off, or
`--panel-port 9000` to move it.

### 4b. With Python — for more than one camera

Use this when you want several cameras on one machine, an ONVIF camera, or
ignore-regions: `config.py` can express far more than the questions can, and
it wins over `cabinwatch.json` if both exist.

Needs Python 3.10 or newer.

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

Neither `agent/config.py` nor `cabinwatch.json` is committed, so your token and
camera passwords never leave that machine.

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

### A USB gimbal webcam — ask it first

A gimbal webcam is a lottery. Some expose the motor as a standard camera
control, which is what `uvc` drives. Others move only from the maker's own app,
over a private channel nothing else can speak. The box never says which, so ask
the camera:

```bash
python agent/agent.py --probe-ptz 0        # 0 is the source number
```

It reports every pan/tilt/zoom control the camera exposes, with the real limits
the camera itself declares, and prints the `ptz` block to paste — including that
camera's own travel range, so holding an arrow stops where the motor stops
instead of at some number we guessed.

It also turns the camera and asks you which way it actually went, because
which direction a positive number means is not agreed between makers. On an
OBSBOT a bigger pan number turns the camera to *your* left, so without that
answer every arrow on the dashboard would do the opposite of what it says.

**An OBSBOT Tiny 2 or Tiny SE is this kind of camera** — its gimbal is a
standard UVC control, so the arrows work. Its block, if you would rather not
run the probe:

```python
"ptz": {
    "type": "uvc",
    "device": "/dev/video0",            # ignored on Windows
    "step": 3600,
    "pan_range": [-468000, 468000],     # about ±130°
    "tilt_range": [-324000, 324000],    # about ±90°
    "invert_pan": True,
},
```

One quirk to know about on these: the camera does not reliably report back
where it is pointing — a read returns the last position that was *asked for*,
not where the gimbal actually is. That matters if you leave the camera's own
AI tracking switched on, because then it moves itself and our idea of its
position goes stale. **Home** puts it back to the centre and both agree again.
Either use the arrows or use its tracking, not both at once.

If it finds nothing, that is an answer too, not a fault. Everything else still
works — recording, alerts, listening, talking — and a camera with its own face
or person tracking keeps following the subject by itself, with nothing for us to
send it. Only the arrows on the dashboard go away. Set `"type": "none"`, or
leave the `ptz` block out.

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

### The fan filter

A ceiling fan or a cooler in the room is the one thing that defeats a baby
monitor. It is loud, it never stops, and it sits right on top of the sound you
are listening for — so you hear a motor instead of the child, and the alert
never fires because the room's noise level is already high.

**Fan filter** on a camera's card deals with that. A fan is *steady*: it holds
the same frequencies minute after minute, while a voice moves around and comes
and goes. So the steady part is measured and taken out, and what is left is
whatever is not the fan. On the measurements in `tests/test_noise_filter.py`,
a cry that sat 9 dB *under* a cooler comes out 11 dB *over* it, and the cooler
itself drops by around 11 dB. A cooler that is switched on later gets learned
within about three seconds.

It is on by default everywhere — the laptop agent, the browser page and the
Android app. On the phone and in the browser it is the device's own voice-call
noise cleaner, which also stops the speaker feeding back while you talk. Turn
it off if you would rather hear the room exactly as it is.

### If the camera is not the right way up

A phone hung over a cot, or propped on its side against a wall, sends a
sideways picture. **Rotate** on that camera's card turns it a quarter turn at
a time. The turn happens on the device and before anything else sees the
frame, so the recordings and the movement detection come out straight too —
not just the live view.

A phone standing on edge works it out by itself when it starts, from gravity.
Lying flat there is nothing to read, so it leaves the picture alone; and once
you set it by hand, your choice wins and is never overruled.

One thing it cannot do: a clip saved by the **browser** page keeps the
camera's own orientation, because the browser records the camera stream
directly rather than the turned picture. The Android app and the laptop agent
turn their recordings too.

### If the power goes out

An mp4 only becomes playable when it is closed properly — the index saying
where every frame is gets written at the very end. So a camera that loses
power mid-clip used to lose the whole segment, not the last part of it:
"moov atom not found", and up to five minutes of exactly the footage you
would want after an incident.

Segments are written as Matroska now, which writes as it goes, and are
repackaged into mp4 when they close — or on the next start, if there was no
chance to close them. Measured in `tests/test_power_cut.py`, pulling the plug
now costs the last second or two instead of everything.

### Two devices with the same name

Each camera needs its own name. If a second device connects under a name that
is already in use by a device that is still running, it is turned away and you
get an alert saying so — rather than the two of them taking turns throwing
each other off the server all night, with both their pictures arriving under
one name.

A camera that simply stops stays on the dashboard, greyed out, with the time
it was last heard from, so "when did it go?" has an answer. **Remove** takes
it off the list once you are done with it.

### What each alert means

| Alert | What set it off |
|---|---|
| **Baby awake** | A sound in the voice range, held for more than a second. |
| **Thud / fall** | A sudden loud low-frequency bang. Check immediately. |
| **Movement** | Something moved in view. |
| **Person seen** | Movement that was also person-shaped — used for the cabin. |
| **Sound** | Continuous noise that was not clearly a cry. |
| **Camera was moved** | The camera itself was turned, knocked or covered. |
| **Battery low** | A phone camera is about to die. |
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

## Checking it still works

Everything in `tests/` is a plain program: it prints what it checked and
exits non-zero if something is wrong. To run the lot:

```bash
python tests/run_all.py
```

Around two hundred checks, including ones that record real video, kill the
process mid-clip to see what survives, and drive the dashboard in a real
browser. The browser ones skip themselves if there is no browser to drive, so
this works on a bare machine. A single file runs on its own too:

```bash
python tests/test_power_cut.py
```

GitHub Actions runs all of it on every push, and a release cannot be
published unless it passes.

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
