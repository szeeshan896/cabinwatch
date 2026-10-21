# Setup checklist

The order matters — each step needs the one before it.

## 1. Server (once, ~10 minutes)

- [ ] Push this repo to your own GitHub account
- [ ] Railway → New Project → Deploy from GitHub repo
- [ ] Generate three random strings:
      `python -c "import secrets; print(secrets.token_urlsafe(32))"`
- [ ] Railway → Variables → add `AGENT_TOKEN`, `VIEW_PASSWORD`, `SECRET_KEY`
- [ ] Railway → Settings → Networking → Generate Domain
- [ ] Open the URL, log in, add to your phone's home screen

## 2. Alerts (once, ~3 minutes)

- [ ] Install the **ntfy** app on your phone
- [ ] Invent a long random topic name, e.g. `cabinwatch-7f3a91c2e84b`
- [ ] ntfy app → Subscribe to topic → that name
- [ ] Railway → Variables → `NTFY_TOPIC` = the same name → redeploy
- [ ] ntfy app → that topic → raise its priority so it rings on silent

## 3. First camera — baby's room

Pick whichever you have spare. A laptop hears better; a phone hides better.

**Android phone, using the app (best for a room you depend on)**
- [ ] GitHub → **Actions** → newest *Build Android APK* run
- [ ] Download the **cabinwatch-camera-apk** artifact, unzip it
- [ ] Put `app-debug.apk` on the phone, open it, allow the install
- [ ] Fill in the server address and the same `AGENT_TOKEN`
- [ ] Name it, pick the lens, turn on **Watching a baby**
- [ ] **Start camera**, allow camera + microphone + notifications
- [ ] Press **Stop Android sleeping this app** — do not skip this one
- [ ] Close the app; check the notification stays and the dashboard shows green
- [ ] Restart the phone once and confirm it comes back on its own

**Any phone, using the browser page (nothing to install)**
- [ ] Dashboard → **+ Phone**, open that page on the spare phone, log in
- [ ] Name it, pick the lens, choose "Baby room", press **Start camera**
- [ ] Allow camera + microphone
- [ ] Plug it in to charge, place it facing the bed
- [ ] Press **Dim screen** so it is not glowing at night
- [ ] Leave the tab open and in the foreground (this one needs it)

**Laptop**
- [ ] `pip install -r agent/requirements.txt`
- [ ] `cp agent/config_example.py agent/config.py`
- [ ] `python agent/agent.py --list-cameras` to find the camera and mic numbers
- [ ] Edit `agent/config.py`: `SERVER_URL`, `AGENT_TOKEN`, the `baby` camera
- [ ] `python agent/agent.py` (Windows: double-click `agent\run.bat`)

## 4. Check it actually works — do not skip this

- [ ] Camera shows up on the dashboard with a green dot
- [ ] **Snap** shows a picture
- [ ] **Test alert** reaches your phone
- [ ] Wave at the camera → a *Movement* alert arrives with a picture
- [ ] Clap near it → a *Sound* or *Baby awake* alert arrives
- [ ] **Listen** → you can hear the room
- [ ] **Talk** → your voice comes out of that device
- [ ] **Clips** → recordings are listed, and one plays
- [ ] Leave it one night, then check the morning's alerts for false alarms

## 5. Tune it after the first night

- [ ] Fan or curtain causing false alarms → add `ignore_regions`, or raise
      `motion_min_area`
- [ ] Missed the baby waking → raise `sound_sensitivity` above 1.0
- [ ] Too many alerts at night → set `QUIET_FROM=23` and `QUIET_TO=7` on
      Railway (cries, thuds and offline cameras still get through)

## 6. Cabin camera

- [ ] Another phone or laptop, same as step 3, but choose "Cabin / office"
- [ ] In `agent/config.py` set `"person_detection": True` for it
- [ ] Leave `always_record` on so you have the hours before an incident
- [ ] Confirm you can reach it from mobile data, not just home WiFi

## 7. Rotating camera (now or later)

- [ ] Confirm the camera speaks **ONVIF** and **RTSP**
- [ ] Switch RTSP/ONVIF on in the camera's own app, note the user and password
- [ ] `pip install onvif-zeep`
- [ ] Add the camera to `agent/config.py` with an `onvif` ptz block
- [ ] Run the agent on a machine on the *same network as the camera*
- [ ] Arrow pad appears on the dashboard; save positions in the camera's app
