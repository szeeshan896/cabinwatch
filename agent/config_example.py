"""
CabinWatch agent settings.

HOW TO USE
  1. Copy this file to  config.py   (same folder)
  2. Edit config.py with your real server URL, token and cameras
  3. config.py is gitignored, so your token, camera passwords and IP
     addresses never get committed or pushed anywhere

Find out what this machine can see:

    python agent/agent.py --list-cameras
"""

# ---------------------------------------------------------------------------
# Your server
# ---------------------------------------------------------------------------
# The HTTPS address Railway gave your service.
SERVER_URL = "https://YOUR-APP.up.railway.app"

# Must match the AGENT_TOKEN variable set on the server.
AGENT_TOKEN = "paste-the-same-long-random-token-here"

# Leave True. Only set False if you are testing against a local server with a
# self-signed certificate.
VERIFY_TLS = True

# ---------------------------------------------------------------------------
# Where recordings are kept - on THIS machine, not on the server
# ---------------------------------------------------------------------------
CLIPS_DIR = "clips"

SEGMENT_SECONDS = 300       # how long one video file covers, in seconds.
                            # 300 = 5 minutes. Shorter files are quicker to
                            # scrub through and cost less if the machine
                            # loses power mid-write; longer ones are nicer to
                            # watch. Any camera can override it.
RETENTION_HOURS = 72        # ordinary footage is recycled after this
EVENT_RETENTION_HOURS = 336 # footage with movement/sound is kept 14 days
MAX_DISK_GB = 20.0          # never use more disk than this
ALWAYS_RECORD = True        # True = 24x7 rolling. False = only on events.
PREBUFFER_SECONDS = 6       # seconds kept from before an event starts
                            # (only used when ALWAYS_RECORD is False)

# ---------------------------------------------------------------------------
# Capture and detection defaults (each camera can override these)
# ---------------------------------------------------------------------------
CAPTURE_FPS = 15.0
MOTION_MIN_AREA = 1500      # smaller = more sensitive
MOTION_THRESHOLD = 25
SOUND_SENSITIVITY = 1.0     # above 1.0 = more sensitive to quieter sounds
CRY_SECONDS = 1.2           # how long a cry must last before alerting
EVENT_MIN_GAP = 25.0        # seconds between two alerts of the same kind

# Watching the camera itself. If the view changes completely and stays
# changed, somebody has turned it, knocked it or covered it. Raise this if a
# camera sits somewhere that legitimately changes view (a door it is taped
# to), lower it to be told sooner.
TAMPER_HOLD_SECONDS = 4.0

# Microphone and speaker. None = the system default. Numbers come from
# "python agent/agent.py --list-cameras".
AUDIO_DEVICE = None
SPEAKER_DEVICE = None

# Optional, for better person detection in the cabin. Download
# MobileNetSSD_deploy.prototxt and MobileNetSSD_deploy.caffemodel and point
# these at them. Left as None, a built-in detector is used instead, which
# needs no download but is less accurate.
SSD_PROTO = None
SSD_MODEL = None

# ---------------------------------------------------------------------------
# Cameras
# ---------------------------------------------------------------------------
# source:
#   0, 1, 2 ...                       a camera attached to this machine
#   "rtsp://user:pass@192.168.1.50:554/stream1"
#                                     an IP / PTZ camera on this network
#   "http://192.168.1.51:8080/video"  an Android phone running "IP Webcam"
#
# Each entry becomes its own camera on the dashboard, and one agent can run
# as many as the machine can keep up with.
CAMERAS = {

    # ---- the baby's room: a spare laptop, screen can be closed -----------
    "baby": {
        "name": "Baby Room",
        "source": 0,
        "baby_mode": True,        # alert on sounds as well as movement
        "audio": True,            # listen for crying
        "speaker": True,          # let you talk back through this laptop
        "motion_min_area": 900,   # more sensitive: a baby is small
        "sound_sensitivity": 1.3,
        "retention_hours": 48,
        # If this laptop has more than one camera, list them here and you can
        # flip between them from the dashboard.
        # "lenses": [
        #     {"id": "0", "label": "Built-in webcam", "source": 0},
        #     {"id": "1", "label": "USB webcam",      "source": 1},
        # ],
    },

    # ---- the cabin: alert only when a PERSON is seen ---------------------
    # "cabin": {
    #     "name": "Cabin",
    #     "source": 0,
    #     "person_detection": True,   # ignores fans, curtains, light changes
    #     "audio": False,
    #     "always_record": True,
    #     "retention_hours": 96,
    #     # Rectangles to ignore, as [x, y, width, height] fractions of the
    #     # frame. Use this for a ceiling fan or a window onto the street.
    #     "ignore_regions": [
    #         # [0.0, 0.0, 1.0, 0.25],   # the top quarter of the picture
    #     ],
    # },

    # ---- a rotating camera on this network (the gimbal webcam, or an ----
    # ---- outdoor PTZ camera you buy later) ------------------------------
    # "gate": {
    #     "name": "Front Gate",
    #     "source": "rtsp://cabinwatch:PASSWORD@192.168.1.60:554/stream1",
    #     "person_detection": True,
    #     "audio": False,
    #     "speaker": False,
    #
    #     # Pick ONE of the three blocks below.
    #
    #     # 1. ONVIF - almost every camera sold as PTZ or 360 speaks this.
    #     "ptz": {
    #         "type": "onvif",
    #         "host": "192.168.1.60",
    #         "port": 2020,            # often 80, 2020 or 8000
    #         "user": "cabinwatch",
    #         "password": "PASSWORD",
    #     },
    #
    #     # 2. A USB webcam whose motor is a standard camera control.
    #     # "ptz": {
    #     #     "type": "uvc",
    #     #     "device": "/dev/video0",   # Linux; ignored on Windows
    #     #     "step": 3600,
    #     # },
    #
    #     # 3. A camera with its own little web API. {speed} is filled in.
    #     # "ptz": {
    #     #     "type": "http",
    #     #     "auth": ["admin", "PASSWORD"],
    #     #     "urls": {
    #     #         "left":  "http://192.168.1.60/move?dir=left&sp={speed}",
    #     #         "right": "http://192.168.1.60/move?dir=right&sp={speed}",
    #     #         "up":    "http://192.168.1.60/move?dir=up&sp={speed}",
    #     #         "down":  "http://192.168.1.60/move?dir=down&sp={speed}",
    #     #         "stop":  "http://192.168.1.60/move?dir=stop",
    #     #         "home":  "http://192.168.1.60/preset?go=1",
    #     #         "preset:Gate": "http://192.168.1.60/preset?go=2",
    #     #         "preset:Door": "http://192.168.1.60/preset?go=3",
    #     #     },
    #     # },
    # },
}
