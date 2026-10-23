"""
Living in the tray instead of on the desktop.

A console window sitting open on a laptop is something people close -
usually by accident, usually the day you needed the footage. It is also
the wrong shape for what this is: the agent has nothing to say most of the
time, and when it does there is a settings page to say it on.

So on Windows the agent is built with no console at all and shows up as a
tray icon: what the cameras are doing, a way into the settings, and a way
to stop it that is clearly a decision rather than a stray click.

If the tray cannot be used - no pystray, no display, a Linux box with no
desktop at all - this says so once and the agent carries on exactly as it
did before, printing to the terminal. A camera that refuses to run because
it has nowhere to put an icon would be a poor trade.
"""

import os
import sys
import queue
import threading
import webbrowser


def available():
    """Can a tray icon be put up on this machine?"""
    try:
        import pystray                            # noqa: F401
        from PIL import Image                     # noqa: F401
    except Exception:
        return False
    if sys.platform.startswith("win") or sys.platform == "darwin":
        return True
    # A Linux box without a desktop has nowhere to put it.
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _icon_image(watching=True):
    """A small camera, drawn rather than shipped as a file.

    Drawing it keeps the one-file build to one file and means the icon can
    say something: it dims when every camera has stopped.
    """
    from PIL import Image, ImageDraw

    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    body = (88, 166, 255, 255) if watching else (110, 118, 129, 255)
    draw.rounded_rectangle((6, 20, 44, 48), radius=6, fill=body)
    draw.polygon([(46, 28), (58, 20), (58, 48), (46, 40)], fill=body)
    # The lens, and a red dot when it is actually recording.
    draw.ellipse((18, 28, 32, 42), fill=(13, 17, 23, 255))
    if watching:
        draw.ellipse((48, 8, 58, 18), fill=(248, 81, 73, 255))
    return image


class Tray:
    """The icon, its menu, and the thread it has to live on."""

    def __init__(self, supervisor, panel_url=None, on_quit=None):
        self.supervisor = supervisor
        self.panel_url = panel_url
        self.on_quit = on_quit
        self.icon = None
        self._thread = None
        self._stop = threading.Event()
        self._errors = queue.Queue()

    # -- what the menu says --------------------------------------------------
    def _summary(self):
        sessions = list(getattr(self.supervisor, "sessions", {}).values())
        if not sessions:
            return "No camera running"
        live = sum(1 for s in sessions if getattr(s, "connected", False))
        return (f"{live} of {len(sessions)} connected"
                if len(sessions) != 1
                else ("Connected" if live else "Not connected"))

    def _camera_lines(self):
        lines = []
        for cam_id, session in getattr(self.supervisor, "sessions", {}).items():
            status = getattr(session, "status", "?")
            if getattr(session, "rec", None) and session.rec.recording:
                status += ", recording"
            lines.append(f"{getattr(session, 'name', cam_id)}: {status}")
        return lines or ["nothing configured"]

    def _open_panel(self):
        if self.panel_url:
            webbrowser.open(self.panel_url)

    def _open_clips(self):
        folder = os.path.abspath(self.supervisor.cfg.CLIPS_DIR)
        try:
            if sys.platform.startswith("win"):
                os.startfile(folder)              # noqa: S606
            elif sys.platform == "darwin":
                os.system(f'open "{folder}"')
            else:
                os.system(f'xdg-open "{folder}" >/dev/null 2>&1 &')
        except Exception:
            pass

    def _menu(self):
        import pystray

        def camera_items():
            for line in self._camera_lines():
                yield pystray.MenuItem(line, None, enabled=False)

        return pystray.Menu(
            pystray.MenuItem(lambda _: self._summary(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            *list(camera_items()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Settings...", lambda: self._open_panel(),
                             default=True, enabled=bool(self.panel_url)),
            pystray.MenuItem("Open the recordings folder",
                             lambda: self._open_clips()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Stop watching and quit", lambda: self.quit()),
        )

    # -- lifecycle -----------------------------------------------------------
    def start(self):
        """Put the icon up. False means carry on without one."""
        if not available():
            return False
        try:
            import pystray
            self.icon = pystray.Icon(
                "cabinwatch", _icon_image(True), "CabinWatch", self._menu())
        except Exception as e:
            print(f"[tray] could not create the icon: {e}")
            return False

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        threading.Thread(target=self._refresh_loop, daemon=True).start()
        return True

    def _run(self):
        try:
            self.icon.run()
        except Exception as e:
            print(f"[tray] the icon stopped: {e}")
            self._errors.put(e)

    def _refresh_loop(self):
        """Keep the icon honest about whether anything is actually running."""
        watching = None
        while not self._stop.wait(5.0):
            sessions = getattr(self.supervisor, "sessions", {}).values()
            now = any(getattr(s, "connected", False) for s in sessions)
            if now != watching:
                watching = now
                try:
                    self.icon.icon = _icon_image(now)
                except Exception:
                    pass
            try:
                self.icon.title = f"CabinWatch - {self._summary()}"
            except Exception:
                pass

    def quit(self):
        self._stop.set()
        if self.on_quit is not None:
            try:
                self.on_quit()
            except Exception:
                pass
        self.stop()

    def stop(self):
        self._stop.set()
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass
            self.icon = None
