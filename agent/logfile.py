"""
Somewhere for the agent to talk when there is no console.

Built for the tray, the Windows agent has no console at all - and in a
windowed build Python sets sys.stdout and sys.stderr to None, so the very
first print() would end the program with an AttributeError before it ever
opened a camera. Every print in the agent would have to be guarded, or
output has to go somewhere.

It goes to a file next to the exe. That is better than silence for the
reason that matters later: when a camera stops behaving at three in the
morning, the log is the only account of what it thought it was doing.
"""

import os
import sys
import time
import threading


class _TeeFile:
    """Writes to a file, and to the console as well if there is one."""

    def __init__(self, path, also=None, max_bytes=2 * 1024 * 1024):
        self.path = path
        self.also = also
        self.max_bytes = int(max_bytes)
        self._lock = threading.Lock()
        self._handle = None
        self._open()

    def _open(self):
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            self._handle = open(self.path, "a", encoding="utf-8",
                                errors="replace")
        except OSError:
            self._handle = None

    def _roll_if_big(self):
        """Keep one old log, so a long-running agent cannot fill the disk."""
        try:
            if self._handle is None or self._handle.tell() < self.max_bytes:
                return
            self._handle.close()
            old = self.path + ".old"
            if os.path.exists(old):
                os.remove(old)
            os.replace(self.path, old)
            self._open()
        except OSError:
            pass

    def write(self, text):
        with self._lock:
            if self._handle is not None:
                try:
                    if text.strip():
                        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
                        self._handle.write(f"{stamp}  {text}")
                    else:
                        self._handle.write(text)
                    self._handle.flush()
                    self._roll_if_big()
                except Exception:
                    pass
        if self.also is not None:
            try:
                self.also.write(text)
                self.also.flush()
            except Exception:
                pass
        return len(text)

    def flush(self):
        for target in (self._handle, self.also):
            try:
                if target is not None:
                    target.flush()
            except Exception:
                pass

    def isatty(self):
        return False

    def fileno(self):
        # Something asking for a real file descriptor must be told there
        # is not one, rather than handed the log's by accident.
        raise OSError("no file descriptor here")


def path_next_to_us(name="cabinwatch.log"):
    """Beside the exe, or beside the agent folder when run from source."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def start(path=None):
    """Send print() to a log file. Returns where it went, or None.

    Only takes over when there is no console to print to, or when a path
    is asked for - running from a terminal should still print to that
    terminal. CABINWATCH_LOG names a file to use instead of the default
    one beside the program.
    """
    silent = sys.stdout is None or sys.stderr is None
    chosen = path or os.environ.get("CABINWATCH_LOG") or None
    if not silent and chosen is None:
        return None

    target = chosen or path_next_to_us()
    log = _TeeFile(target, also=sys.__stdout__ if not silent else None)
    sys.stdout = log
    sys.stderr = log
    print(f"--- CabinWatch started {time.strftime('%d %b %Y %H:%M:%S')} ---")
    return target
