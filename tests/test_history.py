"""
What the server remembers across a restart, and what it does not.

Events lived only in memory, so every redeploy wiped the lot - and on
Railway a redeploy is how you change anything at all. Push a new version at
eight in the evening and the night's history starts from nothing.

The pictures are deliberately not kept. They are the bulky part, and knowing
that something happened at 03:12 outlives being able to see a thumbnail of
it. Be clear about the rest too: on a host with an ephemeral disk this is
cleared by a redeploy anyway. It covers a restart, not a rebuild.
"""

import os
import sys
import json
import shutil
import tempfile

WORK = tempfile.mkdtemp(prefix="cabinwatch-history-")
LOG = os.path.join(WORK, "events.json")
os.environ.update(AGENT_TOKEN="demo", VIEW_PASSWORD="demo", SECRET_KEY="f",
                  NTFY_TOPIC="", EVENT_LOG=LOG,
                  EVIDENCE_DIR=os.path.join(WORK, "evidence"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.hub import Hub                        # noqa: E402

PASSED = []
FAILED = []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(("PASS  " if ok else "FAIL  ") + name
          + (("  -- " + detail) if detail else ""))
    return bool(ok)


def test_the_history_outlives_a_restart():
    first = Hub()
    first.add_event("cabin", "Cabin", "person", "Someone is there")
    first.add_event("cabin", "Cabin", "impact", "A thud.", thumb=b"JPEGDATA")

    again = Hub()
    ok = check("the events are still there after a restart",
               len(again.events) == 2, f"{len(again.events)} events")
    ok &= check("and they still say what happened",
                [e["detail"] for e in again.events]
                == ["Someone is there", "A thud."],
                str([e["detail"] for e in again.events]))
    ok &= check("the pictures are not carried over",
                all(not e.get("thumb") for e in again.events))
    return ok


def test_new_events_do_not_reuse_old_numbers():
    """Two events with one id would make the thumbnails point at the wrong
    event - the sort of thing you only notice weeks later."""
    hub = Hub()
    before = {e["id"] for e in hub.events}
    hub.add_event("cabin", "Cabin", "motion", "Something moved")
    fresh = hub.events[-1]["id"]
    return check("a new event gets a number of its own",
                 fresh not in before, f"id {fresh}, already used {sorted(before)}")


def test_a_damaged_history_is_not_fatal():
    """A half-written file after a power cut must not stop the server."""
    with open(LOG, "w") as f:
        f.write('[{"id": 1, "kind": "person"')         # cut off mid-write
    hub = Hub()
    ok = check("a truncated history is ignored rather than fatal",
               len(hub.events) == 0, f"{len(hub.events)} events")
    hub.add_event("cabin", "Cabin", "person", "Still working")
    ok &= check("and the server carries on recording new ones",
                len(hub.events) == 1)
    with open(LOG) as f:
        ok &= check("writing over the damaged file fixes it",
                    len(json.load(f)) == 1)
    return ok


def test_it_does_not_grow_without_limit():
    os.remove(LOG)
    hub = Hub()
    for i in range(hub.events.maxlen + 50):
        hub.add_event("cabin", "Cabin", "motion", f"movement {i}")
    with open(LOG) as f:
        rows = json.load(f)
    ok = check("the file is capped at the history length",
               len(rows) == hub.events.maxlen, f"{len(rows)} rows")
    ok &= check("and holds the newest, not the first",
                rows[-1]["detail"].endswith(str(hub.events.maxlen + 49)),
                rows[-1]["detail"])
    return ok


def main():
    try:
        for test in (test_the_history_outlives_a_restart,
                     test_new_events_do_not_reuse_old_numbers,
                     test_a_damaged_history_is_not_fatal,
                     test_it_does_not_grow_without_limit):
            test()
        print()
        print(f"history: {len(PASSED)} passed, {len(FAILED)} failed")
        return 0 if not FAILED else 1
    finally:
        shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
