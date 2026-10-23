"""
Coming back by itself.

A camera that only runs while somebody has an open terminal is not a
camera. The first power cut, the first Windows update, the first time
somebody closes the window to tidy the desktop - and the room is not being
watched, with nothing to say so except the camera quietly missing from the
dashboard.

Two ways in, depending on the machine:

  Windows   an entry under the current user's Run key. No administrator
            rights, no installer, and it starts when that user logs in.
            The catch is in that last word, and it is said out loud below
            rather than left to be discovered: a laptop sitting at the
            login screen is not running anything.

  Linux     a systemd user service, for a Raspberry Pi left plugged in as
            the bridge to an IP camera. With lingering enabled it starts
            at boot without anybody logging in at all.

Both are written so that removing them leaves no trace behind.
"""

import os
import sys
import shutil
import subprocess

APP_NAME = "CabinWatch"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
UNIT_NAME = "cabinwatch-agent.service"


def frozen():
    """Is this the one-file exe rather than a checkout with Python?"""
    return bool(getattr(sys, "frozen", False))


def agent_script():
    """Where agent.py is, worked out from this file rather than argv.

    argv[0] is whatever started this process, which is "-c" under python -c
    and a stale path after a move; this file sits next to the agent and
    always has.
    """
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "agent.py")


def command():
    """The command line that starts this agent again, quoted for a shell."""
    if frozen():
        return f'"{os.path.abspath(sys.executable)}"'
    script = agent_script()
    # pythonw on Windows keeps a console window from flashing up; it is not
    # always there, so fall back to the interpreter running this.
    runner = sys.executable
    if os.name == "nt":
        quiet = os.path.join(os.path.dirname(runner), "pythonw.exe")
        if os.path.exists(quiet):
            runner = quiet
    return f'"{runner}" "{script}"'


def working_directory():
    """Where it should be started from, so clips land where they always did."""
    if frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(agent_script()))


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------
def _winreg():
    try:
        import winreg
        return winreg
    except ImportError:
        return None


def windows_status(reg=None):
    reg = reg or _winreg()
    if reg is None:
        return None
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = reg.QueryValueEx(key, APP_NAME)
            return value
    except FileNotFoundError:
        return None
    except OSError:
        return None


def windows_install(reg=None):
    reg = reg or _winreg()
    if reg is None:
        return False, "this is not Windows"
    line = command()
    try:
        with reg.CreateKey(reg.HKEY_CURRENT_USER, RUN_KEY) as key:
            reg.SetValueEx(key, APP_NAME, 0, reg.REG_SZ, line)
    except OSError as e:
        return False, f"Windows would not let us write that: {e}"
    return True, line


def windows_remove(reg=None):
    reg = reg or _winreg()
    if reg is None:
        return False, "this is not Windows"
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0,
                         reg.KEY_SET_VALUE) as key:
            reg.DeleteValue(key, APP_NAME)
    except FileNotFoundError:
        return True, "it was not set up to start by itself"
    except OSError as e:
        return False, str(e)
    return True, "removed"


# ---------------------------------------------------------------------------
# Linux
# ---------------------------------------------------------------------------
def unit_text():
    return f"""[Unit]
Description=CabinWatch camera agent
After=network-online.target

[Service]
Type=simple
WorkingDirectory={working_directory()}
ExecStart={command().replace('"', '')}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""


def unit_path():
    return os.path.expanduser(f"~/.config/systemd/user/{UNIT_NAME}")


def linux_status():
    return unit_path() if os.path.exists(unit_path()) else None


def linux_install():
    if not shutil.which("systemctl"):
        return False, "this machine has no systemctl to register with"
    path = unit_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(unit_text())
    except OSError as e:
        return False, str(e)

    for args in (["--user", "daemon-reload"],
                 ["--user", "enable", UNIT_NAME]):
        try:
            subprocess.run(["systemctl"] + args, capture_output=True,
                           timeout=20)
        except Exception as e:
            return False, f"systemctl {' '.join(args)} failed: {e}"
    return True, path


def linux_remove():
    path = unit_path()
    if not os.path.exists(path):
        return True, "it was not set up to start by itself"
    try:
        subprocess.run(["systemctl", "--user", "disable", UNIT_NAME],
                       capture_output=True, timeout=20)
    except Exception:
        pass
    try:
        os.remove(path)
    except OSError as e:
        return False, str(e)
    return True, "removed"


# ---------------------------------------------------------------------------
# What the rest of the agent calls
# ---------------------------------------------------------------------------
def supported():
    return os.name == "nt" or bool(shutil.which("systemctl"))


def status():
    """Where it is registered to start from, or None."""
    return windows_status() if os.name == "nt" else linux_status()


def install():
    return windows_install() if os.name == "nt" else linux_install()


def remove():
    return windows_remove() if os.name == "nt" else linux_remove()


def explain(installed, detail):
    """What to say afterwards, including the part people trip over."""
    if not installed:
        return f"Could not set it to start by itself: {detail}"
    if os.name == "nt":
        return (
            "It will start by itself when you log in to Windows.\n"
            f"    {detail}\n"
            "\n"
            "    One thing to know: this starts when you log in, not when\n"
            "    the machine powers up. A laptop sitting at the login\n"
            "    screen after a power cut is not watching anything. If it\n"
            "    is a machine kept for this, turn on automatic sign-in\n"
            "    (netplwiz) so a restart takes it all the way back."
        )
    return (
        "Registered as a systemd user service.\n"
        f"    {detail}\n"
        "\n"
        "    It starts when you log in. To have it start at boot with\n"
        "    nobody logged in - a Pi left plugged in, say - run:\n"
        "        sudo loginctl enable-linger $USER"
    )
