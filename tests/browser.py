"""
Finding a browser to drive, wherever this is being run.

The dashboard is a page, and some of what it has to get right - a dropdown
that stays open, a card that does not flicker, a button that disables itself
- can only be checked by opening it in a real browser. Where that browser
lives differs between a developer's machine, this project's container and a
CI runner, so look rather than assume: a hardcoded path works in exactly one
place and fails silently everywhere else.
"""

import os
import glob


def chromium_path():
    """An explicit Chromium to launch, or None to let Playwright decide."""
    explicit = os.environ.get("CHROMIUM_PATH")
    if explicit and os.path.exists(explicit):
        return explicit

    roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH"), "/opt/pw-browsers",
             os.path.expanduser("~/.cache/ms-playwright")]
    patterns = ("chromium-*/chrome-linux/chrome",
                "chromium-*/chrome-linux/headless_shell",
                "chromium_headless_shell-*/chrome-linux/headless_shell",
                "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
                "chromium-*/chrome-win/chrome.exe")
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for pattern in patterns:
            found = sorted(glob.glob(os.path.join(root, pattern)))
            if found:
                return found[-1]

    for where in ("/usr/bin/chromium", "/usr/bin/chromium-browser",
                  "/usr/bin/google-chrome"):
        if os.path.exists(where):
            return where
    return None


def launch(playwright, **kw):
    """Start Chromium, with the sandbox off - these run as root in CI."""
    args = kw.pop("args", ["--no-sandbox", "--disable-dev-shm-usage"])
    path = chromium_path()
    if path:
        return playwright.chromium.launch(executable_path=path, args=args, **kw)
    return playwright.chromium.launch(args=args, **kw)


def available():
    """Can a browser actually be started here?"""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return False
    try:
        with sync_playwright() as pw:
            launch(pw).close()
        return True
    except Exception:
        return False
