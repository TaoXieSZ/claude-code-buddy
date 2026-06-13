## Context

The Tab5 composes its whole UI into a single full-frame `M5Canvas spr`
(1280×720, 16-bit RGB565) and pushes dirty bands to the panel over MIPI-DSI.
The sprite's backing buffer is in PSRAM and readable (`spr.getBuffer()` /
`spr.readPixel`). The device is connected to the Mac over USB-CDC serial, owned
by one daemon (currently `cursor-bridge` on `feat/cursor-next`,
`CURSOR_BRIDGE_TAB5_SERIAL`). That daemon's `buddy_core.SerialPortWriter` reads
the port line-by-line and dispatches JSON lines to `on_stick_line`; it also
writes control lines (`cmd:*`) to the device.

So a screenshot needs three pieces: a firmware responder that serializes the
sprite, a daemon-side reader that reassembles it into a PNG (the daemon owns the
port, so it must do the capture), and a way to trigger it.

Constraints:
- The daemon RX is **line-oriented**; only lines starting with `{` reach
  `on_stick_line` today. The screenshot bytes must be framed as text lines so
  they coexist with the normal JSON traffic without a separate binary reader.
- Daemon venvs have `pyserial`/`bleak`/`Quartz` but **PIL is not guaranteed** —
  PNG must be written with the stdlib (`zlib`).
- Full 1280×720×2 = 1.84MB is large; downsample for a quick, legible capture.
- The agent's image `Read` supports PNG (not BMP), so the output must be PNG.

## Goals / Non-Goals

**Goals:**
- One command → a PNG of the Tab5's current screen on the host, no camera.
- Legible enough to judge layout, colors, and text (≥ ~½ resolution).
- No new Python dependency (stdlib PNG), no change to normal rendering or the
  heartbeat/permission/key protocols.
- Robust framing that coexists with live JSON heartbeats on the same line stream.

**Non-Goals:**
- Not full-resolution/lossless capture or video; a downsampled still is enough.
- Not a generic file-transfer channel; just the screenshot frame.
- No on-device image compression (ESP side stays simple: raw RGB565 + base64).
- Not exposed to end users as a product feature; it is a dev/agent tool.

## Decisions

### D1 — Capture source: the existing full-frame sprite, downsampled in firmware

Read `spr` directly (it already holds the composited frame) and **downsample by
2** to 640×360 by sampling every other pixel/row. No re-render, no extra
full-size buffer. RGB565 is kept on the wire; the host expands to RGB888.

- *Alternative*: send full 1280×720 — rejected (4× the bytes for no real benefit
  to judging the UI). Downsample-by-2 is a one-line stride in the encoder.
- *Alternative*: JPEG on device — rejected (no easy encoder in this stack).

### D2 — Wire framing: text frame that coexists with JSON lines

Firmware emits, on `{"cmd":"shot"}`:

```
SHOT 640 360 460800
<base64 chunk line>            // ~4 KB per line
... repeated ...
ENDSHOT
```

- `SHOT <w> <h> <rawByteLen>` header; then base64 of the raw RGB565 buffer split
  into fixed-size lines; then `ENDSHOT`.
- These are plain text lines, so the daemon's existing line reader still works:
  a normal heartbeat `{...}` is untouched; a line starting with `SHOT ` switches
  the reader into capture mode until `ENDSHOT`.
- *Rationale*: avoids a separate binary sub-protocol in the async serial reader;
  base64 keeps everything newline-delimited.

### D3 — Daemon capture + stdlib PNG

In `SerialPortWriter._rx_loop`, before the `startswith("{")` dispatch:

- On `SHOT w h len` → enter capture mode (stash w/h/len, reset a base64 buffer).
- While capturing, append non-`ENDSHOT` lines to the base64 buffer (do **not**
  forward to `on_tx_line`).
- On `ENDSHOT` → base64-decode, convert RGB565→RGB888, write a PNG with a small
  stdlib writer (`zlib.compress` + manual IHDR/IDAT/IEND chunks + CRC32), to
  `TAB5_SHOT_PATH` (default `/tmp/tab5-shot.png`). Set an `asyncio.Event` /
  store the path so a waiting socket request can return it.

- *Alternative*: write raw to a file and convert in the CLI — rejected; keep the
  artifact a ready-to-read PNG, and the CLI dependency-free.

### D4 — Trigger: daemon socket action + CLI

- `handle_client` gains `action: "screenshot"`: write `{"cmd":"shot"}` to the
  device, await the capture-complete event (timeout ~8 s), reply
  `{"ok":true,"path":"/tmp/tab5-shot.png"}` (or `{"ok":false,"error":...}`).
- `tools/tab5-shot/shot.py`: connect to `CURSOR_BRIDGE_SOCKET` (or
  `CC_BRIDGE_SOCKET`), send the action, print the path on success. An agent then
  reads the PNG; a human can `open` it.
- *Rationale*: the daemon already owns the port and runs a unix-socket server;
  reusing it avoids fighting for the serial port.

### D5 — Where it lands

The daemon code goes in `buddy_core/core.py` (shared), so whichever daemon owns
the Tab5 serial can serve screenshots; mirror across the checkout the running
daemon uses (today `feat/cursor-next`). Firmware lands on `feat/sticks3-buddy`.

## Risks / Trade-offs

- [Large frame stalls the serial stream / starves heartbeats during capture] →
  Downsample to 640×360 and chunk; capture is on-demand and brief. If needed,
  drop to 320×180. The heartbeat loop tolerates a short gap.
- [base64 lines interleave with a heartbeat mid-capture] → The firmware emits the
  whole `SHOT…ENDSHOT` frame in one tight write before returning to the loop, so
  no heartbeat is interleaved; the daemon capture mode also ignores stray `{`
  lines until `ENDSHOT` (logs and continues if the frame is malformed/timed out).
- [stdlib PNG writer bugs] → Keep it minimal and unit-test it on a tiny known
  bitmap (CRC/ível round-trip) so the agent always gets a valid PNG.
- [Capture never completes (device busy / unplugged)] → Socket action has a
  timeout and returns `{"ok":false}`; partial buffers are discarded.
- [Color fidelity: RGB565→RGB888 expansion] → Use the standard 5/6/5→8/8/8 bit
  replication; good enough to judge the UI.

## Migration Plan

1. Firmware: add `cmd:shot` + the sprite encoder; flash.
2. Daemon: add the capture mode + PNG writer + socket action; restart the
   Tab5-owning daemon.
3. Add the CLI; document `cmd:shot` in `REFERENCE.md`.
4. Mirror the daemon piece to the other checkout.
5. Rollback: revert the `cmd:shot` branch + capture mode; nothing else depends
   on it.

## Open Questions

- Default resolution: ship ½ (640×360) or make it a `cmd:shot` argument
  (`{"cmd":"shot","scale":4}`)? (Default: ½, add `scale` if too slow.)
- Output path: fixed `/tmp/tab5-shot.png` (overwritten each shot) or timestamped
  under an artifacts dir? (Default: fixed `/tmp/tab5-shot.png` for easy reading;
  the CLI can `--out` elsewhere.)
