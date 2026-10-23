#!/usr/bin/env python3
"""
Run every check in this folder and say plainly what failed.

Each test file is a standalone program that prints its own results and
exits non-zero if something is wrong, so this only has to run them and
count. Some need a browser and some need a camera codec; those say so and
are skipped rather than reported as failures, so this can run on a machine
that has not got them.
"""

import os
import sys
import time
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))

# Tests that drive a real browser. They need Playwright and a Chromium.
NEEDS_BROWSER = {
    "test_dashboard_steady.py",
    "test_device_details.py",
}

sys.path.insert(0, HERE)
import browser                                  # noqa: E402


def have_browser():
    return browser.available()


def main():
    files = sorted(f for f in os.listdir(HERE)
                   if f.startswith("test_") and f.endswith(".py"))
    if len(sys.argv) > 1:
        wanted = set(sys.argv[1:])
        files = [f for f in files if f in wanted or f[5:-3] in wanted]

    browser = have_browser()
    if not browser:
        print("No browser available - the browser tests will be skipped.\n")

    failed, skipped, ran = [], [], 0
    widest = max(len(f) for f in files) if files else 10

    for name in files:
        if name in NEEDS_BROWSER and not browser:
            skipped.append(name)
            print(f"{name:<{widest}}  skipped (needs a browser)")
            continue

        started = time.time()
        done = subprocess.run([sys.executable, os.path.join(HERE, name)],
                              capture_output=True, text=True)
        took = time.time() - started
        ran += 1
        if done.returncode == 0:
            print(f"{name:<{widest}}  ok      {took:5.1f}s")
        else:
            failed.append((name, done))
            print(f"{name:<{widest}}  FAILED  {took:5.1f}s")

    print()
    for name, done in failed:
        print(f"--- {name} ---")
        tail = (done.stdout or "").strip().splitlines()[-25:]
        print("\n".join(tail))
        err = (done.stderr or "").strip().splitlines()[-10:]
        if err:
            print("\n".join(err))
        print()

    print(f"{ran} test files run, {len(failed)} failed"
          + (f", {len(skipped)} skipped" if skipped else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
