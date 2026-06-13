## Why

The Tab5 sits on the desk wired to the Mac over USB-CDC and already relays
permission verdicts. It should also serve as a Mac **input device**, the way
the Plus2/StackChan sticks already do for push-to-talk dictation: a one-touch
voice-input trigger (drive the Mac's dictation / 输入法 hotkey) and — since the
Tab5 has a real keyboard accessory + USB-A HID host — a **second physical
keyboard** that types into the Mac. This turns the buddy from a passive display
into an input surface, reusing the daemon's existing Quartz key-relay over the
link we already have.

## What Changes

- **Tab5 PTT voice trigger (like the stick).** Add an on-device hold-to-talk
  affordance (on-screen mic button and/or a reserved keyboard key). On press it
  sends `{"cmd":"mic","state":"down"}` and on release `{"cmd":"mic","state":"up"}`
  over the serial link — the **same wire command the sticks send**, which the
  daemon already relays to a Mac dictation hotkey via `_send_key`
  (`*_BRIDGE_PTT_MODE` tap/hold/double_tap unchanged). No on-device audio
  capture; the Mac's dictation app/输入法 hears the room.
- **Tab5 keyboard → Mac second keyboard.** Relay keystrokes from the Tab5
  keyboard accessory (I2C @0x6D) and USB-A HID host to the Mac. Firmware emits a
  new `{"cmd":"key",...}` line; the daemon types it into the Mac via Quartz
  (Unicode characters for printable keys, keycode+modifiers for special keys).
- **Keyboard mode toggle.** Because the Tab5 keyboard currently drives the
  dashboard (arrows scroll, Enter/Esc answer permission), introduce an explicit
  mode: **DASHBOARD** (keys control the UI, today's behavior) vs **MAC**
  (keys relay to the Mac). A reserved toggle (on-screen button + a reserved key)
  switches modes; the active mode is shown on screen.
- **Daemon `cmd:key` handler.** Extend `buddy_core` to type relayed keys into
  the Mac. PTT (`cmd:mic`) needs **no** daemon change — it already works.

## Capabilities

### New Capabilities
- `tab5-ptt-dictation`: on-device push-to-talk trigger that emits the existing
  `cmd:mic` down/up over the link to drive the Mac dictation hotkey — Tab5 as a
  voice-input front-end, mirroring the stick. Covers the trigger gesture/button,
  the wire command, and the on-screen recording indicator.
- `tab5-keyboard-relay`: relaying Tab5 keyboard input to the Mac as a second
  keyboard, including the DASHBOARD/MAC mode toggle, the `cmd:key` wire command,
  and the daemon-side Quartz typing (characters + special keys + modifiers).

### Modified Capabilities
<!-- None at the spec level. The daemon-event-mapping spec covers hook→heartbeat
     mapping, not the stick→daemon control channel; the cmd:mic relay already
     exists. cmd:key is additive. -->

## Impact

- **Firmware (`src/tab5/`)**: `ui.cpp` (mic button + REC indicator, keyboard
  mode toggle + indicator, route keys to UI vs serial), `kbd.cpp` (emit
  `cmd:key` in MAC mode; Character vs HID mode on the accessory), `feed.cpp`
  (serial TX of `cmd:mic` / `cmd:key`), possibly a small input-router module.
- **Daemon (`tools/buddy_core/core.py`)**: new `cmd:key` handler that types via
  Quartz (`CGEventKeyboardSetUnicodeString` for characters; keycode+flags for
  special keys, reusing `_send_key`/`_MOD_FLAGS`). Must land in the buddy_core
  that the Tab5-owning daemon runs (currently `feat/cursor-next`'s
  cursor-bridge) and mirror to `sticks3` cc-bridge.
- **No change** to the heartbeat schema, permission round-trip, `app` routing,
  or `REFERENCE.md` data plane (this is the stick→daemon control channel, which
  already carries `cmd:*`). The new `cmd:key` should be documented in
  `REFERENCE.md` alongside `cmd:mic`/`cmd:permission`.
- **macOS Accessibility permission** already required for the daemon's Python
  (used by `_send_key`); keyboard relay uses the same grant.
