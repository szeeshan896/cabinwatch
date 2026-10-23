"""
A camera that stopped, and what brings it back.

The original ask was to see stopped devices in the panel and turn one back
on from there. The first half is done - they stay listed with the time they
were last heard from. The second half runs into the operating system:
nothing can reach an app that is not running. Android allows exactly one
exception, a Firebase push, and that needs a Firebase project the owner has
to create.

So the phone brings itself back instead, which covers what actually
happens: swiped off the recents list, ended by a battery optimiser, killed
by a manufacturer's cleaner, or the phone rebooted. It checks itself every
quarter of an hour and asks one question - should this be running?

What cannot be done is said out loud on the card, so nobody sits looking at
a grey tile wondering whether to drive over there.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
KOTLIN = os.path.join(ROOT, "android", "app", "src", "main", "java",
                      "com", "cabinwatch")

PASSED = []
FAILED = []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(("PASS  " if ok else "FAIL  ") + name
          + (("  -- " + detail) if detail else ""))
    return bool(ok)


def read(path):
    with open(path) as f:
        return f.read()


def kotlin(name):
    return read(os.path.join(KOTLIN, name))


def test_the_phone_checks_on_itself():
    dog = kotlin("Watchdog.kt")
    ok = check("it asks whether it should be running",
               "if (!settings.running" in dog)
    ok &= check("and does nothing if it already is",
                "if (CameraService.isRunning) return" in dog)
    ok &= check("it keeps the chain going, so one check is not the last",
                dog.index("schedule(context)") < dog.index("if (!settings.running"),
                "rescheduled before the early returns")

    gap = re.search(r"EVERY_MS = (\d+) \* 60 \* 1000L", dog)
    minutes = int(gap.group(1)) if gap else 0
    ok &= check("often enough to matter, rarely enough not to cost battery",
                5 <= minutes <= 30, f"every {minutes} minutes")
    ok &= check("and inexact, because this is a safety net not a stopwatch",
                "RTC_WAKEUP" in dog and "setExact" not in dog)
    return ok


def test_stopping_it_on_purpose_keeps_it_stopped():
    """A watchdog that overrides the off switch is a fault, not a feature."""
    service = kotlin("CameraService.kt")
    block = service[service.index("if (intent?.action == ACTION_STOP)"):]
    return check("stopping it cancels the check too",
                 "Watchdog.cancel(this)" in block[:400], block[:160])


def test_the_cases_that_actually_happen():
    service = kotlin("CameraService.kt")
    ok = check("swiping the app away is covered",
               "onTaskRemoved" in service
               and "Watchdog.schedule(this)" in service)
    ok &= check("and it is not treated as a decision to stop watching",
                "if (settings.running) Watchdog.schedule(this)" in service)

    boot = kotlin("BootReceiver.kt")
    ok &= check("a reboot restarts the camera and the check",
                "Watchdog.schedule(context)" in boot)

    manifest = read(os.path.join(ROOT, "android", "app", "src", "main",
                                 "AndroidManifest.xml"))
    ok &= check("the check is declared, or nothing would ever fire it",
                'android:name=".Watchdog"' in manifest)
    return ok


def test_the_card_says_what_will_bring_it_back():
    page = read(os.path.join(ROOT, "server", "templates", "dashboard.html"))
    ok = check("an offline card explains itself", "backwhen" in page)
    for kind, words in (("android", "checks itself every 15 minutes"),
                        ("browser", "Open the page on that phone again"),
                        ("agent", "start with Windows")):
        ok &= check(f"and differently for a {kind} camera", words in page)
    return ok


def test_the_limit_is_written_down_not_hidden():
    """Promising a remote start that cannot work would be worse than not."""
    dog = kotlin("Watchdog.kt")
    ok = check("the source says what is not possible without Firebase",
               "Firebase" in dog, "")
    readme = read(os.path.join(ROOT, "README.md"))
    ok &= check("and so does the README",
                "Firebase" in readme or "cannot be reached" in readme)
    return ok


def main():
    for test in (test_the_phone_checks_on_itself,
                 test_stopping_it_on_purpose_keeps_it_stopped,
                 test_the_cases_that_actually_happen,
                 test_the_card_says_what_will_bring_it_back,
                 test_the_limit_is_written_down_not_hidden):
        test()
    print()
    print(f"coming back: {len(PASSED)} passed, {len(FAILED)} failed")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
