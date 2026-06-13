## Context

PTT works end-to-end as a hotkey relay (verified: daemon logs `mic down → key
54`, Accessibility granted). The missing piece is audio: the dictation app
(豆包) listens to the BlackHole virtual device, but nothing feeds it. The Tab5
has an ES7210 mic already initialized (`M5.Mic`, used for the avatar VU via
`M5.Mic.record(buf, 256, 16000)`); on Tab5 the mic and ES8388 speaker share
I2S_NUM_0 (arbitrated in `sound.cpp`), but during PTT no clip plays, so the mic
owns the bus. The only host link is USB-CDC serial, owned by the Tab5 daemon
(cursor-bridge on feat/cursor-next). `ffmpeg` is installed; BlackHole 2ch
exists. The daemon already frames non-JSON serial lines (the `SHOT` screenshot
frame shows the pattern).

## Goals / Non-Goals

**Goals:**
- Hold PTT → the Tab5 mic's audio plays into BlackHole in near-real-time, so the
  dictation app transcribes it; release → stops.
- Reuse the existing I2S mic and serial link; no new Python/system dependency
  (pipe to the already-installed `ffmpeg`).
- Coexist cleanly with heartbeats, `cmd:key`, and `cmd:shot` on the same line
  stream.

**Non-Goals:**
- No high-fidelity/stereo audio; 16 kHz mono is the dictation norm.
- No on-device compression (raw PCM + base64; bandwidth is fine while held).
- No echo cancellation (speaker is idle during PTT; not needed).
- Not always-on streaming; audio flows only while PTT is held.
- No RTC/Agora/voice-agent integration — just mic → BlackHole.

## Decisions

### D1 — Capture: 16 kHz mono S16 from `M5.Mic`, only while held

`ui.cpp` exposes `uiMicHeld()`. `main.cpp`'s loop, while held, records a small
chunk (e.g. 320 samples = 20 ms) via `M5.Mic.record(buf, N, 16000)` and streams
it. **Verify the exact `M5.Mic.record` polling semantics against the M5Unified
mic example** (record is non-blocking/DMA; read the buffer once `isRecording()`
clears) before finalizing the loop — do not guess the API.

### D2 — Wire frame: `A<base64>` lines, gated by mic down/up

While held, firmware emits `A<base64 of raw S16LE chunk>` newline-terminated
lines. These coexist with JSON heartbeats (`{`), the `SHOT` frame, etc. The
daemon treats `A`-prefixed lines as audio only between `cmd:mic down` and `up`
(the firmware also sends those). 20 ms chunks → ~50 lines/s, ~43 KB/s base64.

### D3 — Daemon playback: pipe PCM → `ffmpeg` → BlackHole

On `mic down`, the daemon spawns:
`ffmpeg -f s16le -ar 16000 -ac 1 -i - -f audiotoolbox -audio_device_index <idx>`
where `<idx>` is BlackHole 2ch's audiotoolbox output index (enumerated once via
`ffmpeg -sinks audiotoolbox`, matched by name, cached; configurable via
`TAB5_MIC_SINK`). Decoded PCM from each `A` frame is written to ffmpeg stdin.
On `mic up` (+ short drain), stdin is closed and the process reaped.

- **UPDATE (during impl):** `ffmpeg`'s `audiotoolbox` output device selection
  is unreliable on this host — `-sinks audiotoolbox` says "not implemented" and
  `-audio_device_index` fails to bind BlackHole (`AudioObjectGetPropertyData
  UID` error). So the "no new dependency via ffmpeg" path does not work
  reliably. Reliable alternative below.
- **Revised D3':** play via Python `sounddevice` (PortAudio), which selects
  "BlackHole 2ch" by name and streams PCM from a ring buffer. Cost: a one-time
  `brew install portaudio` + `pip install sounddevice` in the daemon venv.
  This is the dependency the original goal hoped to avoid, but ffmpeg can't do
  it dependably. Pending user OK to add the dependency.
- *Alternative*: keep ffmpeg always running — rejected; spawn per-utterance so a
  stale pipe can't hold the device.

### D4 — Lifecycle & robustness

`SerialPortWriter` tracks an audio state: `mic down` → open ffmpeg; `A` frame →
decode+write (drop if no pipe); `mic up` → close after a brief flush. Guard
against: ffmpeg missing (log once, no crash), write errors (reap + reopen next
hold), and the screenshot/heartbeat paths (audio frames are a separate prefix).

## Risks / Trade-offs

- [M5.Mic.record semantics misused → garbled/dropped audio] → Mirror the
  upstream M5Unified mic example verbatim for the record/poll loop; test on
  device.
- [Capture loop starves the UI while held] → 20 ms chunks; during PTT the UI
  only shows the REC indicator, so a slightly slower repaint is acceptable.
- [Serial saturation (audio + heartbeat + a screenshot at once)] → screenshots
  are manual/rare; audio is ~43 KB/s, well within USB-CDC; heartbeats are tiny.
- [ffmpeg device index drifts when devices change] → enumerate by name each
  `mic down` (cheap) or cache + `TAB5_MIC_SINK` override.
- [Latency/jitter into BlackHole] → small; dictation captures the whole
  utterance, so minor latency is fine. No jitter buffer in v1; add if choppy.
- [I2S contention if a sound cue plays mid-PTT] → unlikely during dictation;
  `sound.cpp` already arbitrates, mic resumes after playback.

## Migration Plan

1. Firmware: add `uiMicHeld()` + the capture/stream loop; flash; confirm `A`
   frames arrive in the daemon log while held.
2. Daemon: add the ffmpeg→BlackHole audio path; restart the Tab5-owning daemon.
3. Set 豆包 input device = BlackHole 2ch; hold PTT and speak; verify transcription.
4. Mirror the daemon change to the other checkout; note `A` frame in REFERENCE.
5. Rollback: revert the capture loop + audio path; PTT hotkey relay unaffected.

## Open Questions

- Chunk size / sample rate: 16 kHz / 20 ms default; bump if choppy or if 豆包
  prefers 48 kHz.
- Should the daemon also auto-set 豆包's input to BlackHole? No — out of scope;
  the user configures the dictation app once.
