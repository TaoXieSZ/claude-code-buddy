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
  <style>
    :root { color-scheme: light dark; }
    body { font-family: -apple-system, BlinkMacSystemFont, sans-serif;
           max-width: 480px; margin: 32px auto; padding: 0 16px; }
    h1 { font-size: 1.4em; margin-bottom: 0.2em; }
    .sub { color: #888; font-size: 0.9em; margin-bottom: 24px; }
    .row { margin: 18px 0; }
    label { display: block; font-weight: 600; margin-bottom: 6px; }
    input[type=range] { width: 100%; }
    select { width: 100%; padding: 6px; font-size: 1em; }
    .val { font-variant-numeric: tabular-nums; color: #666; }
    .toggle { display: flex; align-items: center; gap: 12px; }
    .toggle input { width: 18px; height: 18px; }
    .status { color: #888; font-size: 0.85em; margin-top: 24px; }
    .err { color: #c44; }
  </style>
</head>
<body>
  <h1>StackChan</h1>
  <div class="sub">live settings — saved on the device</div>

  <div class="row">
    <label>Volume <span class="val" id="vol_v">96</span> / 255</label>
    <input type="range" id="vol" min="0" max="255" value="96">
  </div>

  <div class="row">
    <label>Brightness <span class="val" id="bright_v">200</span> / 255</label>
    <input type="range" id="bright" min="0" max="255" value="200">
  </div>

  <div class="row">
    <label>Character pack</label>
    <select id="char"></select>
  </div>

  <div class="row toggle">
    <input type="checkbox" id="motion" checked>
    <label for="motion" style="margin:0">Servo motion (uncheck for quiet mode)</label>
  </div>

  <div class="row toggle">
    <input type="checkbox" id="idle_wiggle" checked>
    <label for="idle_wiggle" style="margin:0">Idle wiggle (gentle look-around when idle)</label>
  </div>

  <div class="status" id="status">idle</div>

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

// Debounce sliders so we don't flood BLE while dragging.
function debounced(fn, ms) { let t; return (...a)=>{ clearTimeout(t); t=setTimeout(()=>fn(...a), ms); }; }

$('vol').oninput    = e => $('vol_v').textContent = e.target.value;
$('vol').onchange   = e => post({cmd:'vol', v: +e.target.value});

$('bright').oninput  = e => $('bright_v').textContent = e.target.value;
$('bright').onchange = e => post({cmd:'bright', v: +e.target.value});

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

// Restore other sliders from localStorage so the UI reflects last push.
['vol','bright'].forEach(k => {
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
