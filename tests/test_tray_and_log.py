"""
Living in the tray, with somewhere to talk.

A console window open on a laptop is something people close - usually by
accident, usually the day the footage mattered. So the Windows build is
packaged windowed and shows up as a tray icon instead.

That brings a trap with it that is worth a test of its own. In a windowed
build Python sets sys.stdout and sys.stderr to None, and in that state the
very first print() ends the program with an AttributeError - before a
camera is ever opened, with nothing on screen to say why. The agent prints
within a few lines of starting, about the microphone, so this is not a
theoretical risk; it is the first thing that would happen.

The other half is that the tray has to be optional. A Linux box with no
desktop, a machine without pystray - a camera that refuses to run because
it has nowhere to put an icon would be a poor trade.
"""

import os
import re
import sys
import time
import shutil
import tempfile
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
AGENT = os.path.join(ROOT, "agent")
sys.path.insert(0, AGENT)

import logfile                                   # noqa: E402
import tray as tray_mod                          # noqa: E402

PASSED = []
FAILED = []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(("PASS  " if ok else "FAIL  ") + name
          + (("  -- " + detail) if detail else ""))
    return bool(ok)


def test_the_agent_survives_having_no_console():
    """The windowed case, run for real in a separate interpreter."""
    work = tempfile.mkdtemp(prefix="cabinwatch-noconsole-")
    try:
        script = (
            "import sys, traceback\n"
            f"sys.path.insert(0, {AGENT!r})\n"
            "sys.stdout = None\n"            # exactly what a windowed build
            "sys.stderr = None\n"            # hands the program
            "try:\n"
            "    import agent\n"
            "    print('a camera said something')\n"
            "    open('done.txt','w').write(agent.LOG_PATH or 'no log')\n"
            "except BaseException:\n"
            "    open('err.txt','w').write(traceback.format_exc())\n"
            "    raise SystemExit(2)\n"
        )
        env = dict(os.environ,
                   CABINWATCH_LOG=os.path.join(work, "cabinwatch.log"))
        done = subprocess.run([sys.executable, "-c", script], cwd=work,
                              env=env, capture_output=True, text=True,
                              timeout=180)
        broke = os.path.join(work, "err.txt")
        why = open(broke).read().strip().splitlines()[-1] if os.path.exists(
            broke) else ""
        ok = check("the agent starts with no stdout at all",
                   done.returncode == 0, why)

        marker = os.path.join(work, "done.txt")
        log = open(marker).read().strip() if os.path.exists(marker) else ""
        ok &= check("and it found somewhere to write instead", bool(log)
                    and log != "no log", log)
        if log and os.path.exists(log):
            text = open(log).read()
            ok &= check("what it would have printed is in the log",
                        "a camera said something" in text,
                        text.strip().splitlines()[-1] if text else "")
            ok &= check("with the time against each line, for reading later",
                        bool(re.search(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d",
                                       text)))
        return ok
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_a_terminal_is_left_alone():
    """Run from a terminal it should still print there, not into a file."""
    work = tempfile.mkdtemp(prefix="cabinwatch-console-")
    try:
        script = (
            "import sys\n"
            f"sys.path.insert(0, {AGENT!r})\n"
            "import agent\n"
            "print('TERMINAL OUTPUT')\n"
            "print('log is', agent.LOG_PATH)\n"
        )
        done = subprocess.run([sys.executable, "-c", script], cwd=work,
                              capture_output=True, text=True, timeout=180)
        ok = check("it still prints to a terminal when there is one",
                   "TERMINAL OUTPUT" in (done.stdout or ""))
        said = next((l for l in (done.stdout or "").splitlines()
                     if l.startswith("log is")), "it said nothing")
        ok &= check("and does not start a log file nobody asked for",
                    "log is None" in (done.stdout or ""), said)
        return ok
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_the_log_does_not_grow_for_ever():
    """A camera runs for months; a log that never rolls fills the disk."""
    work = tempfile.mkdtemp(prefix="cabinwatch-roll-")
    try:
        path = os.path.join(work, "cabinwatch.log")
        log = logfile._TeeFile(path, max_bytes=4096)
        for i in range(400):
            log.write(f"line {i} " + "x" * 80 + "\n")
        log.flush()

        size = os.path.getsize(path)
        ok = check("the live log is kept small", size <= 8192, f"{size} bytes")
        ok &= check("and the previous one is kept, not thrown away",
                    os.path.exists(path + ".old"))

        # The newest lines have to be in the live file, not the old one.
        ok &= check("the newest lines are the ones you see first",
                    "line 399" in open(path).read())
        return ok
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_the_tray_is_optional():
    ok = check("it knows whether a tray is possible here",
               isinstance(tray_mod.available(), bool),
               f"available: {tray_mod.available()}")

    # Starting it where it is not possible must say no, not raise.
    class FakeSupervisor:
        sessions = {}

        class cfg:
            CLIPS_DIR = "."

    icon = tray_mod.Tray(FakeSupervisor(), panel_url="http://127.0.0.1:8765")
    try:
        started = icon.start()
        ok &= check("starting it where it cannot run returns a plain no",
                    started in (True, False), str(started))
        if not started:
            ok &= check("and nothing is left behind", icon.icon is None)
    except Exception as e:
        ok &= check("starting it where it cannot run returns a plain no",
                    False, f"{type(e).__name__}: {e}")
    finally:
        icon.stop()

    agent_source = open(os.path.join(AGENT, "agent.py")).read()
    ok &= check("and the agent carries on without one",
                "if icon.start():" in agent_source
                and "icon = None" in agent_source)
    return ok


def test_the_icon_says_whether_anything_is_running():
    """A tray icon that looks the same whatever happens is decoration."""
    try:
        watching = tray_mod._icon_image(True)
        stopped = tray_mod._icon_image(False)
    except Exception as e:
        return check("the icon can be drawn", False, str(e))

    ok = check("the icon is drawn rather than shipped as a file",
               watching.size == (64, 64), str(watching.size))
    ok &= check("and it looks different when nothing is running",
                list(watching.getdata()) != list(stopped.getdata()))
    return ok


def test_the_build_is_windowed_and_checked():
    flow = open(os.path.join(ROOT, ".github", "workflows",
                             "build.yml")).read()
    ok = check("the exe is built with no console", "--windowed" in flow)
    ok &= check("the tray's parts are collected into it",
                "--collect-all pystray" in flow and "--collect-all PIL" in flow)
    ok &= check("and the check waits for it, since it is windowed now",
                "-PassThru -Wait" in flow)
    ok &= check("and reads the log, since there is no console to read",
                "cabinwatch.log" in flow and "Cameras this machine can open"
                in flow)
    return ok


def main():
    for test in (test_the_agent_survives_having_no_console,
                 test_a_terminal_is_left_alone,
                 test_the_log_does_not_grow_for_ever,
                 test_the_tray_is_optional,
                 test_the_icon_says_whether_anything_is_running,
                 test_the_build_is_windowed_and_checked):
        test()
    print()
    print(f"tray and log: {len(PASSED)} passed, {len(FAILED)} failed")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
