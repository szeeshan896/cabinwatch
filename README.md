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

### If the APK will not install

Android refuses to install an app over one that was signed with a different
key. The error is just **"App not installed"**, with no explanation.

Every build used to be signed with whatever throwaway key the build machine
happened to make that day, so each new APK was a stranger to the one already
on the phone. To get out of it now: **uninstall CabinWatch on the phone, then
install the APK.** You will have to type the server address and token again.

To stop it happening again, give the builds one key that stays the same. Once,
on any machine with Java:

```bash
keytool -genkeypair -v -keystore cabinwatch.jks -alias cabinwatch \
        -keyalg RSA -keysize 2048 -validity 10000 \
        -storepass CHOOSE-A-PASSWORD -keypass CHOOSE-A-PASSWORD \
        -dname "CN=CabinWatch, O=CabinWatch, C=IN"

base64 -w0 cabinwatch.jks      # macOS: base64 -i cabinwatch.jks
```

Then in the repository, **Settings → Secrets and variables → Actions**, add:

| Secret | Value |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | the long line `base64` printed |
| `ANDROID_KEYSTORE_PASSWORD` | the password you chose |

**Keep `cabinwatch.jks` somewhere safe and do not put it in the repository.**
Lose it and the next APK cannot update the installed app — you would be back to
uninstalling first. Every build from then on is signed with it, each one a
higher version than the last, and updates install over the top.

Until those secrets exist the build still works; it just says so in the build
log and the APK cannot update an app already on a phone.

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

**Listen** opens the camera's microphone and **Talk** sends your voice out of
its speaker. **Baby** turns sound alerts on for that camera, **Alerts** disarms
it when you are in the room yourself, and **Pause rec** stops it recording for
a while. **Clips** is everything the device has recorded.

The rest depend on the camera and what it is watching, and each has its own
section below: **Fan filter**, **Rotate**, **People only** (cabin cameras),
**Hours**, and **Siren**. A camera that has stopped reporting keeps its card,
greyed out, with **Remove** to take it off the list for good.

### Finding a recording

Round-the-clock recording makes several hundred files a day, and a flat list
of filenames is useless at that size. **Clips** shows one day at a time, with
the day drawn as twenty-four blocks — lit where something was recorded, amber
where something happened — so a quiet night reads as a quiet night at a
glance. **Only what happened** hides the rest.

Clips marked **EVENT** had movement or a sound in them and are kept longer on
the device. **COPY** means the server has its own copy, so **Clips keeps
working on a camera that is no longer there** — which is the whole point of
having kept it.

None of this costs the server anything: it passes on the list the device
already keeps, and the sorting, grouping and filtering happen in your browser.

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
| **Unplugged** | A camera that was charging no longer is. |
| **Two devices, one name** | Two cameras are set up with the same name. |
| **Camera offline** | It stopped reporting — power, internet or battery. |

Cries, thuds, people and a camera going offline get through quiet hours. The
rest are held back.

### Keeping the data bill small

Video only moves while you are actually looking, and every frame on the wire
is a whole JPEG — nothing is shared between one frame and the next, so the
bill is the size of a frame times the frame rate. Measured on a real frame
from a real camera:

| Mode | Per hour of watching |
|---|---|
| **Live** 480px, 4 fps | about **120 MB** |
| **HD** 960px, 6 fps | about **530 MB** |
| **Snap** one still every 2s | a small fraction of Live |

HD used to be 1280px at 12 fps, which measured at **1.7 GB an hour** — left on
by accident overnight, somebody's whole data allowance. It is smaller and
slower now, and **falls back to Live by itself after two minutes** and says so.
HD is for looking closely for a moment, not for leaving on.

Closing the dashboard tab, or even switching away from it, stops the stream by
itself.

Sending whole frames is the expensive part of all this. The same picture as
H.264 measures about **86% smaller** (`tests/test_live_cost.py` measures it on
every run), so moving the live view to a real video codec is the one change
left that would make a large difference — and it is a rewrite of the whole
video path, so it is written down here rather than done quietly.

---

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

### Only waking you for a person

Movement on its own is a poor burglar alarm. A curtain over an open window, a
ceiling fan, headlights crossing a wall — they all move, and after a few
nights of being woken by them you stop trusting the phone. That is worse than
no alarm, because now the real one gets ignored too.

**People only** on a cabin camera makes an alert wait until somebody is
actually in the frame. The phone decides that itself, with its own labeller,
and the picture never leaves it to get the answer. It is only asked after
something has already moved, so an empty room at night costs nothing.

It does not appear on a baby camera, where any movement is the point. And if
a phone cannot answer — an old one without the parts — it raises the alert
anyway rather than swallowing it.

### Hours — so you stop being alerted about yourself

A camera you have to arm and disarm by hand ends up disarmed. You turn it off
because it keeps alerting about you, and nobody remembers to turn it back on
in the evening.

**Hours** on a camera's card asks the question you can actually answer: when
are you normally there? Pick the times and the days, and it leaves you alone
during them and watches the rest of the time by itself. It keeps pushing that
at the camera rather than setting it once, so a camera that was off all night
comes back to the right state instead of whatever it had when it died.

**Set `TIMEZONE` for this to make sense.** The server runs wherever it is
hosted, and on Railway that is UTC — so without it, hours set for ten at night
start at half past three in the morning in India. Put your IANA zone in
Railway's variables, for example `TIMEZONE=Asia/Kolkata`. Quiet hours use it
too, and had exactly the same problem until now.

### The siren

**Siren** on a camera's card makes that device scream — a two-tone wail
through its own speaker, with the torch flashing where the phone has one. It
is a deterrent, and it is also how you find out whether anybody is actually
there. It stops by itself after two minutes, because a siren nobody turns off
flattens the battery and takes the camera with it.

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

### The laptop agent lives in the tray

There is no console window. The agent sits in the system tray: right-click the
icon for the settings page, the recordings folder, and a way to stop it that
is clearly a decision rather than a stray click. The icon dims when no camera
is connected, so a glance tells you whether it is working.

A window open on the desktop is something people close — usually by accident,
usually the day the footage mattered.

Because there is no console, everything it would have printed goes to
**`cabinwatch.log`**, next to the exe. That is the first thing to look at when
a camera misbehaves overnight, and the first thing to send me. It keeps one
previous log and never grows past a couple of megabytes. `CABINWATCH_LOG`
names a different file if you want one somewhere else.

On a machine with no tray — a Linux server, a Pi with no desktop — it prints
to the terminal exactly as it always did.

### Making a laptop camera come back by itself

A camera that only runs while somebody has a window open is not a camera. It
lasts until the first Windows update, the first power cut, or the first time
somebody tidies the desktop.

On the settings page there is now a **Start by itself** choice, or from a
terminal:

```bash
python agent/agent.py --start-with-windows     # and --no-start-with-windows
```

On Windows that is an entry under your own user's Run key — no administrator
rights, no installer, and nothing left behind when you turn it off. On Linux
it is a systemd user service, for a Raspberry Pi left plugged in as the bridge
to an IP camera.

**The part that catches people:** this starts when you *log in*, not when the
machine powers up. A laptop sitting at the login screen after a power cut is
not watching anything. If it is a machine kept for this, turn on automatic
sign-in (`netplwiz` on Windows) so a restart takes it all the way back. On a
Pi, `sudo loginctl enable-linger $USER` does the same job.

### Two devices with the same name

Each camera needs its own name. If a second device connects under a name that
is already in use by a device that is still running, it is turned away and you
get an alert saying so — rather than the two of them taking turns throwing
each other off the server all night, with both their pictures arriving under
one name.

A camera that simply stops stays on the dashboard, greyed out, with the time
it was last heard from, so "when did it go?" has an answer. **Remove** takes
it off the list once you are done with it.

### A phone on battery will not last the night

Watching a room is what empties a phone. The camera, the encoder and the
microphone run without a pause whether or not anybody is looking — so a spare
phone left watching, unplugged, is switched off at zero by morning. Nothing is
wrong when that happens; that is simply what it costs.

**A camera meant to stay on belongs on a charger.** The app holds the screen
dim, not off, and no setting changes the arithmetic.

For the times you forget, there is **Standby**. The camera holds its
connection and shuts everything else down, wakes the moment you open it to
look, and goes back when you stop. The battery then lasts for days rather than
hours.

**What standby costs, plainly:** it is not recording and it is not watching
for movement, so it raises no alerts at all while it sleeps. It is the right
state for a spare phone you left somewhere, and the wrong one for the camera
over a cot.

You can put a camera there yourself with **Standby** on its card. The server
also does it on its own, because forgetting the charger is the mistake that
actually happens:

- below **55%** and not charging, it warns you while there is still time to
  walk over with a cable
- below **30%**, it goes to standby and says so

A flat phone is worth less than no camera at all — it cannot be woken, it
cannot tell you where it is, it is simply gone. A sleeping one is still there
in the morning. Both thresholds are settings (`BATTERY_WARN_BELOW`,
`STANDBY_BELOW`); a camera on charge is never touched.

### When a camera is unplugged

Pulling the charger is the first thing anybody does to a phone they are
taking, and it happens long before the battery is low enough for the usual
warning — which would reach you hours later, from a phone that is no longer
in the building. So losing power is its own alert, and it gets through quiet
hours.

It waits a few seconds before believing it: phones misreport for a moment,
and being woken at three in the morning by a loose cable is worse than being
told three seconds later. A laptop on mains has no battery to report, so it
never raises this at all.

### When a camera stops, and what brings it back

A camera that stops stays on the dashboard, greyed out, with the time it was
last heard from — and the card says what will bring that particular kind of
camera back, so nobody sits looking at a grey tile wondering whether to drive
over there.

- **The Android app** checks on itself every quarter of an hour and starts
  again if something ended it — swiped off the recents list, stopped by a
  battery optimiser, killed by a manufacturer's "cleaner". It also comes back
  after a reboot. Stopping it from the app's own button keeps it stopped.
- **The laptop agent** comes back if you turned on **Start by itself**.
- **A browser camera** stops when the page is closed or the phone sleeps, and
  has to be opened again on that phone. That is the browser's rule, not ours —
  it is why the app exists.

**What cannot be done:** starting a fully stopped app from the dashboard.
Nothing can reach an app that is not running. Android allows exactly one
exception — a Firebase Cloud Messaging push — and that needs a Firebase
project set up under your own Google account, with its key added to the app.
It is free, and it is the only route; if you want it, it is a small change
once the project exists.

### If the camera itself is what gets stolen

Recordings live on the device that made them. For the baby that is exactly
right — nothing is sent anywhere until you ask for it. For the cabin it has a
hole straight through the middle of it: the thing being recorded is somebody
taking the device, and when they take it the recording goes too.

So when a camera finishes a clip that a **person**, a **thud**, the camera
**being moved**, or **crying** was raised during, it tells the server, and the
server pulls a copy and keeps it. The alert in Recent Activity then carries a
**saved clip** link that still works after the camera is gone.

Ordinary movement is deliberately not on that list — on a camera watching a
room somebody lives in it would fill the store within the hour and push out
the clip that mattered. Change it with `EVIDENCE_KINDS` if you want to.

The store is small on purpose: 800 MB and 14 days by default
(`EVIDENCE_MAX_MB`, `EVIDENCE_DAYS`), oldest dropped first. And be clear about
what it is not: **on Railway the disk is wiped by every redeploy.** This
survives the camera going missing, not everything. Download anything that
matters.

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
