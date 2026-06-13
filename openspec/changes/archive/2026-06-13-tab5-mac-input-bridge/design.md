## Context

The Tab5 talks to the Mac over USB-CDC serial. The daemon holding that port
(currently `cursor-bridge` on `feat/cursor-next`, via `CURSOR_BRIDGE_TAB5_SERIAL`)
runs `buddy_core.make_on_stick_line`, which already dispatches inbound
`{"cmd":...}` lines:

- `cmd:permission` → resolves a pending future (Tab5 → daemon verdict).
- `cmd:mic` `{state:down|up}` → `_send_key(ptt_keycode, ...)` per `PTT_MODE`
  (tap / hold / double_tap) — exactly the stick's PTT dictation relay.
- `cmd:telemetry` → dashboard health.

So the **PTT half is a firmware-only feature**: the Tab5 just needs to emit the
same `cmd:mic` down/up that the stick emits. The **keyboard half needs one new
daemon handler** (`cmd:key`) plus firmware to emit it and a way to choose when
keys drive the UI vs the Mac.

Current Tab5 input (`kbd.cpp`): the keyboard accessory (I2C @0x6D) is put in HID
mode and yields `[modifier, usage_code]`; the USB-A HID host yields boot-keyboard
reports. Both currently call `uiKeyEvent()` to drive the dashboard (←/→ switch
session, ↑/↓ scroll, Enter/y allow, Esc/n deny). `feed.cpp` already owns the
serial TX side (it writes `cmd:permission` verdicts back).

Constraints: Accessibility permission is already granted to the daemon's venv
Python (PTT uses it). The serial link is line-delimited JSON. macOS key
synthesis is via Quartz CGEvent.

## Goals / Non-Goals

**Goals:**
- One-touch hold-to-talk on the Tab5 that drives the Mac dictation/输入法 hotkey,
  behaviorally identical to the stick (same `cmd:mic`, same `PTT_MODE`).
- Tab5 keyboard (accessory + USB-A) usable as a second Mac keyboard: printable
  characters, Return/Backspace/Tab/Esc/arrows, and modifiers (⌘/⌥/⌃/⇧).
- A clear, low-surprise way to switch the keyboard between driving the dashboard
  and typing into the Mac, with on-screen state.
- Reuse the existing relay infra; minimal new wire surface (`cmd:key`).

**Non-Goals:**
- No on-device audio capture/streaming/STT (explicitly: the Mac's dictation app
  hears the room; the Tab5 only triggers the hotkey).
- Not a full HID device over USB (the port is CDC for the feed; we relay via the
  daemon, not by re-enumerating as a USB keyboard).
- No key-repeat/auto-repeat emulation in v1 (single press → single type).
- No remapping/macros; 1:1 key relay.

## Decisions

### D1 — PTT trigger: on-screen hold-to-talk button (+ optional reserved key)

- Add a mic button to the dashboard. **Press → `{"cmd":"mic","state":"down"}`,
  release → `{"cmd":"mic","state":"up"}`** over serial. A top-of-screen REC
  indicator blinks while held (mirrors the stick's red REC banner).
- *Alternative*: a reserved physical key as PTT. Kept as a secondary trigger but
  the on-screen button is primary (always available, no mode confusion).
- Zero daemon change — the daemon already maps `cmd:mic` to the dictation hotkey
  via `PTT_MODE`. The user picks tap/hold/double_tap exactly as for the sticks.

### D2 — No mode: keyboard is always a Mac keyboard; dashboard is touch-driven

A single key cannot both scroll the dashboard and type a character, so rather
than an explicit mode toggle (which the user rejected as clunky), we split by
**input modality**: the **keyboard always relays to the Mac**, and the
**dashboard is driven entirely by touch** — which it already fully supports:
drag-to-scroll, tap a session card to switch, tap ✓/✗ to answer permission.
Both are usable at the same time (type on the keyboard while touching the
screen), with no mode, indicator, or toggle key.

- *Consequence*: keyboard-driven dashboard control (arrows scroll, Enter/y
  answer permission) is retired — touch covers every one of those. The
  `uiKeyEvent` UI handler stays in the source but is no longer fed by `kbd.cpp`.
- *Alternatives considered*: (a) explicit DASHBOARD/MAC toggle — rejected by the
  user (wants simultaneous use, no switching). (b) Reserve arrows/Enter for the
  UI and relay the rest — rejected: then you couldn't press Enter/arrows in a
  Mac text field, which defeats "second keyboard".

### D3 — `cmd:key` wire format (firmware → daemon)

Line-delimited JSON on the existing channel:

```json
{"cmd":"key","ch":"a"}                       // printable: type the Unicode char
{"cmd":"key","key":"enter"}                  // named special key
{"cmd":"key","key":"left","mods":["cmd"]}    // special/char + modifiers
```

- Printable keys: firmware puts the accessory in **Character mode** (MODE reg
  0x10 = 2) to get the ASCII/char directly, or maps HID usage→char; sends `ch`.
- Special keys (Return/Backspace/Tab/Esc/arrows/Delete) and any combo with
  modifiers: send a named `key` (+ optional `mods`).
- *Rationale*: characters via Unicode are layout-proof (no kVK guessing across
  QWERTY/locale); special keys/modifiers need real keycodes, so name them and
  let the daemon map to kVK.

### D4 — Daemon `cmd:key` handler (Quartz)

Extend `make_on_stick_line` (or the rx dispatch) with a `cmd == "key"` branch:

- `ch` present → `CGEventKeyboardSetUnicodeString` on a synthesized key event
  (down+up) so the exact character is typed regardless of layout.
- `key` present → map name→kVK (a small table: enter=0x24, delete/backspace=0x33,
  tab=0x30, esc=0x35, left/right/up/down=0x7B/7C/7E/7D, space=0x31, …) and emit
  keyDown/keyUp; apply `mods` as `CGEventFlags` (reuse `_MOD_FLAGS` semantics).
- Lands in `buddy_core/core.py` so **both** cc-bridge and cursor-bridge get it;
  the relay runs in whichever daemon owns the Tab5 serial.

### D5 — Firmware input router

Introduce a tiny router in `kbd.cpp`/`ui.cpp`: on each decoded key, if mode ==
DASHBOARD call `uiKeyEvent`; if mode == MAC translate to `cmd:key` and hand to
`feed.cpp` for serial TX. The toggle key is intercepted in both modes. Keep the
existing single-producer key queue; only the consumer branches on mode.

### D6 — Cross-checkout landing

Firmware is on `feat/sticks3-buddy`. The Tab5-owning daemon is `cursor-bridge`
on `feat/cursor-next`; `cc-bridge` (sticks3) is the other consumer. The
`cmd:key` handler must land in the `buddy_core` that the owning daemon runs, and
be mirrored across branches (same pattern as the serial-feed / `app`-tag ports).
Document `cmd:key` in `REFERENCE.md` next to `cmd:mic`/`cmd:permission`.

## Risks / Trade-offs

- [Mode confusion: user types into the Mac thinking they're scrolling, or vice
  versa] → Always show a prominent mode indicator; default to DASHBOARD; make the
  toggle deliberate and visible; consider auto-reverting to DASHBOARD after N s
  idle in MAC mode (open question).
- [Accessibility permission not granted → keys silently dropped] → Reuse the
  existing PTT grant; log a clear warning if Quartz typing fails (same path
  `_send_key` already guards).
- [Layout/locale mismatch for special keys] → Characters go via Unicode
  (layout-proof); only named special keys use kVK, which are layout-independent.
- [Key-repeat / fast typing overflow the 8-slot key queue] → v1 drops on full
  (existing behavior); acceptable for a secondary keyboard. Revisit if needed.
- [Two daemons could both try to own the Tab5 serial] → Only one holds the port;
  the relay runs there. Unchanged from today's single-owner serial model.
- [PTT vs the Tab5's own mic VU] → The on-device mic is only read for the avatar
  VU; PTT does not capture audio, so there is no I2S contention with `cmd:mic`.

## Migration Plan

1. Firmware-first: add the mic button + `cmd:mic` TX (PTT works immediately,
   daemon already handles it). Flash and verify dictation triggers.
2. Add the daemon `cmd:key` handler (buddy_core), restart the Tab5-owning daemon.
3. Add the firmware keyboard mode toggle + `cmd:key` TX; flash; verify typing.
4. Mirror the `cmd:key` handler to the other checkout's buddy_core; update
   `REFERENCE.md`.
5. Rollback: revert firmware (keys fall back to DASHBOARD-only) and/or the
   `cmd:key` branch; PTT and the rest are unaffected.

## Open Questions

- Toggle binding: dedicated on-screen button only, or also a reserved key/chord?
  Which key is least likely to be wanted as a typed key? (Default: on-screen
  button + a reserved Fn/function key.)
- Auto-revert MAC→DASHBOARD after idle, or stay until toggled? (Default: stay.)
- Accessory **Character mode** (ASCII direct) vs keep **HID mode** and map
  usage→char in firmware? Character mode is simpler for printables but HID mode
  carries modifiers/special keys; may need to read both registers. (Default:
  HID mode + a firmware usage→char table, so modifiers/special keys are uniform.)
- Should PTT also be bindable to a reserved keyboard key, or on-screen only?
  (Default: on-screen primary; revisit.)
