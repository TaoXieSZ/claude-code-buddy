"""StackChan dashboard — tiny localhost HTTP server for runtime settings.

Sliders for volume / brightness, dropdown for character pack, toggles
for motion and idle-wiggle. UI talks to this server via JSON POSTs;
this server forwards each setting as a `cmd` frame over BLE to the
firmware, which applies + persists to NVS.

Designed to drop into an existing asyncio daemon (cc-bridge) without
pulling in aiohttp — uses stdlib http.server in a daemon thread, then
hops back to the asyncio loop via run_coroutine_threadsafe for the
single BLE write per request.

Endpoints
---------
  GET  /                — single-page HTML dashboard
  GET  /api/characters  — list of available character pack dirs on the
                          daemon machine (mirrors what's on LittleFS
                          if the user ran `uploadfs` from the same
                          checkout).
  POST /api/cmd         — body: any cmd frame the firmware understands.
                          Body forwarded verbatim over BLE.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_PORT = 18765   # high-port to dodge collisions with adhoc dev servers
DEFAULT_BIND = "127.0.0.1"

DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>StackChan Dashboard</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@500&display=swap" rel="stylesheet">
  <style>
    :root {
      color-scheme: light dark;
      --bg: #fafafa;
      --card: #ffffff;
      --text: #1a1a1a;
      --muted: #6b7280;
      --border: #e5e7eb;
      --accent: #d97757;
      --err: #c44;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #0f0f10;
        --card: #1a1a1c;
        --text: #f5f5f7;
        --muted: #9ca3af;
        --border: #2a2a2d;
      }
    }
    * { box-sizing: border-box; }
    body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
      font-size: 15px;
      line-height: 1.5;
      max-width: 540px;
      margin: 0 auto;
      padding: 40px 24px 60px;
      background: var(--bg);
      color: var(--text);
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
    }
    h1 {
      font-size: 1.7em;
      font-weight: 700;
      letter-spacing: -0.02em;
      margin: 0 0 6px;
    }
    .sub { color: var(--muted); font-size: 0.95em; margin-bottom: 28px; }
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 18px 20px;
      margin-bottom: 14px;
    }
    .row + .row { margin-top: 22px; }
    .label-line {
      display: flex; justify-content: space-between; align-items: baseline;
      margin-bottom: 4px; gap: 12px;
    }
    label.main {
      font-weight: 600; font-size: 1em; color: var(--text);
      cursor: default;
    }
    .val {
      font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, monospace;
      font-size: 0.85em;
      color: var(--muted);
      font-weight: 500;
      white-space: nowrap;
    }
    .help {
      color: var(--muted);
      font-size: 0.85em;
      line-height: 1.45;
      margin: 4px 0 12px;
    }
    input[type=range] {
      width: 100%;
      accent-color: var(--accent);
      height: 4px;
    }
    select {
      width: 100%;
      padding: 9px 12px;
      font-size: 1em;
      font-family: inherit;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--bg);
      color: var(--text);
    }
    .toggle { display: flex; align-items: center; gap: 12px; }
    .toggle input { width: 20px; height: 20px; accent-color: var(--accent); cursor: pointer; }
    .toggle label.main { cursor: pointer; }
    .status {
      color: var(--muted);
      font-size: 0.8em;
      font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, monospace;
      margin-top: 24px;
      padding: 10px 14px;
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 8px;
      word-break: break-all;
    }
    .status.err { color: var(--err); }
    code { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 0.85em; padding: 1px 4px; background: var(--bg); border-radius: 3px; }
  </style>
</head>
<body>
  <h1>StackChan</h1>
  <div class="sub">Live settings — every change saves to the device's NVS and survives reboots.</div>

  <div class="card">
    <div class="row">
      <div class="label-line">
        <label class="main" for="vol">Volume</label>
        <span class="val"><span id="vol_v">96</span> / 255</span>
      </div>
      <p class="help">Speaker output for hook-event WAV clips (PermissionRequest, Stop, PreToolUse, etc.). Below ~40 you won't hear it across a desk; 96 is comfortable at arm's length.</p>
      <input type="range" id="vol" min="0" max="255" value="96">
    </div>

    <div class="row">
      <div class="label-line">
        <label class="main" for="bright">Screen brightness</label>
        <span class="val"><span id="bright_v">200</span> / 255</span>
      </div>
      <p class="help">CoreS3 LCD backlight. 255 is full burn; 200 is comfortable at desk distance; below 60 is hard to read in daylight.</p>
      <input type="range" id="bright" min="0" max="255" value="200">
    </div>

    <div class="row">
      <div class="label-line">
        <label class="main" for="tilt">Head tilt</label>
        <span class="val"><span id="tilt_v">65</span>°</span>
      </div>
      <p class="help">Where the head rests when not actively animating. <code>0°</code> = chin to chest (screen pointed at desk). <code>90°</code> = straight up at the ceiling. <code>50–70°</code> presents the face at typical desk-sitting eye level. Pattern motion (nod, look-around, dance) adds <code>±5–8°</code> on top.</p>
      <input type="range" id="tilt" min="0" max="90" value="65">
    </div>
  </div>

  <div class="card">
    <div class="row">
      <label class="main" for="char">Character pack</label>
      <p class="help">Which sprite pack drives the face. Options come from <code>data/characters/</code> on the daemon machine; flash a new pack with <code>pio run -t uploadfs</code> and it appears here on next page load.</p>
      <select id="char"></select>
    </div>
  </div>

  <div class="card">
    <div class="row toggle">
      <input type="checkbox" id="motion" checked>
      <label class="main" for="motion">Servo motion</label>
    </div>
    <p class="help">Master switch for the head-and-neck servos. Off parks the head at the tilt baseline and silences the motors — useful when screen-recording or in a meeting.</p>
  </div>

  <div class="card">
    <div class="row toggle">
      <input type="checkbox" id="idle_wiggle" checked>
      <label class="main" for="idle_wiggle">Idle wiggle</label>
    </div>
    <p class="help">When the daemon is idle (no Claude activity), peek left/right every ~12 s so the character feels alive. Off makes idle a hard freeze at the tilt baseline.</p>
  </div>

  <div class="status" id="status">ready</div>

<script>
const $ = id => document.getElementById(id);
function setStatus(t, isErr) { const e=$('status'); e.textContent=t; e.className='status'+(isErr?' err':''); }

async function post(cmd) {
  try {
    const r = await fetch('/api/cmd', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(cmd)});
    if (!r.ok) throw new Error('HTTP '+r.status);
    setStatus('sent '+JSON.stringify(cmd));
    localStorage.setItem('stackchan_'+(cmd.cmd||'?'), JSON.stringify(cmd));
  } catch (e) { setStatus(e.message, true); }
}

$('vol').oninput    = e => $('vol_v').textContent = e.target.value;
$('vol').onchange   = e => post({cmd:'vol', v: +e.target.value});
$('bright').oninput  = e => $('bright_v').textContent = e.target.value;
$('bright').onchange = e => post({cmd:'bright', v: +e.target.value});
$('tilt').oninput  = e => $('tilt_v').textContent = e.target.value;
$('tilt').onchange = e => post({cmd:'tilt', v: +e.target.value});
$('motion').onchange      = e => post({cmd:'motion', enabled: e.target.checked});
$('idle_wiggle').onchange = e => post({cmd:'idle_wiggle', enabled: e.target.checked});
$('char').onchange        = e => post({cmd:'char', name: e.target.value});

// Populate character dropdown from /api/characters.
fetch('/api/characters').then(r=>r.json()).then(list => {
  const sel = $('char');
  list.forEach(n => { const o = document.createElement('option'); o.value=n; o.textContent=n; sel.appendChild(o); });
  const saved = localStorage.getItem('stackchan_char');
  if (saved) { try { sel.value = JSON.parse(saved).name; } catch(e){} }
});

// Restore sliders + toggles from localStorage so the UI reflects last push.
['vol','bright','tilt'].forEach(k => {
  const s = localStorage.getItem('stackchan_'+k);
  if (s) { try { const v = JSON.parse(s).v; $(k).value = v; $(k+'_v').textContent = v; } catch(e){} }
});
['motion','idle_wiggle'].forEach(k => {
  const s = localStorage.getItem('stackchan_'+k);
  if (s) { try { $(k).checked = JSON.parse(s).enabled; } catch(e){} }
});
</script>
</body>
</html>"""


def _characters_dir() -> Path:
    # Mirror what uploadfs will push. data/characters/ is project's
    # canonical staging dir for the LittleFS image.
    here = Path(__file__).resolve()
    # tools/cc-bridge/dashboard.py → repo root is parents[2]
    return here.parents[2] / "data" / "characters"


def _list_characters() -> list[str]:
    d = _characters_dir()
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir())


class _Handler(BaseHTTPRequestHandler):
    # Class-level injected via ThreadingHTTPServer.context
    _ble = None
    _loop: asyncio.AbstractEventLoop | None = None
    _log: logging.Logger | None = None

    def log_message(self, fmt, *a):
        if self._log:
            self._log.debug("[dash] " + (fmt % a))

    def _send_json(self, code: int, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        p = urlparse(self.path).path
        if p in ("/", "/index.html"):
            body = DASHBOARD_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if p == "/api/characters":
            self._send_json(200, _list_characters())
            return
        self.send_error(404)

    def do_POST(self):  # noqa: N802
        if urlparse(self.path).path != "/api/cmd":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length).decode())
        except Exception as e:
            self._send_json(400, {"error": f"bad json: {e}"})
            return
        if not isinstance(payload, dict) or not payload.get("cmd"):
            self._send_json(400, {"error": "payload must be {\"cmd\":\"...\"}"})
            return
        if self._loop is None or self._ble is None:
            self._send_json(503, {"error": "ble not ready"})
            return
        # Bridge thread → asyncio loop. We don't wait on the result
        # (BLE write can take a beat; we want the HTTP response snappy).
        try:
            asyncio.run_coroutine_threadsafe(self._ble.write(payload), self._loop)
        except Exception as e:
            self._send_json(500, {"error": str(e)})
            return
        self._send_json(200, {"ok": True, "sent": payload})


def start_dashboard(ble_writer, loop: asyncio.AbstractEventLoop,
                    log: logging.Logger | None = None,
                    bind: str = DEFAULT_BIND, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """Spin up the dashboard HTTP server on a daemon thread.

    Returns the server so the caller can shut it down on exit.
    """
    _Handler._ble = ble_writer
    _Handler._loop = loop
    _Handler._log = log or logging.getLogger(__name__)

    # allow_reuse_address smooths daemon restarts when a previous bind
    # is in TIME_WAIT. macOS launchd kicks us hard enough that this
    # matters in practice.
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((bind, port), _Handler)
    t = threading.Thread(target=server.serve_forever, name="stackchan-dash",
                         daemon=True)
    t.start()
    if log:
        log.info("dashboard listening on http://%s:%d", bind, port)
    return server
