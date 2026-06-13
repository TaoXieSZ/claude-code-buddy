"""cursor-bridge stick dashboard.

Minimal localhost HTTP server that surfaces the latest stick telemetry
(battery + IMU + BLE connection) pushed by the Plus1 + RoverC firmware.
Read-only — no settings/control here yet (cursor-bridge has no firmware
runtime cmds the way cc-bridge has for StackChan).

Endpoints
---------
  GET /            single-page HTML, auto-refreshes /api/stick every 3 s
  GET /api/stick   JSON: latest telemetry + connection state

Pattern intentionally mirrors tools/cc-bridge/dashboard.py so a future
unification refactor is straightforward.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

DEFAULT_PORT = 18766   # cc-bridge uses 18765; +1 for cursor-bridge
DEFAULT_BIND = "127.0.0.1"

DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Cursor Stick</title>
  <style>
    :root { color-scheme: light dark; --bg:#fafafa; --card:#fff; --text:#1a1a1a; --muted:#6b7280; --border:#e5e7eb; --accent:#d97757; --err:#c44; --ok:#16a34a; }
    @media (prefers-color-scheme: dark) {
      :root { --bg:#0f0f10; --card:#1a1a1c; --text:#f5f5f7; --muted:#9ca3af; --border:#2a2a2d; }
    }
    body { font-family:-apple-system,BlinkMacSystemFont,Inter,system-ui,sans-serif; max-width:540px; margin:0 auto; padding:32px 24px; background:var(--bg); color:var(--text); }
    h1 { font-size:1.5em; margin:0 0 4px; letter-spacing:-0.02em; }
    .sub { color:var(--muted); font-size:0.9em; margin-bottom:24px; }
    .card { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:16px 18px; margin-bottom:12px; }
    .row { display:flex; justify-content:space-between; align-items:baseline; gap:12px; }
    .row + .row { margin-top:10px; }
    .k { color:var(--muted); font-size:0.85em; }
    .v { font-family:ui-monospace,SFMono-Regular,monospace; font-size:0.95em; }
    .pill { display:inline-block; padding:2px 8px; border-radius:999px; font-size:0.78em; font-family:ui-monospace,monospace; }
    .pill.ok { background:rgba(22,163,74,0.15); color:var(--ok); }
    .pill.err { background:rgba(196,68,68,0.18); color:var(--err); }
    .pill.warn { background:rgba(217,119,87,0.2); color:var(--accent); }
    .stamp { color:var(--muted); font-size:0.78em; font-family:ui-monospace,monospace; margin-top:18px; }
  </style>
</head>
<body>
  <h1>Cursor stick</h1>
  <div class="sub">Live telemetry from the Plus1 + RoverC HAT firmware. Updates every 3 s.</div>

  <div class="card">
    <div class="row"><span class="k">connection</span><span id="conn" class="pill warn">…</span></div>
    <div class="row"><span class="k">peer</span><span class="v" id="peer">—</span></div>
  </div>

  <div class="card">
    <div class="row"><span class="k">battery</span><span class="v" id="bat">—</span></div>
    <div class="row"><span class="k">usb</span><span class="v" id="usb">—</span></div>
  </div>

  <div class="card">
    <div class="row"><span class="k">imu (g)</span><span class="v" id="imu">—</span></div>
    <div class="row"><span class="k">orientation</span><span class="v" id="ori">—</span></div>
  </div>

  <div class="card">
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
      <button id="dance" style="font-size:0.95em;padding:8px 14px;border-radius:8px;border:1px solid var(--border);background:var(--card);color:var(--text);cursor:pointer;">💃 Dance 3s</button>
      <button id="dance_long" style="font-size:0.95em;padding:8px 14px;border-radius:8px;border:1px solid var(--border);background:var(--card);color:var(--text);cursor:pointer;">🎉 Dance 8s</button>
      <span class="k" id="dance_status">—</span>
    </div>
  </div>

  <div class="card">
    <div style="display:flex;gap:18px;align-items:center;">
      <canvas id="joy" width="160" height="160" style="touch-action:none;border-radius:50%;background:rgba(127,127,127,0.08);border:1px solid var(--border);cursor:grab;"></canvas>
      <div style="flex:1;">
        <div class="k" style="margin-bottom:6px;">drive (mouse/touch)</div>
        <div class="v" id="joy_v" style="margin-bottom:14px;">x=0  y=0</div>
        <div class="k" style="margin-bottom:6px;">rotate <span class="v" id="rot_v">0</span></div>
        <input type="range" id="rot" min="-100" max="100" value="0" style="width:100%;accent-color:var(--accent);">
      </div>
    </div>
  </div>

  <div class="stamp" id="stamp">waiting…</div>

<script>
const $ = id => document.getElementById(id);

function fmtIMU(imu) {
  if (!imu) return '—';
  const f = v => (v == null) ? '—' : (+v).toFixed(2).padStart(5, ' ');
  return `ax ${f(imu.ax)}  ay ${f(imu.ay)}  az ${f(imu.az)}`;
}
function fmtOri(imu) {
  if (!imu || imu.az == null) return '—';
  const az = +imu.az;
  if (az >  0.7) return 'face up';
  if (az < -0.7) return 'face down';
  return 'edge';
}

async function poll() {
  try {
    const r = await fetch('/api/stick', {cache:'no-store'});
    const d = await r.json();
    const c = $('conn');
    if (d.connected) { c.textContent = 'connected'; c.className = 'pill ok'; }
    else             { c.textContent = 'offline';   c.className = 'pill err'; }
    $('peer').textContent = d.peer || '—';
    const t = d.telemetry;
    if (t && t.bat) {
      $('bat').textContent = `${t.bat.pct}%  ${t.bat.mV} mV`;
      $('usb').textContent = t.bat.usb ? 'plugged' : 'on battery';
    }
    if (t && t.imu) {
      $('imu').textContent = fmtIMU(t.imu);
      $('ori').textContent = fmtOri(t.imu);
    }
    if (t && t.ts) {
      const age = Math.max(0, Math.round(Date.now()/1000 - t.ts));
      $('stamp').textContent = `last telemetry: ${age}s ago`;
    } else {
      $('stamp').textContent = 'no telemetry yet — stick may be disconnected';
    }
  } catch (e) {
    $('stamp').textContent = 'fetch error: ' + e.message;
  }
}
poll();
setInterval(poll, 3000);

// ── joystick ──────────────────────────────────────────────────────────
const joy = $('joy'); const jx = joy.getContext('2d');
const W = joy.width, H = joy.height, R = W/2 - 4;
let knob = {x:0, y:0};   // unit: pixels from center
let dragging = false;
let lastSent = 0;

function drawJoy() {
  jx.clearRect(0,0,W,H);
  jx.strokeStyle = 'rgba(127,127,127,0.4)';
  jx.lineWidth = 1;
  jx.beginPath(); jx.arc(W/2, H/2, R, 0, Math.PI*2); jx.stroke();
  jx.beginPath(); jx.moveTo(W/2-R, H/2); jx.lineTo(W/2+R, H/2); jx.stroke();
  jx.beginPath(); jx.moveTo(W/2, H/2-R); jx.lineTo(W/2, H/2+R); jx.stroke();
  jx.fillStyle = dragging ? '#d97757' : '#888';
  jx.beginPath(); jx.arc(W/2 + knob.x, H/2 + knob.y, 12, 0, Math.PI*2); jx.fill();
}
drawJoy();

function knobFromEvent(e) {
  const r = joy.getBoundingClientRect();
  const t = (e.touches && e.touches[0]) || e;
  const dx = t.clientX - r.left - W/2;
  const dy = t.clientY - r.top  - H/2;
  const d = Math.hypot(dx, dy);
  if (d > R) { knob.x = dx * R/d; knob.y = dy * R/d; }
  else       { knob.x = dx;       knob.y = dy; }
  // normalized to [-1, 1]; screen y is down so flip for "forward = up"
  return { x: knob.x / R, y: -knob.y / R };
}

// RoverC mecanum mix — verbatim from upstream M5_RoverC::setSpeed.
function mecanumMix(x, y, z) {
  let X = x*100, Y = y*100, Z = z*100;
  if (Z !== 0) {
    X = X * (100 - Math.abs(Z)) / 100;
    Y = Y * (100 - Math.abs(Z)) / 100;
  }
  const c = v => Math.max(-100, Math.min(100, Math.round(v)));
  // [b0, b1, b2, b3] in upstream wheel order
  return [ c(Y+X-Z), c(Y-X+Z), c(Y-X-Z), c(Y+X+Z) ];
}

async function sendDrive(x, y, z) {
  const s = mecanumMix(x, y, z);
  try {
    await fetch('/api/drive', {method:'POST', headers:{'Content-Type':'application/json'},
                               body: JSON.stringify({s})});
  } catch (e) { /* ignore — joystick is fire-and-forget */ }
}

function tick(x, y) {
  const z = (+$('rot').value) / 100;
  $('joy_v').textContent = `x=${x.toFixed(2)}  y=${y.toFixed(2)}  z=${z.toFixed(2)}`;
  const now = Date.now();
  if (now - lastSent < 90) return;   // ~11 Hz rate cap
  lastSent = now;
  sendDrive(x, y, z);
}

function start(e) { dragging = true; const v = knobFromEvent(e); tick(v.x, v.y); drawJoy(); e.preventDefault(); }
function move(e)  { if (!dragging) return; const v = knobFromEvent(e); tick(v.x, v.y); drawJoy(); e.preventDefault(); }
function end(e)   { if (!dragging) return; dragging = false; knob.x = 0; knob.y = 0;
                    $('joy_v').textContent = 'x=0  y=0  z=0'; sendDrive(0, 0, 0); drawJoy(); }

joy.addEventListener('mousedown', start);
window.addEventListener('mousemove', move);
window.addEventListener('mouseup', end);
joy.addEventListener('touchstart', start, {passive:false});
joy.addEventListener('touchmove',  move,  {passive:false});
joy.addEventListener('touchend',   end);

async function triggerDance(ms) {
  $('dance_status').textContent = `dancing ${ms/1000}s…`;
  try {
    await fetch('/api/dance', {method:'POST', headers:{'Content-Type':'application/json'},
                               body: JSON.stringify({ms})});
    setTimeout(() => { $('dance_status').textContent = 'done'; }, ms);
  } catch (e) { $('dance_status').textContent = 'err: '+e.message; }
}
$('dance').onclick      = () => triggerDance(3000);
$('dance_long').onclick = () => triggerDance(8000);

$('rot').oninput = e => {
  $('rot_v').textContent = e.target.value;
  // Send rotation even with no joystick — purely spinning in place.
  if (!dragging) sendDrive(0, 0, (+e.target.value)/100);
};
$('rot').onchange = e => {
  // Snap back to 0 on release (momentary)
  e.target.value = 0; $('rot_v').textContent = '0';
  if (!dragging) sendDrive(0, 0, 0);
};
</script>
</body>
</html>"""


class _Handler(BaseHTTPRequestHandler):
    _state = None       # BuddyState
    _ble = None         # BleWriter
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
        if p == "/api/stick":
            client = getattr(self._ble, "client", None) if self._ble else None
            connected = bool(client and getattr(client, "is_connected", False))
            # device_prefix is always set; the actual peer name (e.g.
            # "Cursor-6DE2") isn't latched by BleWriter, so show the
            # prefix as a hint.
            peer = getattr(self._ble, "_device_prefix", "") if self._ble else ""
            payload = {
                "connected": connected,
                "peer": peer,
                "telemetry": self._state.stick_telemetry if self._state else None,
                "now": time.time(),
            }
            self._send_json(200, payload)
            return
        self.send_error(404)

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        if path not in ("/api/drive", "/api/dance"):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length).decode())
        except Exception as e:
            self._send_json(400, {"error": f"bad json: {e}"})
            return
        if self._loop is None or self._ble is None:
            self._send_json(503, {"error": "ble not ready"})
            return

        if path == "/api/dance":
            ms = 3000
            if isinstance(payload, dict) and isinstance(payload.get("ms"), (int, float)):
                ms = max(500, min(15000, int(payload["ms"])))
            try:
                asyncio.run_coroutine_threadsafe(
                    self._ble.write({"cmd": "dance", "ms": ms}), self._loop)
            except Exception as e:
                self._send_json(500, {"error": str(e)})
                return
            self._send_json(200, {"ok": True, "ms": ms})
            return

        s = payload.get("s") if isinstance(payload, dict) else None
        if not (isinstance(s, list) and len(s) == 4 and all(isinstance(v, (int, float)) for v in s)):
            self._send_json(400, {"error": "payload must be {\"s\":[s0,s1,s2,s3]}"})
            return
        # Forward as the firmware-recognised manual-motor cmd. Keepalive
        # window in firmware (1500 ms) means we don't need to send a stop
        # frame on idle — bugc2_manual_tick auto-stops on next ble write
        # round if no follow-up comes.
        try:
            asyncio.run_coroutine_threadsafe(
                self._ble.write({"cmd": "motor", "s": [int(v) for v in s]}),
                self._loop)
        except Exception as e:
            self._send_json(500, {"error": str(e)})
            return
        self._send_json(200, {"ok": True})


def start_dashboard(state, ble_writer, loop: asyncio.AbstractEventLoop,
                    log: logging.Logger | None = None,
                    bind: str = DEFAULT_BIND, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """Spin up the dashboard HTTP server on a daemon thread."""
    _Handler._state = state
    _Handler._ble = ble_writer
    _Handler._loop = loop
    _Handler._log = log or logging.getLogger(__name__)
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((bind, port), _Handler)
    t = threading.Thread(target=server.serve_forever, name="cursor-stick-dash",
                         daemon=True)
    t.start()
    if log:
        log.info("dashboard listening on http://%s:%d", bind, port)
    return server
