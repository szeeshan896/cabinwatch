"""
Reading a camera's motor limits off the camera, instead of guessing them.

How far a gimbal can turn is different on every camera, and the old code had
one hardcoded guess for all of them - so on a camera with a smaller range the
dashboard arrows would keep "moving" long after the motor had stopped, and on
a larger one half the travel was unreachable. These checks cover the parsing
of what the camera reports, and that a camera reporting nothing is handled as
a camera with no motor rather than as an error.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "agent"))

import ptz                                   # noqa: E402

# Real v4l2-ctl output, as a gimbal webcam reports it.
GIMBAL = """\
User Controls

                     brightness 0x00980900 (int)    : min=-64 max=64 step=1 default=0 value=0
                       contrast 0x00980901 (int)    : min=0 max=100 step=1 default=50 value=50

Camera Controls

                  pan_absolute 0x009a0908 (int)    : min=-170000 max=170000 step=3600 default=0 value=0
                 tilt_absolute 0x009a0909 (int)    : min=-30000 max=90000 step=3600 default=0 value=0
                 zoom_absolute 0x009a090d (int)    : min=0 max=4 step=1 default=0 value=0
"""

PLAIN = """\
User Controls

                     brightness 0x00980900 (int)    : min=-64 max=64 step=1 default=0 value=0
                      sharpness 0x0098091b (int)    : min=0 max=6 step=1 default=2 value=2
"""


class FakeRun:
    """Stands in for running v4l2-ctl, so this works with no camera at all."""

    def __init__(self, output):
        self.output = output
        self.asked = None

    def __call__(self, args, **kw):
        self.asked = args

        class Result:
            stdout = self.output
        return Result()


def with_v4l2(output, body):
    real_which, real_run = ptz.shutil.which, ptz.subprocess.run
    fake = FakeRun(output)
    ptz.shutil.which = lambda name: "/usr/bin/v4l2-ctl"
    ptz.subprocess.run = fake
    try:
        return body(fake)
    finally:
        ptz.shutil.which, ptz.subprocess.run = real_which, real_run


def check(name, condition, detail=""):
    print(("PASS  " if condition else "FAIL  ") + name
          + (("  -- " + detail) if detail else ""))
    return bool(condition)


def test_reads_the_limits_a_gimbal_reports():
    found = with_v4l2(GIMBAL, lambda f: ptz.uvc_controls("/dev/video2"))
    ok = check("a gimbal's three axes are all found",
               set(found) == {"pan", "tilt", "zoom"}, f"found {sorted(found)}")
    ok &= check("pan limits are read off the camera",
                found.get("pan", {}).get("min") == -170000
                and found["pan"]["max"] == 170000,
                str(found.get("pan")))
    ok &= check("a tilt range that is not centred is kept as it is",
                found.get("tilt", {}).get("min") == -30000
                and found["tilt"]["max"] == 90000,
                str(found.get("tilt")))
    ok &= check("the step the camera asks for is read too",
                found.get("pan", {}).get("step") == 3600)
    return ok


def test_a_camera_with_no_motor_is_not_an_error():
    found = with_v4l2(PLAIN, lambda f: ptz.uvc_controls("/dev/video0"))
    return check("a plain webcam reports no axes", found == {}, str(found))


def test_the_backend_uses_those_limits():
    def body(_):
        return ptz.UvcPTZ("/dev/video2")
    motor = with_v4l2(GIMBAL, body)
    ok = check("the backend takes its range from the camera",
               motor.pan_range == (-170000, 170000)
               and motor.tilt_range == (-30000, 90000),
               f"pan {motor.pan_range} tilt {motor.tilt_range}")

    # And it must not wander outside them, however long an arrow is held.
    def body2(_):
        m = ptz.UvcPTZ("/dev/video2")
        for _ in range(200):
            m.move("right", 1.0)
            m.move("down", 1.0)
        return m
    far = with_v4l2(GIMBAL, body2)
    ok &= check("holding an arrow stops at the end of the travel",
                far.pan == 170000 and far.tilt == -30000,
                f"pan {far.pan} tilt {far.tilt}")
    return ok


def test_a_configured_range_wins():
    """Someone who has measured their own camera must be able to say so."""
    def body(_):
        return ptz.build({"type": "uvc", "device": "/dev/video2",
                          "pan_range": [-90000, 90000]})
    motor = with_v4l2(GIMBAL, body)
    return check("a range given in the config is used over the camera's",
                 motor.pan_range == (-90000, 90000)
                 and motor.tilt_range == (-30000, 90000),
                 f"pan {motor.pan_range} tilt {motor.tilt_range}")


def test_the_fallback_when_there_is_no_v4l2():
    """On Windows there is no v4l2-ctl, and it must still start."""
    real = ptz.shutil.which
    ptz.shutil.which = lambda name: None
    try:
        motor = ptz.UvcPTZ("/dev/video0", cap=None)
    finally:
        ptz.shutil.which = real
    return check("with nothing to ask, a sane range is assumed",
                 motor.pan_range[0] < 0 < motor.pan_range[1],
                 str(motor.pan_range))


def main():
    ok = True
    for t in (test_reads_the_limits_a_gimbal_reports,
              test_a_camera_with_no_motor_is_not_an_error,
              test_the_backend_uses_those_limits,
              test_a_configured_range_wins,
              test_the_fallback_when_there_is_no_v4l2):
        ok &= bool(t())
    print()
    print("ptz probe: " + ("all checks pass" if ok else "SOMETHING FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
