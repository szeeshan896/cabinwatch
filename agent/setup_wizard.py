"""
First-run setup, so the agent is usable without editing any files.

This is what makes a standalone .exe sensible: you double-click it, it finds
the cameras and microphones on the machine, asks a handful of questions, and
writes cabinwatch.json next to itself. After that it starts straight away
every time.

Editing agent/config.py by hand still works and still wins if it exists -
that route is better when you are running several cameras or an ONVIF camera,
because it can express far more than these questions.
"""

import os
import sys
import json


def base_dir():
    """The folder to keep settings and recordings in.

    For a PyInstaller .exe that is the folder holding the .exe, not the
    temporary folder it unpacks itself into - otherwise every restart would
    lose your settings and your footage.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_NAME = "cabinwatch.json"


def config_path():
    return os.path.join(base_dir(), CONFIG_NAME)


class JsonConfig:
    """Presents a cabinwatch.json the same way config.py looks to the agent."""

    def __init__(self, data):
        d = data or {}

        self.SERVER_URL = str(d.get("server_url", "")).strip().rstrip("/")
        self.AGENT_TOKEN = str(d.get("agent_token", "")).strip()
        self.VERIFY_TLS = bool(d.get("verify_tls", True))

        clips = d.get("clips_dir", "clips")
        self.CLIPS_DIR = clips if os.path.isabs(clips) else \
            os.path.join(base_dir(), clips)

        self.SEGMENT_SECONDS = int(d.get("segment_seconds", 60))
        self.RETENTION_HOURS = float(d.get("retention_hours", 72))
        self.EVENT_RETENTION_HOURS = float(d.get("event_retention_hours", 336))
        self.MAX_DISK_GB = float(d.get("max_disk_gb", 20.0))
        self.ALWAYS_RECORD = bool(d.get("always_record", True))
        self.PREBUFFER_SECONDS = int(d.get("prebuffer_seconds", 6))

        self.CAPTURE_FPS = float(d.get("capture_fps", 15.0))
        self.MOTION_MIN_AREA = int(d.get("motion_min_area", 1500))
        self.MOTION_THRESHOLD = int(d.get("motion_threshold", 25))
        self.SOUND_SENSITIVITY = float(d.get("sound_sensitivity", 1.0))
        self.CRY_SECONDS = float(d.get("cry_seconds", 1.2))
        self.EVENT_MIN_GAP = float(d.get("event_min_gap", 25.0))

        self.AUDIO_DEVICE = d.get("audio_device")
        self.SPEAKER_DEVICE = d.get("speaker_device")
        self.SSD_PROTO = d.get("ssd_proto")
        self.SSD_MODEL = d.get("ssd_model")

        self.CAMERAS = d.get("cameras") or {}


def load_json_config():
    """Returns a JsonConfig if cabinwatch.json exists and parses, else None."""
    path = config_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return JsonConfig(json.load(f))
    except Exception as e:
        print(f"[config] {CONFIG_NAME} could not be read: {e}")
        print(f"[config] Fix or delete it: {path}")
        return None


def save_json_config(data):
    path = config_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return path


# ---------------------------------------------------------------------------
# The questions
# ---------------------------------------------------------------------------
def _ask(question, default=None, secret=False):
    suffix = f" [{default}]" if default not in (None, "") else ""
    while True:
        try:
            answer = input(f"{question}{suffix}: ").strip()
        except EOFError:
            return default or ""
        if not answer and default not in (None, ""):
            return default
        if answer:
            return answer
        print("  Please type something.")


def _ask_choice(question, options, default_index=0):
    """options is a list of (value, label)."""
    print(f"\n{question}")
    for i, (_, label) in enumerate(options, start=1):
        mark = " (default)" if i - 1 == default_index else ""
        print(f"  {i}. {label}{mark}")
    while True:
        raw = input(f"Choose 1-{len(options)} [{default_index + 1}]: ").strip()
        if not raw:
            return options[default_index][0]
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        print("  Type one of the numbers above.")


def _ask_yes_no(question, default=True):
    suffix = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{question} [{suffix}]: ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False


def run_wizard(probe_cameras, list_microphones):
    """Ask what is needed, write cabinwatch.json, and return a JsonConfig.

    probe_cameras and list_microphones are passed in so this module does not
    have to import OpenCV or sounddevice itself.
    """
    print()
    print("=" * 62)
    print("  CabinWatch agent - first-time setup")
    print("=" * 62)
    print()
    print("  Answers are saved to:")
    print(f"    {config_path()}")
    print()
    print("  Both answers come from YOUR server. If you have not put the")
    print("  server up yet, stop here and do that first - this cannot")
    print("  connect to anything until it exists. Part 1 of the README has")
    print("  the steps; it takes about ten minutes on Railway.")
    print()
    print("  Once it is up:")
    print("    - the address is the domain Railway generated for the")
    print("      service, under Settings -> Networking. It looks like")
    print("      https://cabinwatch-production.up.railway.app")
    print("    - the token is the AGENT_TOKEN you set yourself under")
    print("      Variables. Nobody gives it to you - you invent a long")
    print("      random string once, and every camera uses that same one.")
    print()

    server = _ask("Server address")
    if not server.startswith("http"):
        server = "https://" + server.lstrip("/")
    token = _ask("Camera token (AGENT_TOKEN)")

    print("\nLooking for cameras on this machine...")
    found = probe_cameras()
    if not found:
        print("\n  No camera could be opened.")
        print("  Close anything else using it (Teams, Zoom, the Camera app)")
        print("  and run this again.")
        return None

    print(f"  Found {len(found)}:")
    cam_options = [(idx, f"Camera {idx}  ({w}x{h})") for idx, w, h in found]
    source = _ask_choice("Which camera should this use?", cam_options, 0)

    name = _ask("Name this camera (so you know which one it is)", "Baby Room")

    watching = _ask_choice(
        "What is it watching?",
        [("baby", "A baby - alert me on sounds and any movement"),
         ("cabin", "A cabin or office - alert me only when a person is seen")],
        0,
    )

    mics = list_microphones()
    audio_device = None
    if mics:
        print()
        if watching == "baby":
            print("Which microphone should listen for crying?")
        else:
            print("Which microphone should this use?")
        mic_options = [(None, "Whatever Windows is using by default")] + \
            [(i, f"{i}: {label}") for i, label in mics]
        audio_device = _ask_choice("Microphone", mic_options, 0)
    else:
        print("\n  No microphone found. Sound alerts and talking back will be")
        print("  switched off for this camera.")

    retention = _ask_choice(
        "How long should recordings be kept on this machine?",
        [(24, "1 day"), (72, "3 days (default)"), (168, "7 days"),
         (336, "14 days")],
        1,
    )
    max_disk = _ask_choice(
        "How much disk space may it use at most?",
        [(5.0, "5 GB"), (20.0, "20 GB (default)"), (50.0, "50 GB"),
         (100.0, "100 GB")],
        1,
    )

    cam_id = "".join(
        ch if ch.isalnum() else "-" for ch in name.lower()
    ).strip("-").replace("--", "-") or "cam"

    camera = {
        "name": name,
        "source": source,
        "baby_mode": watching == "baby",
        "audio": bool(mics),
        "speaker": bool(mics),
    }
    if watching == "baby":
        camera["motion_min_area"] = 900     # a baby is small in frame
        camera["sound_sensitivity"] = 1.3
    else:
        camera["person_detection"] = True   # ignore fans, curtains, light
    if audio_device is not None:
        camera["audio_device"] = audio_device

    data = {
        "server_url": server,
        "agent_token": token,
        "clips_dir": "clips",
        "retention_hours": retention,
        "max_disk_gb": max_disk,
        "cameras": {cam_id: camera},
    }

    path = save_json_config(data)
    print()
    print("-" * 62)
    print(f"  Saved to {path}")
    print(f"  Recordings will go in {os.path.join(base_dir(), 'clips')}")
    print()
    print("  To change anything later, edit that file, or delete it and run")
    print("  this again. To add more cameras to this one machine, add them")
    print('  under "cameras".')
    print("-" * 62)
    print()
    return JsonConfig(data)


def can_ask():
    """False when there is no console to ask questions on."""
    return sys.stdin is not None and sys.stdin.isatty()
