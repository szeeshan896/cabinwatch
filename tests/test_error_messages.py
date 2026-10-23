import sys, time, threading, http.server
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
sys.path.insert(0, _os.path.join(_ROOT, "agent"))
import agent as A

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(404); self.end_headers(); self.wfile.write(b"nope")
    def log_message(self, *a): pass

srv = http.server.HTTPServer(("127.0.0.1", 8123), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()

class Cfg:
    SERVER_URL="http://127.0.0.1:8123"; AGENT_TOKEN="t"; VERIFY_TLS=True
    CLIPS_DIR="/tmp/x"; SEGMENT_SECONDS=60; RETENTION_HOURS=1
    EVENT_RETENTION_HOURS=1; MAX_DISK_GB=1.0; ALWAYS_RECORD=False
    PREBUFFER_SECONDS=1; CAPTURE_FPS=5.0; MOTION_MIN_AREA=1500
    MOTION_THRESHOLD=25; SOUND_SENSITIVITY=1.0; CRY_SECONDS=1.2
    EVENT_MIN_GAP=25.0; AUDIO_DEVICE=None; SPEAKER_DEVICE=None
    SSD_PROTO=SSD_MODEL=None; CAMERAS={}

s = A.CameraSession("office-cabin", {"name":"Office Cabin","source":"none",
                                     "audio":False,"speaker":False}, Cfg)
threading.Thread(target=s.ws_loop, daemon=True).start()
time.sleep(3)
s._stop.set()
