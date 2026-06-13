## ADDED Requirements

### Requirement: Keyboard always relays to the Mac; dashboard is touch-driven

The Tab5 keyboard (accessory + USB-A HID) SHALL always relay keystrokes to the
Mac as a second keyboard — there is no mode toggle. The dashboard SHALL remain
fully operable by touch (drag-to-scroll, tap a session card to switch, tap the
allow/deny buttons), so the keyboard and the dashboard are usable at the same
time without switching.

#### Scenario: Keys go to the Mac
- **WHEN** the user types on the Tab5 keyboard
- **THEN** the keystrokes are relayed to the Mac (not consumed by the dashboard)

#### Scenario: Dashboard still controllable while typing
- **WHEN** the user drags the transcript / taps a session card / taps ✓ or ✗
- **THEN** the dashboard scrolls / switches session / answers permission via
  touch, independent of keyboard input

### Requirement: Printable character relay

In MAC mode, printable keys pressed on the Tab5 keyboard SHALL be typed into the
Mac as the corresponding Unicode characters, independent of the Mac keyboard
layout.

#### Scenario: Letter typed into the Mac
- **WHEN** in MAC mode the user presses a printable key (e.g. `a`)
- **THEN** the firmware sends `{"cmd":"key","ch":"a"}` and the daemon types the
  character `a` into the focused Mac application

#### Scenario: Layout independence
- **WHEN** a printable character is relayed
- **THEN** the daemon types it via the Unicode-string path so the result does not
  depend on the Mac's active keyboard layout

### Requirement: Special keys and modifiers relay

In MAC mode the daemon SHALL relay and apply non-printable keys (Return,
Backspace, Tab, Esc, arrows, Delete) and modifier combinations (⌘/⌥/⌃/⇧).

#### Scenario: Special key
- **WHEN** in MAC mode the user presses Return
- **THEN** the firmware sends `{"cmd":"key","key":"enter"}` and the daemon emits a
  Return keystroke to the Mac

#### Scenario: Modifier combination
- **WHEN** in MAC mode the user presses a key with a held modifier (e.g. ⌘+A)
- **THEN** the firmware includes the modifier (`{"cmd":"key","ch":"a","mods":["cmd"]}`)
  and the daemon applies it as the corresponding event flags

### Requirement: Relay requires accessibility and fails safely

Keyboard relay SHALL use the daemon's existing macOS Accessibility grant
(same as PTT). If key synthesis is unavailable, the daemon SHALL log a warning
and drop the key rather than crash.

#### Scenario: Quartz unavailable
- **WHEN** the daemon cannot synthesize a key event (Quartz/permission missing)
- **THEN** it logs a warning and continues; no crash, other functions unaffected
