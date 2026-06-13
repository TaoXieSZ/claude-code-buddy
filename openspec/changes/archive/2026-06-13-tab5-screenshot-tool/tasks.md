## 1. Firmware: screenshot encoder

- [x] 1.1 `uiScreenshot()` in `ui.cpp`/`ui.h`: reads the sprite buffer, downsamples ×2 (640×360), byte-swaps to canonical little-endian RGB565, emits `SHOT <w> <h> <rawLen>` + base64 chunks + `ENDSHOT` via mbedtls base64
- [x] 1.2 `feed.cpp` parses `{"cmd":"shot"}` → `uiScreenshot()`
- [x] 1.3 Frame emitted contiguously (single function, no heartbeat interleave)

## 2. Daemon: capture + stdlib PNG

- [x] 2.1 Stdlib PNG writer (`_write_png`, zlib + IHDR/IDAT/IEND + CRC32) and `_rgb565_to_rgb888`
- [x] 2.2 `SerialPortWriter._rx_loop` capture mode: `SHOT` header → collect base64 → `ENDSHOT` → decode + write PNG to `TAB5_SHOT_PATH` (default `/tmp/tab5-shot.png`); `screenshot()` coroutine + event
- [x] 2.3 Runaway/short/malformed frames dropped with a warning (no crash)
- [x] 2.4 Unit tests: `_rgb565_to_rgb888` pure colors + `_write_png` produces a valid PNG (signature + IHDR dims + IDAT row size) — 18 pass

## 3. Trigger: socket action + CLI

- [x] 3.1 `handle_client` `action:"screenshot"` → `ble.screenshot()` → `{ok,path}` (8s default timeout); `CompositeWriter.screenshot` delegates to the serial peer
- [x] 3.2 `tools/tab5-shot/shot.py` — hits the daemon socket, prints the PNG path (`--socket`/`--timeout`)

## 4. Docs + cross-checkout

- [x] 4.1 Documented `cmd:shot` + the `SHOT`…`ENDSHOT` frame in `REFERENCE.md`
- [x] 4.2 Mirrored the daemon capture/socket changes to feat/cursor-next (the Tab5-owning daemon) + copied the CLI

## 5. Build, flash, verify

- [x] 5.1 `pio run -e m5stack-tab5` builds clean; `pytest` 18 passed; both daemons compile
- [x] 5.2 Flashed firmware; restarted cursor-bridge (capture handler loaded)
- [x] 5.3 `tools/tab5-shot/shot.py` produces a valid 640×360 PNG of the live screen (fixed RGB565 byte-swap so colors are correct)
- [x] 5.4 Agent can `Read` the PNG directly — no more photos
