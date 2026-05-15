# daemon-event-mapping

## ADDED Requirements

### Requirement: Camera frame ingest server

`buddy_core/core.py` MUST run an asyncio TCP server, started from `run()`, that
accepts a single StackChan camera frame stream. Frames arrive as a 4-byte
little-endian length header followed by a JPEG payload. The server MUST tolerate
the StackChan connecting and disconnecting as permission-prompt windows open and
close.

#### Scenario: StackChan connects and streams
- GIVEN the daemon `run()` loop is active
- WHEN the StackChan opens the frame socket and sends a length-prefixed JPEG
- THEN the server decodes one complete frame and hands it to the gesture
  classifier

#### Scenario: StackChan disconnects
- GIVEN an open frame stream
- WHEN the StackChan closes the socket (its prompt cleared)
- THEN the server releases the connection and waits for the next one without
  error

### Requirement: Gesture classification with a hold window

The daemon MUST classify decoded frames into `thumbs-up` / `thumbs-down` / `none`
via MediaPipe Hands, and MUST require the same non-`none` gesture across a
debounce/hold window before it counts as confirmed. MediaPipe MUST be an optional
import — if unavailable, frames are dropped and gesture-approve degrades to
manual approval without crashing.

#### Scenario: Sustained gesture confirmed
- GIVEN a pending permission prompt and a frame stream
- WHEN the same `thumbs-up` is detected across the full hold window
- THEN the gesture is confirmed as `approve`

#### Scenario: Flickering gesture not confirmed
- GIVEN a frame stream
- WHEN a `thumbs-up` appears for fewer frames than the hold window
- THEN no decision is confirmed

#### Scenario: MediaPipe unavailable
- GIVEN a daemon where the MediaPipe import failed
- WHEN frames arrive
- THEN they are logged and dropped; no crash; the permission prompt remains
  resolvable manually

### Requirement: Confirmed gesture resolves the pending permission

When a gesture is confirmed while `state.prompt` is set, the daemon MUST send
`{"cmd":"gesture","result":"approve"|"deny"}` back to the firmware for UI
feedback, and MUST route the decision into the same Claude Code permission
resolution path that a manual approval uses. A confirmed gesture for a tool in
`SAFE_TOOLS` is a no-op (those never block).

#### Scenario: Confirmed approve resolves the prompt
- GIVEN a `BuddyState` with `state.prompt` set for a non-`SAFE_TOOLS` tool
- WHEN a gesture is confirmed as `approve`
- THEN the daemon sends `{"cmd":"gesture","result":"approve"}` to the firmware
  and the pending Claude Code permission is approved

#### Scenario: Confirmed deny resolves the prompt
- GIVEN a `BuddyState` with `state.prompt` set
- WHEN a gesture is confirmed as `deny`
- THEN the daemon sends `{"cmd":"gesture","result":"deny"}` to the firmware and
  the pending Claude Code permission is denied

#### Scenario: Gesture confirmed with no pending prompt
- GIVEN a `BuddyState` with `state.prompt` unset
- WHEN a gesture is confirmed
- THEN no permission decision is made and no gesture command is sent
