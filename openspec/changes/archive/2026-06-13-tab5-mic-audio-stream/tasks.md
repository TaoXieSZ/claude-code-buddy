## 1. Firmware: capture + stream while held

- [x] 1.1 Confirmed `M5.Mic.record` double-buffer semantics from `Mic_Class.hpp` (queue depth 2; ping-pong for gapless)
- [x] 1.2 `bool uiMicHeld()` accessor in `ui.cpp`/`ui.h`
- [x] 1.3 `feedSendAudio(pcm, n)` in `feed.cpp` → `A<base64>` (mbedtls base64)
- [x] 1.4 `main.cpp` loop: while held, ping-pong two 320-sample 16 kHz buffers, stream the completed one (+ drive VU)
- [x] 1.5 Built clean; flashed

## 2. Daemon: PCM → BlackHole

- [x] 2.1 `_BlackHoleSink` resolves the device by name (`TAB5_MIC_SINK`, default "BlackHole") via sounddevice
- [x] 2.2 `SerialPortWriter`: `A` frame → decode + `sink.feed()`; idle >0.6s → `sink.stop()`
- [x] 2.3 Robust: device missing / open failure → warn, no crash; buffer capped ~2 s to bound latency
- [x] 2.4 Audio frames kept out of the JSON/screenshot paths (distinct `A` prefix)
- [NOTE] ffmpeg `audiotoolbox` device selection was unreliable on this host → used sounddevice/PortAudio (added `portaudio` + `sounddevice`); design.md D3' updated

## 3. Cross-checkout + docs

- [x] 3.1 Mirrored `_BlackHoleSink` + RX wiring to both `buddy_core` copies (sticks3 + feat/cursor-next)
- [x] 3.2 Documented the `A` audio frame in `REFERENCE.md`

## 4. Build, flash, verify

- [x] 4.1 `pio run -e m5stack-tab5` builds clean; both daemons compile; pytest 18 passed
- [x] 4.2 Flashed firmware; restarted the Tab5-owning daemon (reconnected)
- [x] 4.3 Daemon→BlackHole verified by loopback self-test (fed a 440 Hz tone → BlackHole input RMS ≈ 7347 = AUDIO OK)
- [x] 4.4 User-verified: 豆包 input = BlackHole 2ch; hold PTT, speak → transcription appears (出字了). Gain 5× persisted in plist.
