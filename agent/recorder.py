"""
Recording, on the machine the camera is attached to.

Footage never leaves the device unless you ask for a specific clip from the
dashboard. That means a power cut or an internet outage at the cabin does not
cost you the evidence, and your hosting bill does not grow with your footage.

How it works
------------
Video is written as one file per minute into rolling/. A janitor thread
deletes the oldest files once they pass the retention window or the disk
budget. When movement or a sound fires an event, the minute it happened in is
marked "keep": those files get a much longer retention, so the moment
somebody walked into your cabin is still there a week later while the boring
hours have already been recycled.
"""

import os
import time
import json
import wave
import shutil
import threading
import datetime
import contextlib

import cv2

import muxer


class Recorder:
    def __init__(self, cam_id, clips_dir, fps=15.0, segment_seconds=300,
                 retention_hours=72, event_retention_hours=336,
                 max_disk_gb=20.0, always_record=True, prebuffer_seconds=6,
                 audio_rate=16000, want_audio=True):
        self.cam_id = cam_id
        self.dir = os.path.join(clips_dir, cam_id)
        self.fps = float(fps)
        self.segment_seconds = int(segment_seconds)
        self.retention_hours = float(retention_hours)
        self.event_retention_hours = float(event_retention_hours)
        self.max_disk_bytes = float(max_disk_gb) * 1024 ** 3
        self.always_record = bool(always_record)
        self.prebuffer_seconds = int(prebuffer_seconds)

        # Half-written video and its sound live here until they are joined,
        # so a listing of finished clips never has to know about them.
        self.tmp_dir = os.path.join(self.dir, ".tmp")
        os.makedirs(self.dir, exist_ok=True)
        os.makedirs(self.tmp_dir, exist_ok=True)

        self._muxes = []                     # joins still running
        self._recover_tmp()

        self._keep_path = os.path.join(self.dir, "keep.json")
        self._keep = self._load_keep()

        self.audio_rate = int(audio_rate)
        self.want_audio = bool(want_audio)
        self._wav = None
        self._video_tmp = None
        self._audio_tmp = None
        self._frames = 0

        self._writer = None
        self._current = None
        self._started_at = 0.0
        self._size = None
        self._lock = threading.Lock()

        # Only used when always_record is off: holds the seconds before an
        # event so a clip does not begin after the interesting moment.
        from collections import deque
        self._prebuffer = deque(maxlen=max(1, int(fps * prebuffer_seconds)))

        self.manual = False
        # Pausing beats everything else, including round-the-clock recording.
        # Without this the Stop button did nothing at all: it cleared manual
        # and always_record carried on regardless.
        self.paused = False
        self._event_until = 0.0

        self._stop = threading.Event()
        self._janitor = threading.Thread(target=self._janitor_loop, daemon=True)
        self._janitor.start()

    # -- keep-list -----------------------------------------------------------
    def _recover_tmp(self):
        """Finish clips a crash or a power cut left half-joined.

        These used to be deleted on startup, which threw away the most
        recent footage every time - exactly the footage somebody stopping
        the agent after an incident would want.
        """
        try:
            names = os.listdir(self.tmp_dir)
        except FileNotFoundError:
            return

        stamps = sorted({n.rsplit(".", 2)[0] for n in names
                         if n.endswith((".video.mp4", ".audio.wav"))})
        for ts in stamps:
            video = os.path.join(self.tmp_dir, f"{ts}.video.mp4")
            audio = os.path.join(self.tmp_dir, f"{ts}.audio.wav")
            final = os.path.join(self.dir, f"{ts}.mp4")
            if not os.path.isfile(video) or os.path.getsize(video) < 2048:
                for p2 in (video, audio):
                    with contextlib.suppress(OSError):
                        os.remove(p2)
                continue
            if os.path.isfile(final):
                continue
            print(f"[rec {self.cam_id}] recovering {ts}.mp4 left by a "
                  f"previous run")
            # No frame count survived the crash, so no timing correction.
            self._muxes.append(muxer.mux(
                video, audio if os.path.isfile(audio) else None, final,
                declared_fps=self.fps, frames=0, seconds=0))

        # Anything else in there is not part of a clip.
        for n in names:
            if not n.endswith((".video.mp4", ".audio.wav")):
                with contextlib.suppress(OSError):
                    os.remove(os.path.join(self.tmp_dir, n))

    def _load_keep(self):
        try:
            with open(self._keep_path) as f:
                return set(json.load(f))
        except Exception:
            return set()

    def _save_keep(self):
        try:
            with open(self._keep_path, "w") as f:
                json.dump(sorted(self._keep), f)
        except Exception:
            pass

    def mark_event(self, hold_seconds=20):
        """The clip being written now is worth keeping."""
        with self._lock:
            if self._current:
                self._keep.add(self._current)
                self._save_keep()
            self._event_until = time.time() + hold_seconds

    # -- writing -------------------------------------------------------------
    @property
    def recording(self):
        return self._writer is not None

    @property
    def current(self):
        return self._current

    def should_record(self):
        if self.paused:
            return False
        if self.manual:
            return True
        if self.always_record:
            return True
        return time.time() < self._event_until

    def write(self, frame):
        """Feed one annotated frame. Handles starting and rolling segments."""
        now = time.time()

        if not self.should_record():
            self._prebuffer.append(frame)
            if self._writer is not None:
                self._close()
            return

        if self._writer is not None and \
                now - self._started_at >= self.segment_seconds:
            self._close()

        if self._writer is None:
            self._open(frame)
            # When recording only on events, lead in with what came before.
            if not self.always_record and self._writer is not None:
                for f in list(self._prebuffer):
                    try:
                        self._writer.write(f)
                    except Exception:
                        break
                self._prebuffer.clear()

        if self._writer is not None:
            try:
                self._writer.write(frame)
                self._frames += 1
            except Exception as e:
                print(f"[rec {self.cam_id}] write failed: {e}")
                self._close()

    def write_audio(self, pcm_bytes):
        """Feed 16-bit mono samples for the segment being written.

        Comes straight from the microphone the agent already has open for
        its sound alerts, so nothing competes for the device.
        """
        wav = self._wav
        if wav is None or not pcm_bytes:
            return
        try:
            wav.writeframes(pcm_bytes)
        except Exception:
            pass

    def _open(self, frame):
        h, w = frame.shape[:2]
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        name = f"{ts}.mp4"
        video_tmp = os.path.join(self.tmp_dir, f"{ts}.video.mp4")
        audio_tmp = os.path.join(self.tmp_dir, f"{ts}.audio.wav")

        # mp4v is the codec OpenCV can always write without extra packages.
        writer = cv2.VideoWriter(video_tmp, cv2.VideoWriter_fourcc(*"mp4v"),
                                 self.fps, (w, h))
        if not writer.isOpened():
            print(f"[rec {self.cam_id}] could not open {video_tmp}")
            return

        wav = None
        if self.want_audio and muxer.available():
            try:
                wav = wave.open(audio_tmp, "wb")
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(self.audio_rate)
            except Exception as e:
                print(f"[rec {self.cam_id}] could not start the sound "
                      f"track: {e}")
                wav = None
        elif self.want_audio:
            muxer.warn_once()

        with self._lock:
            self._writer = writer
            self._wav = wav
            self._video_tmp = video_tmp
            self._audio_tmp = audio_tmp if wav is not None else None
            self._frames = 0
            self._current = name
            self._size = (w, h)
            self._started_at = time.time()
            if time.time() < self._event_until:
                self._keep.add(name)
                self._save_keep()

    def _close(self):
        with self._lock:
            writer, wav = self._writer, self._wav
            video_tmp, audio_tmp = self._video_tmp, self._audio_tmp
            name, frames = self._current, self._frames
            seconds = time.time() - self._started_at if self._started_at else 0
            self._writer = self._wav = None
            self._video_tmp = self._audio_tmp = None
            self._current = None
            self._frames = 0

        if writer is not None:
            try:
                writer.release()
            except Exception:
                pass
        if wav is not None:
            try:
                wav.close()
            except Exception:
                pass

        if name is None or video_tmp is None:
            return

        # Joining happens off the capture thread, so a slow encode never
        # drops frames or stalls the live view.
        self._muxes = [t for t in self._muxes if t.is_alive()]
        self._muxes.append(muxer.mux(
            video_tmp, audio_tmp, os.path.join(self.dir, name),
            declared_fps=self.fps, frames=frames, seconds=seconds))

    def stop(self, timeout=25.0):
        """Close the open clip and wait for it to finish being written.

        The joining runs on a daemon thread, so returning without waiting
        meant the last segment vanished every time the agent was stopped.
        """
        self._stop.set()
        self._close()
        deadline = time.time() + timeout
        for t in self._muxes:
            left = deadline - time.time()
            if left <= 0:
                print(f"[rec {self.cam_id}] still finishing a clip; it will "
                      f"be recovered on the next start")
                break
            t.join(timeout=left)
        self._muxes = []

    # -- listing and reading -------------------------------------------------
    def list_clips(self, limit=500):
        out = []
        try:
            names = [n for n in os.listdir(self.dir) if n.endswith(".mp4")]
        except FileNotFoundError:
            return out
        names.sort(reverse=True)
        for n in names[:limit]:
            p = os.path.join(self.dir, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            # Skip the file still being written - it will not play yet.
            if n == self._current:
                continue
            out.append({
                "file": n,
                "size_mb": round(st.st_size / 1048576, 1),
                "at": int(st.st_mtime),
                "when": datetime.datetime.fromtimestamp(
                    st.st_mtime).strftime("%d %b %Y, %H:%M:%S"),
                "keep": n in self._keep,
            })
        return out

    def path_of(self, filename):
        """Resolve a clip name to a path, refusing anything outside our dir."""
        if not filename or "/" in filename or "\\" in filename or ".." in filename:
            return None
        p = os.path.realpath(os.path.join(self.dir, filename))
        if not p.startswith(os.path.realpath(self.dir) + os.sep):
            return None
        return p if os.path.isfile(p) else None

    def count(self):
        try:
            return sum(1 for n in os.listdir(self.dir) if n.endswith(".mp4"))
        except FileNotFoundError:
            return 0

    def free_gb(self):
        try:
            return round(shutil.disk_usage(self.dir).free / 1024 ** 3, 1)
        except Exception:
            return None

    def used_bytes(self):
        total = 0
        try:
            for n in os.listdir(self.dir):
                if n.endswith(".mp4"):
                    total += os.path.getsize(os.path.join(self.dir, n))
        except Exception:
            pass
        return total

    # -- retention -----------------------------------------------------------
    def _janitor_loop(self):
        while not self._stop.wait(60):
            try:
                self._sweep()
            except Exception as e:
                print(f"[rec {self.cam_id}] janitor: {e}")

    def _sweep(self):
        try:
            names = [n for n in os.listdir(self.dir) if n.endswith(".mp4")]
        except FileNotFoundError:
            return

        now = time.time()
        files = []
        for n in names:
            if n == self._current:
                continue
            p = os.path.join(self.dir, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            files.append((n, p, st.st_mtime, st.st_size))

        # Age out, with event clips living much longer than ordinary ones.
        total = 0
        survivors = []
        for n, p, mtime, size in files:
            keep = n in self._keep
            limit = (self.event_retention_hours if keep
                     else self.retention_hours) * 3600
            if now - mtime > limit:
                self._delete(n, p)
            else:
                total += size
                survivors.append((n, p, mtime, size, keep))

        # Still over the disk budget: drop the oldest ordinary clips first and
        # only start on event clips when there is nothing else left to give.
        if total > self.max_disk_bytes:
            survivors.sort(key=lambda r: (r[4], r[2]))
            for n, p, mtime, size, keep in survivors:
                if total <= self.max_disk_bytes:
                    break
                self._delete(n, p)
                total -= size

        if self._keep:
            alive = set(os.listdir(self.dir))
            gone = self._keep - alive
            if gone:
                self._keep -= gone
                self._save_keep()

    def _delete(self, name, path):
        try:
            os.remove(path)
        except OSError:
            return
        self._keep.discard(name)
