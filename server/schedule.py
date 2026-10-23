"""
Arming a camera by the clock.

The cabin camera should be watching when nobody is meant to be in the
cabin, and not when you are sitting in it - otherwise you spend the working
day dismissing alerts about yourself, and the evening wondering whether you
remembered to turn it back on. You did not; nobody does.

So each camera can carry a window. Outside it the camera is armed, inside
it the camera is not, and the server keeps pushing that at the camera until
it agrees - which means a camera that was off all night comes back to the
right state by itself rather than to whatever it had when it died.

Times are the viewer's own, not the server's: the server is in a datacentre
and its idea of six in the evening is nobody's.
"""

import os
import json

from . import settings


def _minutes(text):
    """"22:30" -> 1350. None for anything that is not a time of day."""
    try:
        hours, mins = str(text).strip().split(":")
        hours, mins = int(hours), int(mins)
    except (ValueError, AttributeError):
        return None
    if not (0 <= hours < 24 and 0 <= mins < 60):
        return None
    return hours * 60 + mins


class Schedules:
    """Per camera: when it should be armed, and when it should be left be."""

    def __init__(self, path=None):
        self.path = path if path is not None else settings.SCHEDULE_FILE
        self.rows = self._load()

    def _load(self):
        if not self.path:
            return {}
        try:
            with open(self.path) as f:
                rows = json.load(f)
            return rows if isinstance(rows, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception as e:
            print(f"[schedule] could not read {self.path}: {e}")
            return {}

    def _save(self):
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)),
                        exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.rows, f)
            os.replace(tmp, self.path)
        except Exception as e:
            print(f"[schedule] could not write {self.path}: {e}")

    # -- reading and writing one camera's window -----------------------------
    def get(self, cam_id):
        return self.rows.get(cam_id)

    def set(self, cam_id, off_from, off_until, days=None):
        """Leave the camera alone between off_from and off_until.

        Said that way round on purpose: a camera is armed unless you are
        there, so what you are describing is when you are there.
        """
        start, end = _minutes(off_from), _minutes(off_until)
        if start is None or end is None:
            raise ValueError("times must look like 09:30")
        if start == end:
            raise ValueError("that window is empty - the two times are "
                             "the same")
        row = {"off_from": off_from, "off_until": off_until}
        if days:
            wanted = sorted({int(d) for d in days if 0 <= int(d) <= 6})
            if wanted:
                row["days"] = wanted        # 0 is Monday
        self.rows[cam_id] = row
        self._save()
        return row

    def clear(self, cam_id):
        if self.rows.pop(cam_id, None) is None:
            return False
        self._save()
        return True

    # -- what it means right now ---------------------------------------------
    def should_be_armed(self, cam_id, when=None):
        """True, False, or None when this camera is not on a schedule."""
        row = self.rows.get(cam_id)
        if not row:
            return None

        when = when or settings.now()
        days = row.get("days")
        if days is not None and when.weekday() not in days:
            return True              # not a day you are there: keep watch

        start = _minutes(row.get("off_from"))
        end = _minutes(row.get("off_until"))
        if start is None or end is None:
            return None

        now = when.hour * 60 + when.minute
        if start < end:
            inside = start <= now < end
        else:
            inside = now >= start or now < end      # the window crosses midnight
        return not inside

    def describe(self, cam_id):
        row = self.rows.get(cam_id)
        if not row:
            return None
        out = dict(row)
        out["armed_now"] = self.should_be_armed(cam_id)
        return out
