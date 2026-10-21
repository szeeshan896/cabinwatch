# CabinWatch

Turn your laptop **and** spare Android phones into a multi-camera CCTV you can
watch live from your phone — with motion detection, auto-recording, and a manual
record ON/OFF switch per camera. Clips are saved with timestamps so you have
evidence even when you're not watching.

---

## What you get

- **Live view** from your phone's browser (same WiFi)
- **Laptop webcam + any number of phone cameras** in one dashboard
- **Motion detection** per camera
- **Auto-record on motion** (toggle)
- **Record now** manual switch (toggle)
- **Timestamped .mp4 clips** saved to the `clips/` folder
- Each clip includes a few seconds **before** motion started
- Optional **alarm** to a Google Home / Chromecast speaker on motion

---

## 1. Install (one time)

You need Python 3.10+ on the computer that will run it.

```bash
pip install -r requirements.txt
```

## 2. Set up your cameras

All settings live in a file called `config.py`. It isn't in the repo on
purpose — it's where your own camera IPs and device names go, and it stays on
your machine only. Create it from the example:

```bash
# Windows
copy config_example.py config.py

# macOS / Linux
cp config_example.py config.py
```

Then open `config.py` and edit the `CAMERAS` section:

- **Laptop webcam** is already set up (`"source": 0`). A second USB webcam is
  `1`, `2`, etc.
- **Spare Android phones** (the hidden camera for catching the thief):
  1. Install the free **"IP Webcam"** app on the phone (Play Store).
  2. Open it, scroll down, tap **Start server**.
  3. It shows a URL like `http://192.168.1.50:8080`.
  4. In `config.py`, uncomment a `phone` line and paste that URL **with `/video`**:
     ```python
     "phone1": {"name": "Camera 1", "source": "http://192.168.1.50:8080/video"},
     ```
  5. Turn the phone **screen off** and place it hidden, facing the area. The app
     keeps the camera running with the screen off, so nothing shows.

Repeat for each spare phone.

> If you skip this step and never create `config.py`, CabinWatch still runs — it
> just uses the laptop webcam only.

## 3. Run

```bash
python cctv.py
```

On Windows you can instead double-click **`run.bat`**, which installs the
requirements and starts it for you.

It prints two addresses:

```
On this computer : http://localhost:8000
On your phone    : http://192.168.x.x:8000   (same WiFi)
```

Open the phone address in your phone's browser. You'll see all cameras, live,
with the Auto-record / Record now buttons and the list of saved clips.

---

## Watching from ANYWHERE (not just same WiFi)

By default this works on your **home/office WiFi**. To watch from outside, add a
tunnel — easiest is **Tailscale** (free): install it on the computer and your
phone, then use the computer's Tailscale IP instead. Nothing else changes.

---

## Optional: alarm on Google Home

Google speakers won't give their mic/camera to other apps, but they **can** play
an alarm. In `config.py` set:

```python
ALARM_ENABLED = True
ALARM_DEVICE_NAME = "Living Room speaker"   # exact name from the Google Home app
```

and install the extra library:

```bash
pip install PyChromecast
```

Now when motion is detected, the computer casts an alarm sound to that speaker.

---

## Privacy / what's safe to share

- `config.py` (your real camera IPs, device names) is **gitignored** and never
  leaves your machine.
- `clips/` recordings are gitignored too — footage never gets committed.
- Everything in this repo is generic code and placeholder examples.

## Notes

- A laptop's own webcam light **will** turn on when its camera is in use — that's
  normal hardware behaviour. For a discreet angle, use a spare phone (screen
  off), not the laptop.
- Clips pile up in `clips/`. Delete old ones now and then.
- Tune motion sensitivity with `MOTION_MIN_AREA` in `config.py` if you get false
  alarms from a fan or changing light.

## Tech

Python · Flask · OpenCV. Each camera runs in its own thread, streams MJPEG to
the dashboard, and writes timestamped MP4 clips on motion or manual record.
