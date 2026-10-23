"""
Coming back by itself after a restart.

A camera that only runs while somebody has a window open is not a camera.
It survives until the first Windows update, the first power cut, or the
first time somebody tidies the desktop - and then the room is not being
watched, with nothing to say so.

Windows is registered through the registry, which is not here, so that
half is tested against a stand-in that behaves the way the real one does,
including the ways it fails. What is actually being checked is the part
that would be wrong on any machine: that the command written down is one
that will still start this agent tomorrow.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "agent"))

import autostart                               # noqa: E402

PASSED = []
FAILED = []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(("PASS  " if ok else "FAIL  ") + name
          + (("  -- " + detail) if detail else ""))
    return bool(ok)


class FakeRegistry:
    """Enough of winreg to be wrong against, including its exceptions."""

    HKEY_CURRENT_USER = "HKCU"
    REG_SZ = 1
    KEY_SET_VALUE = 2

    def __init__(self, readonly=False):
        self.values = {}
        self.readonly = readonly

    class _Key:
        def __init__(self, reg, path):
            self.reg, self.path = reg, path

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def OpenKey(self, root, path, reserved=0, access=0):
        if path not in self.values:
            raise FileNotFoundError(path)
        return self._Key(self, path)

    def CreateKey(self, root, path):
        if self.readonly:
            raise OSError("access is denied")
        self.values.setdefault(path, {})
        return self._Key(self, path)

    def QueryValueEx(self, key, name):
        try:
            return self.values[key.path][name], self.REG_SZ
        except KeyError:
            raise FileNotFoundError(name)

    def SetValueEx(self, key, name, reserved, kind, value):
        if self.readonly:
            raise OSError("access is denied")
        self.values.setdefault(key.path, {})[name] = value

    def DeleteValue(self, key, name):
        try:
            del self.values[key.path][name]
        except KeyError:
            raise FileNotFoundError(name)


def test_the_command_written_down_still_works_tomorrow():
    """The usual way this breaks: a path taken from how it was started."""
    line = autostart.command()
    ok = check("the agent is named by a full path", os.path.isabs(
        line.split('" "')[-1].strip('"')), line)
    ok &= check("and it is the agent, not whatever launched this",
                line.rstrip('"').endswith("agent.py")
                or autostart.frozen(), line)
    ok &= check("paths are quoted, for the ones with spaces in",
                line.startswith('"') and line.endswith('"'), line)

    where = autostart.working_directory()
    ok &= check("it starts from the folder the clips are under",
                os.path.isdir(where) and
                os.path.exists(os.path.join(where, "agent")), where)
    return ok


def test_windows_registers_and_unregisters():
    reg = FakeRegistry()
    ok = check("nothing is registered to begin with",
               autostart.windows_status(reg) is None)

    done, detail = autostart.windows_install(reg)
    ok &= check("it registers", done, detail)
    ok &= check("under one name, so it cannot pile up",
                list(reg.values[autostart.RUN_KEY]) == ["CabinWatch"],
                str(list(reg.values.get(autostart.RUN_KEY, {}))))
    ok &= check("and reading it back gives the command",
                autostart.windows_status(reg) == autostart.command(),
                str(autostart.windows_status(reg)))

    # Doing it twice must not leave two entries.
    autostart.windows_install(reg)
    ok &= check("asking twice changes nothing",
                len(reg.values[autostart.RUN_KEY]) == 1,
                str(reg.values[autostart.RUN_KEY]))

    done, detail = autostart.windows_remove(reg)
    ok &= check("it unregisters", done, detail)
    ok &= check("and then nothing is registered",
                autostart.windows_status(reg) is None)

    done, detail = autostart.windows_remove(reg)
    ok &= check("removing it again is not an error", done, detail)
    return ok


def test_a_refusal_is_reported_not_swallowed():
    """Locked-down machines exist, and silence would look like success."""
    reg = FakeRegistry(readonly=True)
    done, detail = autostart.windows_install(reg)
    return check("a registry that refuses is reported as a failure",
                 done is False and "not" not in detail[:3], detail)


def test_the_linux_service_says_the_right_things():
    text = autostart.unit_text()
    ok = check("it restarts if it dies", "Restart=always" in text)
    ok &= check("it waits for the network",
                "After=network-online.target" in text)
    ok &= check("it starts the agent", "agent.py" in text or
                autostart.frozen(), text.splitlines()[6])
    ok &= check("it is a user service, needing no root",
                "WantedBy=default.target" in text)
    return ok


def test_what_it_tells_you_afterwards():
    """The trap is that logging in is not the same as powering on."""
    said = autostart.explain(True, "somewhere")
    ok = check("it says what will actually start it",
               "log in" in said, said.splitlines()[0])
    ok &= check("and warns about the case people get caught by",
                ("automatic sign-in" in said) or ("enable-linger" in said),
                said.splitlines()[-1].strip())
    refused = autostart.explain(False, "access is denied")
    ok &= check("a failure reads as a failure",
                refused.startswith("Could not"), refused)
    return ok


def main():
    for test in (test_the_command_written_down_still_works_tomorrow,
                 test_windows_registers_and_unregisters,
                 test_a_refusal_is_reported_not_swallowed,
                 test_the_linux_service_says_the_right_things,
                 test_what_it_tells_you_afterwards):
        test()
    print()
    print(f"autostart: {len(PASSED)} passed, {len(FAILED)} failed")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
