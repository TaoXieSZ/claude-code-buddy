# tab5-screenshot Specification

## Purpose
TBD - created by archiving change tab5-screenshot-tool. Update Purpose after archive.
## Requirements
### Requirement: Firmware screenshot command

On receiving `{"cmd":"shot"}` over the serial link, the Tab5 SHALL serialize its
current full-frame sprite and stream it back as a framed text response, then
resume normal operation. Normal rendering and the heartbeat/permission/key
protocols SHALL be unaffected.

#### Scenario: Shot request produces a framed response
- **WHEN** the device receives `{"cmd":"shot"}`
- **THEN** it emits a `SHOT <w> <h> <rawLen>` header, one or more base64 chunk
  lines of the (downsampled) RGB565 framebuffer, and a final `ENDSHOT` line

#### Scenario: One contiguous frame
- **WHEN** the device streams a screenshot
- **THEN** the `SHOT`…`ENDSHOT` lines are emitted contiguously without a
  heartbeat interleaved, so the host can reassemble one frame

### Requirement: Daemon captures the frame to a PNG

The daemon that owns the Tab5 serial port SHALL detect the `SHOT`…`ENDSHOT`
frame in its serial RX, decode the pixels, and write a PNG file to a known path
using only the Python standard library (no new dependency). Frame lines SHALL
NOT be dispatched as normal JSON events.

#### Scenario: Valid frame becomes a PNG
- **WHEN** the daemon receives a complete `SHOT`…`ENDSHOT` frame
- **THEN** it writes a valid PNG at the configured path (default
  `/tmp/tab5-shot.png`) with the frame's width and height

#### Scenario: Frame lines are not treated as heartbeats
- **WHEN** the daemon is capturing a screenshot frame
- **THEN** the base64 lines are consumed by the capture, not parsed as hook
  events, and live JSON heartbeats outside the frame still work

#### Scenario: Malformed or incomplete frame is dropped
- **WHEN** a screenshot frame is malformed or never reaches `ENDSHOT`
- **THEN** the daemon discards the partial buffer, logs a warning, and keeps
  running (no crash)

### Requirement: Screenshot trigger

There SHALL be a way to trigger a screenshot through the daemon's existing
socket and obtain the resulting PNG path, so a human or an agent can capture the
current screen with one command.

#### Scenario: Socket action returns the path
- **WHEN** a client sends `{"action":"screenshot"}` to the daemon socket
- **THEN** the daemon requests a shot from the device and replies with the PNG
  path on success

#### Scenario: Trigger times out gracefully
- **WHEN** the device does not return a frame within the timeout
- **THEN** the daemon replies with a failure result and does not hang

