## 1. PTT trigger (firmware only — daemon already handles cmd:mic)

- [x] 1.1 Added `feedSendMic(bool)` serial-TX helper in `feed.cpp` (declared in `ui.h`)
- [x] 1.2 Added an on-screen hold-to-talk mic button in the sidebar with a press/release hitbox (`g_hitMic`)
- [x] 1.3 Press → `{"cmd":"mic","state":"down"}`, release → `{"cmd":"mic","state":"up"}`
- [x] 1.4 Blinking REC bar (top of screen, both bands) + button turns red while held; cleared on release
- [x] 1.5 Built + flashed; cursor-bridge reconnected (relays `cmd:mic` → Mac hotkey per `PTT_MODE`) — on-device dictation confirm pending user

## 2. Daemon cmd:key handler

- [x] 2.1 Added `_KVK_SPECIAL` + `_KVK_CHAR` (letters/digits) tables and `kvk_for()` in `buddy_core/core.py`
- [x] 2.2 Added `cmd == "key"` branch: `ch` → `_type_unicode` (CGEventKeyboardSetUnicodeString); `key` → `_type_keycode` (kVK keyDown/keyUp + `_MOD_MASK` flags)
- [x] 2.3 Quartz import guarded; warns and drops on failure (no crash)
- [x] 2.4 Unit tests for `kvk_for` (specials, letters/digits, case-insensitive, unknown) + `_MOD_MASK` — 16 pass

## 3. Keyboard relay (firmware) — no mode (keyboard always → Mac, dashboard via touch)

- [x] 3.1 Decision changed (user): no DASHBOARD/MAC toggle; keyboard always relays, dashboard is touch-driven (drag-scroll / tap card / tap ✓✗)
- [x] 3.2 ~~mode toggle~~ removed — not needed
- [x] 3.3 `kbd.cpp` routes both the I2C accessory and USB-A HID keys to `feedSendKey` (was `uiKeyEvent`)
- [x] 3.4 `feedSendKey` in `feed.cpp` maps HID usage→char (base/shifted), special keys, and the modifier byte → `mods`; printables via `ch`, shortcuts/specials via `key`+`mods`

## 4. Cross-checkout + docs

- [x] 4.1 Mirrored the `cmd:key` handler into both `buddy_core` copies (sticks3 + feat/cursor-next)
- [x] 4.2 Documented `cmd:key` in `REFERENCE.md` next to `cmd:mic` / `cmd:permission`

## 5. Build, flash, verify

- [x] 5.1 `pio run -e m5stack-tab5` builds clean; `pytest` 16 passed; cursor-next daemon compiles
- [x] 5.2 Flashed (free port → upload → restart cursor-bridge); reconnected 12:09:31
- [x] 5.3 On-device: PTT dictation verified end-to-end (hold → 豆包 triggered + Tab5 audio → transcription). Keyboard relay (`cmd:key`) implemented + unit-tested; no-mode design (keyboard always relays, dashboard via touch) shipped.
- [x] 5.4 Iterated with user → PTT/dictation approved (出字了)
