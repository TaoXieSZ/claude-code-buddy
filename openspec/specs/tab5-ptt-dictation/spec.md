# tab5-ptt-dictation Specification

## Purpose
TBD - created by archiving change tab5-mac-input-bridge. Update Purpose after archive.
## Requirements
### Requirement: On-device push-to-talk trigger

The Tab5 SHALL provide a hold-to-talk affordance that emits the existing
`cmd:mic` control command over the serial link, so the daemon drives the Mac
dictation / 输入法 hotkey exactly as the stick does. The Tab5 SHALL NOT capture
or stream audio for this feature.

#### Scenario: Press starts dictation
- **WHEN** the user presses and holds the on-screen mic button
- **THEN** the firmware sends `{"cmd":"mic","state":"down"}` over serial

#### Scenario: Release ends dictation
- **WHEN** the user releases the mic button
- **THEN** the firmware sends `{"cmd":"mic","state":"up"}` over serial

#### Scenario: Daemon relays to the Mac hotkey unchanged
- **WHEN** the daemon receives `cmd:mic` from the Tab5
- **THEN** it relays the configured PTT keystroke per `PTT_MODE`
  (tap / hold / double_tap) using the existing `_send_key` path, with no
  Tab5-specific daemon changes

### Requirement: Recording indicator

While push-to-talk is active, the Tab5 SHALL show a clear on-screen recording
indicator, and clear it when released.

#### Scenario: Indicator while held
- **WHEN** the mic button is held down
- **THEN** a REC indicator is shown (e.g. blinking) at the top of the screen

#### Scenario: Indicator cleared on release
- **WHEN** the mic button is released
- **THEN** the REC indicator is removed

