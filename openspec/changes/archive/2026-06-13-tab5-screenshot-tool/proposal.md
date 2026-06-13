## Why

Iterating on the Tab5 UI currently requires the user to photograph the screen
and send the picture every time. That is slow and breaks the feedback loop. The
Tab5 already renders its whole frame into a PSRAM sprite and is wired to the Mac
over USB-CDC, so the device can simply hand its framebuffer back to the host on
demand — letting an agent (or any tool) *see* the screen as a PNG without a
camera.

## What Changes

- **Firmware screenshot command.** On a new `{"cmd":"shot"}` control line, the
  Tab5 reads its full-frame sprite (`M5Canvas`, RGB565), downsamples it, and
  streams it back over the serial link in a simple framed text protocol
  (`SHOT <w> <h>` header → base64 chunks → `ENDSHOT`).
- **Daemon capture-to-PNG.** The daemon that owns the Tab5 serial port detects
  the `SHOT`…`ENDSHOT` frame in its serial RX, decodes the pixels, and writes a
  PNG to a known path (e.g. `/tmp/tab5-shot.png`) using only the Python stdlib
  (zlib) — no new dependency.
- **Trigger CLI.** A small tool (`tools/tab5-shot/`) asks the daemon (over its
  existing unix socket) to take a screenshot and prints the resulting PNG path,
  so a human or an agent can grab the current screen with one command.
- This is **additive and dev-facing**: no change to the heartbeat schema,
  permission round-trip, `cmd:mic`/`cmd:key`, or normal rendering. The `cmd:shot`
  request and the `SHOT` response frame are new control-channel surface.

## Capabilities

### New Capabilities
- `tab5-screenshot`: capture the Tab5's current screen to a PNG on the host —
  the firmware `cmd:shot` → framebuffer stream, the daemon-side frame capture +
  PNG encode, and the trigger CLI. Covers the wire framing, the on-host output
  path, and failure behavior.

### Modified Capabilities
<!-- None. Additive control command + a new dev tool; no existing spec's
     requirements change. -->

## Impact

- **Firmware (`src/tab5/`)**: `feed.cpp` (parse `cmd:shot`), `ui.cpp`/`ui.h`
  (expose a screenshot encoder that reads the sprite buffer, downsamples, and
  emits the framed base64 over serial). No change to normal rendering.
- **Daemon (`tools/buddy_core/core.py`)**: `SerialPortWriter` RX gains a capture
  mode for the `SHOT`…`ENDSHOT` frame → stdlib PNG writer → file; the socket
  server gains an `action:"screenshot"` that triggers `cmd:shot` and returns the
  path. Mirrored to the checkout whose daemon owns the Tab5 serial.
- **New tool (`tools/tab5-shot/`)**: a thin CLI that hits the daemon socket and
  prints the PNG path.
- **Docs**: `REFERENCE.md` gains `cmd:shot` + the `SHOT` frame next to the other
  control commands.
- Memory/bandwidth: a downsampled RGB565 frame (e.g. 640×360 = ~460KB raw,
  ~613KB base64) streamed in chunks; transient, no steady-state cost.
