"""
CabinWatch configuration.

HOW TO USE:
  1. Copy this file to  config.py   (same folder)
  2. Edit config.py with your real cameras / settings
  3. config.py is gitignored, so your real IPs, device names and
     network details never get committed or pushed anywhere.

If config.py is missing, CabinWatch falls back to these example values
(laptop webcam only) so it still runs out of the box.
"""

# ----------------------------------------------------------------------------
# Cameras
# ----------------------------------------------------------------------------
# source:
#   0, 1, 2 ...                         -> a local webcam on this computer
#   "http://<phone-ip>:8080/video"      -> an Android phone running "IP Webcam"
#   "rtsp://..."                        -> any RTSP/IP camera
CAMERAS = {
    "laptop": {
        "name": "Laptop Webcam",
        "source": 0,
    },
    # Spare phones (recommended as the hidden camera). Install "IP Webcam"
    # on the phone, tap Start server, and paste the URL it shows + "/video".
    #
    # "phone1": {"name": "Camera 1", "source": "http://PHONE_IP:8080/video"},
    # "phone2": {"name": "Camera 2", "source": "http://PHONE_IP:8080/video"},
}

# ----------------------------------------------------------------------------
# Motion detection
# ----------------------------------------------------------------------------
MOTION_MIN_AREA = 1500      # smaller = more sensitive
MOTION_THRESHOLD = 25       # pixel difference threshold
MOTION_COOLDOWN = 8.0       # seconds of stillness before auto-recording stops
PRE_MOTION_SECONDS = 3      # seconds kept from BEFORE motion started

# ----------------------------------------------------------------------------
# Recording
# ----------------------------------------------------------------------------
RECORD_FPS = 20.0
MAX_CLIP_SECONDS = 300      # hard cap on a single clip
CLIPS_DIR = "clips"

# ----------------------------------------------------------------------------
# Server
# ----------------------------------------------------------------------------
HOST = "0.0.0.0"            # 0.0.0.0 = reachable from your phone on same WiFi
PORT = 8000

# ----------------------------------------------------------------------------
# Optional alarm to a Google Home / Chromecast speaker on motion
# ----------------------------------------------------------------------------
ALARM_ENABLED = False
ALARM_DEVICE_NAME = "YOUR_SPEAKER_NAME"   # exact name from the Google Home app
ALARM_SOUND_URL = "https://actions.google.com/sounds/v1/alarms/alarm_clock.ogg"
ALARM_MIN_GAP = 60          # seconds between alarms
