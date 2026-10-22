"""
A settings page for the machine the camera is plugged into.

The agent is a console program, and a console is a poor place to ask someone
which webcam to use. This serves a small page on this machine only, where you
can see what each camera is looking at, watch the microphone level move while
you talk, pick the devices you want, and press Apply.

It listens on 127.0.0.1 and nothing else. The settings include the server
token, so this page must never be reachable from the network - it is only for
whoever is already sitting at this machine.

    http://127.0.0.1:8765
"""

import os
import copy
import json
import socket
import threading
import webbrowser
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2


class ControlPanel:
    """Serves the settings page and applies what you change on it."""

    def __init__(self, supervisor, host="127.0.0.1", port=8765):
        self.supervisor = supervisor
        self.host = host
        self.port = port
        self.httpd = None
        self.url = f"http://{host}:{port}"

    # -- lifecycle -----------------------------------------------------------
    def start(self, open_browser=False):
        panel = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass                      # the console belongs to the agent

            def _send(self, code, body=b"", ctype="text/plain; charset=utf-8",
                      extra=None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _json(self, obj, code=200):
                self._send(code, json.dumps(obj).encode(),
                           "application/json; charset=utf-8")

            def _body(self):
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    return json.loads(self.rfile.read(n) or b"{}")
                except Exception:
                    return {}

            def do_GET(self):
                path = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(path.query)

                if path.path == "/":
                    self._send(200, PAGE.encode(), "text/html; charset=utf-8")
                elif path.path == "/api/state":
                    self._json(panel.state())
                elif path.path == "/api/devices":
                    self._json(panel.devices())
                elif path.path == "/api/preview":
                    cam = (query.get("cam") or [""])[0]
                    jpeg = panel.preview(cam)
                    if jpeg is None:
                        self._send(404, b"no frame yet")
                    else:
                        self._send(200, jpeg, "image/jpeg")
                else:
                    self._send(404, b"not found")

            def do_POST(self):
                path = urllib.parse.urlparse(self.path).path
                if path == "/api/save":
                    ok, detail = panel.save(self._body())
                    self._json({"ok": ok, "detail": detail},
                               200 if ok else 400)
                elif path == "/api/probe":
                    jpeg = panel.probe(self._body().get("source"))
                    if jpeg is None:
                        self._send(404, b"could not open that camera")
                    else:
                        self._send(200, jpeg, "image/jpeg")
                elif path == "/api/open-folder":
                    panel.open_folder()
                    self._json({"ok": True})
                else:
                    self._send(404, b"not found")

        try:
            self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        except OSError as e:
            print(f"[panel] could not start the settings page on "
                  f"{self.url}: {e}")
            return False

        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        print(f"[panel] settings page: {self.url}")
        if open_browser:
            try:
                webbrowser.open(self.url)
            except Exception:
                pass
        return True

    def stop(self):
        if self.httpd is not None:
            try:
                self.httpd.shutdown()
            except Exception:
                pass

    # -- what the page reads -------------------------------------------------
    def state(self):
        sup = self.supervisor
        data = sup.config_data()
        token = data.get("agent_token", "")
        cams = []
        for cam_id, conf in (data.get("cameras") or {}).items():
            s = sup.sessions.get(cam_id)
            cams.append({
                "id": cam_id,
                "name": conf.get("name", cam_id),
                "source": conf.get("source"),
                "audio_device": conf.get("audio_device"),
                "baby_mode": bool(conf.get("baby_mode")),
                "segment_seconds": conf.get(
                    "segment_seconds", data.get("segment_seconds", 300)),
                # live, only when that camera is actually running
                "status": getattr(s, "status", "stopped") if s else "stopped",
                "connected": bool(getattr(s, "connected", False)) if s else False,
                "recording": bool(s.rec.recording) if s else False,
                "motion": bool(getattr(s, "motion_on", False)) if s else False,
                "audio_level": round(getattr(s.sound, "level", 0.0), 4) if s else 0.0,
                "has_audio": bool(getattr(s, "audio_enabled", False)) if s else False,
                "clips": s.rec.count() if s else 0,
                "disk_free_gb": s.rec.free_gb() if s else None,
            })
        return {
            "server_url": data.get("server_url", ""),
            "token_set": bool(token),
            "token_hint": (token[:4] + "..." + token[-4:]) if len(token) > 10 else "",
            "clips_dir": os.path.abspath(sup.cfg.CLIPS_DIR),
            "retention_hours": data.get("retention_hours", 72),
            "max_disk_gb": data.get("max_disk_gb", 20.0),
            "segment_seconds": data.get("segment_seconds", 300),
            "cameras": cams,
            "ffmpeg": _ffmpeg_ok(),
            "editable": bool(getattr(sup, "editable", True)),
        }

    def devices(self):
        """What this machine can actually open, asked fresh."""
        return {"cameras": _probe_cameras(), "microphones": _list_mics()}

    def preview(self, cam_id):
        s = self.supervisor.sessions.get(cam_id)
        if s is None:
            return None
        with s.frame_lock:
            frame = s.frame
        if frame is None:
            return None
        try:
            h, w = frame.shape[:2]
            if w > 640:
                frame = cv2.resize(frame, (640, int(h * 640 / w)),
                                   interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", frame,
                                   [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            return buf.tobytes() if ok else None
        except Exception:
            return None

    def probe(self, source):
        """One frame from a camera that is not running, for the Test button."""
        if source is None:
            return None
        src = int(source) if str(source).isdigit() else source
        cap = None
        try:
            cap = cv2.VideoCapture(src)
            if not cap.isOpened():
                return None
            for _ in range(5):            # let it warm up
                ok, frame = cap.read()
                if ok and frame is not None:
                    h, w = frame.shape[:2]
                    if w > 640:
                        frame = cv2.resize(frame, (640, int(h * 640 / w)))
                    ok, buf = cv2.imencode(".jpg", frame,
                                           [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                    return buf.tobytes() if ok else None
            return None
        except Exception:
            return None
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass

    def open_folder(self):
        path = os.path.abspath(self.supervisor.cfg.CLIPS_DIR)
        try:
            if os.name == "nt":
                os.startfile(path)                       # noqa: S606
            elif sys_is_mac():
                os.system(f'open "{path}"')
            else:
                os.system(f'xdg-open "{path}" >/dev/null 2>&1 &')
        except Exception as e:
            print(f"[panel] could not open {path}: {e}")

    # -- what the page writes ------------------------------------------------
    def save(self, body):
        """Apply the form. Returns (ok, message)."""
        # A shallow copy shares the nested camera dicts with the running
        # config, so a form that failed validation half way through had
        # already changed the live settings. Work on our own copy.
        data = copy.deepcopy(self.supervisor.config_data())

        server = str(body.get("server_url", "")).strip().rstrip("/")
        if server and not server.startswith(("http://", "https://")):
            server = "https://" + server
        if server:
            data["server_url"] = server

        # An empty token means "leave it alone", so the page never has to
        # show the real one back.
        token = str(body.get("agent_token", "")).strip()
        if token:
            data["agent_token"] = token

        for key, cast in (("retention_hours", float),
                          ("max_disk_gb", float),
                          ("segment_seconds", int)):
            if body.get(key) not in (None, ""):
                try:
                    data[key] = cast(body[key])
                except (TypeError, ValueError):
                    return False, f"{key} is not a number"

        cams = data.setdefault("cameras", {})
        for incoming in body.get("cameras") or []:
            cam_id = incoming.get("id")
            if cam_id not in cams:
                continue
            conf = cams[cam_id]
            if incoming.get("name"):
                conf["name"] = str(incoming["name"]).strip()
            if incoming.get("source") not in (None, ""):
                src = incoming["source"]
                conf["source"] = int(src) if str(src).isdigit() else src
            if "audio_device" in incoming:
                mic = incoming["audio_device"]
                if mic in (None, "", "default"):
                    conf.pop("audio_device", None)
                else:
                    conf["audio_device"] = int(mic) if str(mic).isdigit() else mic
            if "baby_mode" in incoming:
                conf["baby_mode"] = bool(incoming["baby_mode"])
                # Keep the two modes' defaults honest when it is switched.
                if conf["baby_mode"]:
                    conf.pop("person_detection", None)
                else:
                    conf["person_detection"] = True
            if incoming.get("segment_seconds") not in (None, ""):
                try:
                    conf["segment_seconds"] = int(incoming["segment_seconds"])
                except (TypeError, ValueError):
                    pass

        try:
            self.supervisor.apply(data)
        except Exception as e:
            return False, str(e)
        return True, "Saved. The cameras are restarting."


def sys_is_mac():
    import sys
    return sys.platform == "darwin"


def _ffmpeg_ok():
    try:
        import muxer
        return bool(muxer.available())
    except Exception:
        return False


def _probe_cameras(limit=6):
    found = []
    for i in range(limit):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            ok, frame = cap.read()
            if ok and frame is not None:
                h, w = frame.shape[:2]
                found.append({"id": i, "label": f"Camera {i}  ({w}x{h})"})
            cap.release()
    return found


def _list_mics():
    try:
        import sounddevice as sd
    except Exception:
        return []
    try:
        return [{"id": i, "label": d["name"]}
                for i, d in enumerate(sd.query_devices())
                if d.get("max_input_channels", 0) > 0]
    except Exception:
        return []


def free_port(preferred=8765):
    """The preferred port, or the next one that is actually free."""
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return preferred


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CabinWatch settings</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  /* room for the floating Apply bar, or it covers the last card */
  body { margin:0; background:#0d1117; color:#e6edf3; padding:0 0 96px;
         font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
  header { position:sticky; top:0; z-index:5; display:flex; align-items:center;
           gap:12px; padding:13px 18px; background:#161b22f2;
           backdrop-filter:blur(8px); border-bottom:1px solid #30363d; }
  header h1 { margin:0; font-size:17px; }
  header .where { margin-left:auto; font-size:12px; color:#8b949e; }
  main { padding:18px; max-width:980px; margin:0 auto; }

  .card { background:#161b22; border:1px solid #30363d; border-radius:12px;
          padding:16px; margin-bottom:16px; }
  .card h2 { margin:0 0 14px; font-size:12px; color:#8b949e;
             text-transform:uppercase; letter-spacing:.8px; }
  label { display:block; font-size:12px; color:#8b949e; margin:12px 0 5px; }
  input, select { width:100%; padding:10px 12px; font-size:15px;
                  border-radius:8px; border:1px solid #30363d;
                  background:#0d1117; color:#e6edf3; }
  input:focus, select:focus { outline:2px solid #1f6feb; border-color:#1f6feb; }
  .row { display:flex; gap:14px; flex-wrap:wrap; }
  .row > div { flex:1 1 220px; min-width:0; }

  .cam { border:1px solid #30363d; border-radius:10px; padding:14px;
         margin-bottom:12px; background:#0d1117; }
  .cam .head { display:flex; align-items:center; gap:9px; margin-bottom:12px; }
  .cam .head b { font-size:15px; }
  .dot { width:8px; height:8px; border-radius:50%; background:#6e7681; }
  .dot.on { background:#3fb950; box-shadow:0 0 6px #3fb950; }
  .cam .head .st { margin-left:auto; font-size:11px; color:#8b949e; }

  .split { display:flex; gap:14px; flex-wrap:wrap; }
  .split .shot { flex:0 0 320px; }
  .split .fields { flex:1 1 260px; min-width:0; }
  .shot img { width:100%; aspect-ratio:16/9; object-fit:contain;
              background:#010409; border-radius:8px; display:block; }
  .shot .none { width:100%; aspect-ratio:16/9; background:#010409;
                border-radius:8px; display:grid; place-items:center;
                color:#6e7681; font-size:12px; text-align:center; padding:10px; }

  .meter { height:8px; background:#21262d; border-radius:4px; overflow:hidden;
           margin-top:9px; }
  .meter > i { display:block; height:100%; width:0; background:#3fb950;
               transition:width .12s; }
  .meter > i.hot { background:#f85149; }
  .mlabel { font-size:11px; color:#6e7681; margin-top:5px; }

  button { padding:11px 16px; font-size:15px; font-weight:600; border:0;
           border-radius:9px; cursor:pointer; background:#238636; color:#fff; }
  button:hover { background:#2ea043; }
  button.alt { background:#21262d; color:#e6edf3; border:1px solid #30363d;
               font-weight:500; font-size:13px; padding:8px 12px; }
  button.alt:hover { background:#30363d; }
  button:disabled { background:#30363d; color:#6e7681; cursor:default; }

  .bar { position:fixed; left:0; right:0; bottom:0; padding:12px 18px;
         background:#161b22f2; backdrop-filter:blur(8px);
         border-top:1px solid #30363d; display:flex; align-items:center;
         gap:14px; }
  .bar .msg { font-size:13px; color:#8b949e; }
  .bar .msg.ok { color:#3fb950; }
  .bar .msg.bad { color:#f85149; }
  .note { font-size:12px; color:#6e7681; margin-top:9px; }
  .warn { margin-top:12px; padding:10px 12px; border-radius:8px; font-size:13px;
          background:#2b2000; border:1px solid #6b4b00; color:#ffd98a; }
</style>
</head>
<body>
<header>
  <h1>CabinWatch settings</h1>
  <span class="where" id="where"></span>
</header>

<main>
  <div class="card">
    <h2>Cameras on this machine</h2>
    <div id="cams"></div>
    <div class="note">
      The picture updates live. Change a camera or microphone and press Apply
      at the bottom &mdash; that camera restarts with the new one.
    </div>
  </div>

  <div class="card">
    <h2>Recording</h2>
    <div class="row">
      <div>
        <label for="seg">Length of each saved file</label>
        <select id="seg">
          <option value="60">1 minute</option>
          <option value="180">3 minutes</option>
          <option value="300">5 minutes</option>
          <option value="600">10 minutes</option>
          <option value="1800">30 minutes</option>
        </select>
      </div>
      <div>
        <label for="ret">Keep recordings for</label>
        <select id="ret">
          <option value="12">12 hours</option>
          <option value="24">1 day</option>
          <option value="72">3 days</option>
          <option value="168">7 days</option>
          <option value="336">14 days</option>
        </select>
      </div>
      <div>
        <label for="disk">Disk space it may use</label>
        <select id="disk">
          <option value="5">5 GB</option>
          <option value="20">20 GB</option>
          <option value="50">50 GB</option>
          <option value="100">100 GB</option>
          <option value="250">250 GB</option>
        </select>
      </div>
    </div>
    <div class="note" id="clipsdir"></div>
    <div style="margin-top:10px">
      <button class="alt" onclick="openFolder()">Open the recordings folder</button>
    </div>
    <div id="noffmpeg"></div>
  </div>

  <div class="card">
    <h2>Server</h2>
    <label for="url">Server address</label>
    <input id="url" placeholder="https://your-app.up.railway.app">
    <label for="tok">Camera token</label>
    <input id="tok" type="password" placeholder="leave blank to keep the current one">
    <div class="note" id="tokhint"></div>
  </div>
</main>

<div class="bar">
  <button id="save" onclick="save()">Apply</button>
  <span class="msg" id="msg"></span>
</div>

<script>
let state = null, devices = {cameras: [], microphones: []};

async function load() {
  state = await (await fetch('/api/state')).json();
  devices = await (await fetch('/api/devices')).json();
  document.getElementById('where').textContent = state.clips_dir;
  document.getElementById('clipsdir').textContent =
    'Recordings are saved in ' + state.clips_dir;
  document.getElementById('url').value = state.server_url || '';
  document.getElementById('tokhint').textContent = state.token_set
    ? 'A token is already saved (' + state.token_hint + '). Leave the box empty to keep it.'
    : 'No token saved yet.';
  pick('seg', state.segment_seconds);
  pick('ret', state.retention_hours);
  pick('disk', state.max_disk_gb);
  document.getElementById('noffmpeg').innerHTML = state.ffmpeg ? '' :
    '<div class="warn"><b>Recordings will have no sound.</b> ffmpeg was not ' +
    'found on this machine. Install it with: <code>pip install imageio-ffmpeg</code></div>';
  if (state.editable === false) {
    // A hand-written config.py wins over the saved file, so saving here
    // would appear to work and then be ignored. Say so and stop.
    document.getElementById('save').disabled = true;
    msg('These settings come from agent/config.py, so change them there.', 'bad');
  }
  renderCams();
}

function pick(id, value) {
  const el = document.getElementById(id);
  const v = String(Math.round(Number(value)));
  if ([...el.options].some(o => o.value === v)) el.value = v;
}

function renderCams() {
  document.getElementById('cams').innerHTML = state.cameras.map(c => `
    <div class="cam">
      <div class="head">
        <span class="dot ${c.connected ? 'on' : ''}"></span>
        <b>${esc(c.name)}</b>
        <span class="st" id="st-${c.id}"></span>
      </div>
      <div class="split">
        <div class="shot">
          <img id="img-${c.id}" alt="" style="display:none">
          <div class="none" id="none-${c.id}">starting the camera&hellip;</div>
          <div class="meter"><i id="lvl-${c.id}"></i></div>
          <div class="mlabel" id="mlbl-${c.id}">microphone</div>
        </div>
        <div class="fields">
          <label>Name</label>
          <input id="nm-${c.id}" value="${esc(c.name)}">

          <label>Camera</label>
          <select id="src-${c.id}">
            ${devices.cameras.map(d => `<option value="${d.id}"
              ${String(d.id) === String(c.source) ? 'selected' : ''}>${esc(d.label)}</option>`).join('')}
            ${devices.cameras.some(d => String(d.id) === String(c.source)) ? '' :
              `<option value="${esc(c.source)}" selected>${esc(c.source)} (in use or not found)</option>`}
          </select>

          <label>Microphone</label>
          <select id="mic-${c.id}">
            <option value="default">Whatever the system is using</option>
            ${devices.microphones.map(d => `<option value="${d.id}"
              ${String(d.id) === String(c.audio_device) ? 'selected' : ''}>${esc(d.label)}</option>`).join('')}
          </select>

          <label>What it is watching</label>
          <select id="baby-${c.id}">
            <option value="1" ${c.baby_mode ? 'selected' : ''}>A baby &mdash; alert on sounds and any movement</option>
            <option value="0" ${c.baby_mode ? '' : 'selected'}>A cabin or office &mdash; alert only on a person</option>
          </select>

          <div style="margin-top:12px">
            <button class="alt" onclick="test('${c.id}')">Test the selected camera</button>
          </div>
        </div>
      </div>
    </div>`).join('');
}

/* live picture + microphone level */
setInterval(async () => {
  if (!state) return;
  for (const c of state.cameras) {
    const img = document.getElementById('img-' + c.id);
    if (img) {
      const probe = new Image();
      probe.onload = () => {
        img.src = probe.src; img.style.display = 'block';
        const n = document.getElementById('none-' + c.id);
        if (n) n.style.display = 'none';
      };
      probe.src = '/api/preview?cam=' + encodeURIComponent(c.id) + '&t=' + Date.now();
    }
  }
}, 1000);

setInterval(async () => {
  try {
    const s = await (await fetch('/api/state')).json();
    state.cameras.forEach(old => {
      const c = s.cameras.find(x => x.id === old.id);
      if (!c) return;
      const lvl = document.getElementById('lvl-' + c.id);
      if (lvl) {
        lvl.style.width = Math.min(100, c.audio_level * 300) + '%';
        lvl.className = c.audio_level > 0.05 ? 'hot' : '';
      }
      const lbl = document.getElementById('mlbl-' + c.id);
      if (lbl) lbl.textContent = c.has_audio
        ? 'microphone — say something, the bar should move'
        : 'no microphone on this camera';
      const st = document.getElementById('st-' + c.id);
      if (st) st.textContent =
        (c.connected ? 'connected' : c.status) +
        (c.recording ? ' · recording' : '') +
        (c.clips ? ' · ' + c.clips + ' clips' : '');
    });
  } catch (e) {}
}, 1500);

async function test(id) {
  const src = document.getElementById('src-' + id).value;
  msg('Opening that camera…');
  try {
    const r = await fetch('/api/probe', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({source: src})
    });
    if (!r.ok) { msg('Could not open it. Is another program using it?', 'bad'); return; }
    const blob = await r.blob();
    const img = document.getElementById('img-' + id);
    img.src = URL.createObjectURL(blob); img.style.display = 'block';
    document.getElementById('none-' + id).style.display = 'none';
    msg('That is what it sees. Press Apply to switch to it.', 'ok');
  } catch (e) { msg('Could not open it: ' + e, 'bad'); }
}

async function save() {
  const btn = document.getElementById('save');
  btn.disabled = true; msg('Saving…');
  const body = {
    server_url: document.getElementById('url').value,
    agent_token: document.getElementById('tok').value,
    segment_seconds: document.getElementById('seg').value,
    retention_hours: document.getElementById('ret').value,
    max_disk_gb: document.getElementById('disk').value,
    cameras: state.cameras.map(c => ({
      id: c.id,
      name: document.getElementById('nm-' + c.id).value,
      source: document.getElementById('src-' + c.id).value,
      audio_device: document.getElementById('mic-' + c.id).value,
      baby_mode: document.getElementById('baby-' + c.id).value === '1'
    }))
  };
  try {
    const r = await fetch('/api/save', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    });
    const j = await r.json();
    msg(j.detail || (j.ok ? 'Saved.' : 'Did not save.'), j.ok ? 'ok' : 'bad');
    if (j.ok) {
      document.getElementById('tok').value = '';
      [3000, 7000, 12000].forEach(ms => setTimeout(load, ms));
    }
  } catch (e) { msg('Did not save: ' + e, 'bad'); }
  btn.disabled = false;
}

async function openFolder() {
  await fetch('/api/open-folder', {method: 'POST'});
  msg('Opened the folder on this machine.', 'ok');
}

function msg(text, kind) {
  const el = document.getElementById('msg');
  el.textContent = text; el.className = 'msg ' + (kind || '');
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g,
    m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
}

load();
</script>
</body>
</html>
"""
