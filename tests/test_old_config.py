#!/usr/bin/env python3
"""
An agent/config.py written before today must still run today.

Every new setting is a chance to break somebody who already had the thing
working: reading it as cfg.NEW_OPTION turns a missing line into a crash on
startup. This builds a config holding only what the first released version
documented, and insists the agent still starts on it.

    python tests/test_old_config.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "agent"))

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {extra}" if extra else ""))


class OldConfig:
    """Exactly the settings the original agent/config_example.py had."""
    SERVER_URL = "https://example.invalid"
    AGENT_TOKEN = "token"
    VERIFY_TLS = True
    CLIPS_DIR = os.path.join(HERE, "..", ".test-oldconfig")
    SEGMENT_SECONDS = 60
    RETENTION_HOURS = 72
    EVENT_RETENTION_HOURS = 336
    MAX_DISK_GB = 20.0
    ALWAYS_RECORD = True
    PREBUFFER_SECONDS = 6
    CAPTURE_FPS = 15.0
    MOTION_MIN_AREA = 1500
    MOTION_THRESHOLD = 25
    SOUND_SENSITIVITY = 1.0
    CRY_SECONDS = 1.2
    EVENT_MIN_GAP = 25.0
    AUDIO_DEVICE = None
    SPEAKER_DEVICE = None
    SSD_PROTO = None
    SSD_MODEL = None
    CAMERAS = {"baby": {"name": "Baby Room", "source": 0, "baby_mode": True}}


def main():
    import shutil
    shutil.rmtree(OldConfig.CLIPS_DIR, ignore_errors=True)
    os.makedirs(OldConfig.CLIPS_DIR, exist_ok=True)

    import agent as agent_mod

    session = None
    try:
        session = agent_mod.CameraSession(
            "baby", OldConfig.CAMERAS["baby"], OldConfig)
        check("a camera starts from a config with none of the newer options",
              True)
    except AttributeError as e:
        check("a camera starts from a config with none of the newer options",
              False, f"{e}")
    except Exception as e:
        check("a camera starts from a config with none of the newer options",
              False, f"{type(e).__name__}: {e}")

    if session is not None:
        check("the tamper watch defaulted rather than failing",
              session.tamper is not None)
        check("recording defaulted to the old clip length",
              session.rec.segment_seconds == 60)
        session.rec.stop()

    # And every setting the agent insists on should be in the example file.
    # Read from the parsed code rather than the text, so prose about the
    # config is not mistaken for code that uses it.
    import ast
    tree = ast.parse(open(os.path.join(HERE, "..", "agent",
                                       "agent.py")).read())
    demanded = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "cfg"
        and node.attr.isupper()
    }
    documented = {
        target.id
        for node in ast.walk(ast.parse(open(
            os.path.join(HERE, "..", "agent", "config_example.py")).read()))
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    missing = sorted(demanded - documented)
    check("every setting the agent requires is in config_example.py",
          not missing, ", ".join(missing) if missing else
          f"{len(demanded)} settings, all documented")

    shutil.rmtree(OldConfig.CLIPS_DIR, ignore_errors=True)
    print(f"\n=== {len(PASS)} passed, {len(FAIL)} failed ===")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
