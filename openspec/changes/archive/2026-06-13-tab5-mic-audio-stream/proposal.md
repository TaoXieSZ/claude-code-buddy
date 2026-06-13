## Why

The Tab5 PTT button already triggers the Mac dictation hotkey (豆包 长按右
Command), but PTT carries **no audio** — it only presses a key. The user routes
dictation input through **BlackHole** expecting the Tab5's own microphone to be
the source, but nothing feeds BlackHole. To make the Tab5 a real wireless
dictation mic, its ES7210 microphone must capture audio, stream it to the Mac
over the existing USB-CDC link, and the daemon must play it into the BlackHole
virtual device — which the dictation app listens to.

## What Changes

- **Firmware mic streaming.** While push-to-talk is held, the Tab5 captures
  16 kHz mono PCM from the ES7210 mic and streams it over serial as framed
  base64 audio chunks (`A<base64>` lines), starting on `mic down` and stopping
  on `mic up`. Reuses the existing I2S mic (no new bus; speaker is idle during
  PTT).
- **Daemon → BlackHole.** The daemon decodes the audio frames and writes the
  raw PCM to an `ffmpeg` subprocess whose output is the **BlackHole 2ch**
  CoreAudio device. ffmpeg is already installed — **no new Python dependency**.
  The pipe opens on `mic down`, closes a moment after `mic up`.
- **Unchanged PTT trigger.** `cmd:mic` still presses the dictation hotkey; the
  audio stream runs alongside it. So one hold = hotkey + live audio into
  BlackHole; the dictation app (input = BlackHole) transcribes the Tab5's mic.

## Capabilities

### New Capabilities
- `tab5-mic-audio`: capture the Tab5 microphone while PTT is held and stream it
  to the Mac's BlackHole device. Covers the firmware capture/stream, the `A`
  audio wire frame, and the daemon's PCM→BlackHole playback path + lifecycle.

### Modified Capabilities
<!-- tab5-ptt-dictation gains a companion audio stream but its requirement
     (emit cmd:mic down/up) is unchanged; the audio is additive on the same
     hold gesture. No spec rewrite needed. -->

## Impact

- **Firmware (`src/tab5/`)**: `main.cpp` (capture loop while held), `feed.cpp`
  (`A` frame TX), `ui.cpp`/`ui.h` (`uiMicHeld()` accessor). I2S mic already
  initialized; no speaker conflict during PTT.
- **Daemon (`tools/buddy_core/core.py`)**: `SerialPortWriter` RX gains an audio
  path — on `A` frames, decode and feed an `ffmpeg` subprocess targeting
  BlackHole; manage the subprocess lifecycle around mic down/up. Mirrored to
  the checkout whose daemon owns the Tab5 serial (feat/cursor-next).
- **Host**: requires `ffmpeg` (present) and the BlackHole 2ch device (present);
  the dictation app's input must be set to BlackHole.
- **No new Python/system dependency.** Heartbeat schema, permission, `cmd:key`,
  and `cmd:shot` are unaffected; `A` frames are additive on the control channel.
- Bandwidth: 16 kHz mono S16 ≈ 256 kbps (~32 KB/s) only while held; base64 ≈
  43 KB/s — fine over USB-CDC; transient.
