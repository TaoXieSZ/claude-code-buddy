## ADDED Requirements

### Requirement: Stream the mic while push-to-talk is held

While push-to-talk is held, the Tab5 SHALL capture 16 kHz mono PCM from its
microphone and stream it to the host over serial as framed base64 audio lines,
starting on `mic down` and stopping on `mic up`. No audio SHALL be streamed when
PTT is not held.

#### Scenario: Audio flows only while held
- **WHEN** the user holds the PTT button
- **THEN** the device emits `A<base64>` audio frames continuously until release,
  and emits none after `mic up`

#### Scenario: Coexists with other traffic
- **WHEN** audio frames are streaming
- **THEN** heartbeats (`{…}`) and other control frames are still parsed
  correctly (audio uses a distinct `A` line prefix)

### Requirement: Daemon plays the stream into BlackHole

The daemon SHALL decode the audio frames and play the PCM into the BlackHole
virtual audio device via `ffmpeg`, so a dictation app configured with BlackHole
as its input hears the Tab5 microphone. The audio path SHALL open on `mic down`
and close shortly after `mic up`.

#### Scenario: PCM reaches BlackHole
- **WHEN** the daemon receives audio frames between `mic down` and `mic up`
- **THEN** it writes the decoded PCM to an ffmpeg process whose output device is
  BlackHole 2ch

#### Scenario: Pipe lifecycle
- **WHEN** `mic up` arrives
- **THEN** the daemon flushes and closes the ffmpeg pipe (no lingering hold on
  the audio device)

#### Scenario: ffmpeg or device missing
- **WHEN** ffmpeg is unavailable or the BlackHole device cannot be found
- **THEN** the daemon logs a warning and continues (PTT hotkey relay still
  works; no crash)

### Requirement: No new host dependency

The audio path SHALL use the already-present `ffmpeg` and BlackHole; it SHALL
NOT require a new Python package or system install.

#### Scenario: Works with existing tools
- **WHEN** the feature is deployed on a host that already has ffmpeg + BlackHole
- **THEN** no additional dependency installation is required
