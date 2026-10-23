"""
Keeping a copy of the footage off the device.

Recordings live on the device that made them, which is the whole design -
no video is shipped anywhere until somebody asks for it, and the data bill
stays small. For the baby that is exactly right.

For the cabin it has a hole in it, and the hole is the point of the camera:
if what gets stolen is the device, the recording of the theft leaves with
it. So when a camera finishes a clip that an alert was raised during, it
says so, and the server pulls a copy and keeps it here.

This store is deliberately small and self-trimming. It holds evidence, not
an archive - the archive is on the device, where there is room for it. And
on a host with an ephemeral disk, Railway among them, a redeploy wipes it:
it is a copy that survives the camera going missing, not one that survives
everything. Anything that matters should be downloaded.
"""

import os
import json
import time
import shutil
import datetime

from . import settings


class Store:
    def __init__(self, folder=None, max_mb=None, days=None, on_removed=None):
        # Called with a key when a copy is dropped, so whatever was pointing
        # at it can stop offering a link to a file that is no longer there.
        self.on_removed = on_removed
        self.folder = folder or settings.EVIDENCE_DIR
        self.max_bytes = (max_mb or settings.EVIDENCE_MAX_MB) * 1024 * 1024
        self.days = days or settings.EVIDENCE_DAYS
        self.index_path = os.path.join(self.folder, "index.json")
        self._index = {}
        try:
            os.makedirs(self.folder, exist_ok=True)
            self._index = self._load()
        except OSError as e:
            print(f"[evidence] cannot use {self.folder}: {e}")

    # -- the list ------------------------------------------------------------
    def _load(self):
        try:
            with open(self.index_path) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self):
        try:
            with open(self.index_path, "w") as f:
                json.dump(self._index, f)
        except OSError:
            pass

    def path_of(self, key):
        """Resolve a saved clip, refusing anything outside the store."""
        if not key or "/" in key or "\\" in key or ".." in key:
            return None
        full = os.path.realpath(os.path.join(self.folder, key))
        if not full.startswith(os.path.realpath(self.folder) + os.sep):
            return None
        return full if os.path.isfile(full) else None

    def key_for(self, cam_id, filename):
        safe_cam = "".join(c if c.isalnum() or c in "-_" else "-"
                           for c in cam_id)[:40]
        safe_file = os.path.basename(filename).replace("/", "-")
        return f"{safe_cam}__{safe_file}"

    def has(self, cam_id, filename):
        return self.key_for(cam_id, filename) in self._index

    def list(self, cam_id=None, limit=200):
        rows = []
        for key, row in self._index.items():
            if cam_id and row.get("camera") != cam_id:
                continue
            rows.append(dict(row, key=key))
        rows.sort(key=lambda r: r.get("at", 0), reverse=True)
        return rows[:limit]

    # -- writing -------------------------------------------------------------
    def begin(self, cam_id, cam_name, filename):
        key = self.key_for(cam_id, filename)
        return key, os.path.join(self.folder, key + ".part")

    def finish(self, key, part_path, cam_id, cam_name, filename, kind=""):
        """Put a completed download into the store and trim to size."""
        final = os.path.join(self.folder, key)
        try:
            os.replace(part_path, final)
        except OSError as e:
            print(f"[evidence] could not store {key}: {e}")
            return None
        self._index[key] = {
            "camera": cam_id,
            "camera_name": cam_name,
            "file": filename,
            "kind": kind,
            "at": time.time(),
            "size_mb": round(os.path.getsize(final) / 1048576, 1),
        }
        self._save()
        self.trim()
        return key

    def abandon(self, part_path):
        try:
            if part_path and os.path.exists(part_path):
                os.remove(part_path)
        except OSError:
            pass

    # -- housekeeping --------------------------------------------------------
    def trim(self):
        """Drop the oldest until the store is inside its limits."""
        now = time.time()
        rows = sorted(self._index.items(), key=lambda kv: kv[1].get("at", 0))

        for key, row in list(rows):
            if now - row.get("at", now) > self.days * 86400:
                self._remove(key)
                rows = [(k, r) for k, r in rows if k != key]

        while self._used() > self.max_bytes and rows:
            key, _ = rows.pop(0)
            self._remove(key)
        self._save()

    def _remove(self, key):
        self._index.pop(key, None)
        if self.on_removed is not None:
            try:
                self.on_removed(key)
            except Exception:
                pass
        try:
            path = os.path.join(self.folder, key)
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass

    def _used(self):
        total = 0
        for key in self._index:
            try:
                total += os.path.getsize(os.path.join(self.folder, key))
            except OSError:
                pass
        return total

    def free_mb(self):
        return round((self.max_bytes - self._used()) / 1048576, 1)
