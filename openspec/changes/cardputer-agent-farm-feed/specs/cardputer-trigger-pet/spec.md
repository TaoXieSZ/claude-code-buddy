## ADDED Requirements

### Requirement: Render a live trigger feed on the LCD

The Cardputer SHALL render the most recent trigger firings as a scrollable feed
on its 240×135 LCD. Each row SHALL show the `trigger_name`, an icon derived from
`trigger_type`, a result indicator from `result`, and a relative time from
`timestamp`.

#### Scenario: New firing appears at top
- **WHEN** the transport emits a new `TriggerLog` entry
- **THEN** it appears at the top of the on-screen feed with its type icon and
  result indicator

#### Scenario: Type icon mapping
- **WHEN** an entry has `trigger_type` of `slack_event`, `cron`, `jira_poll`, or
  `manual`
- **THEN** the row shows the corresponding icon so the source is identifiable at
  a glance

### Requirement: React to trigger results as a pet

The device SHALL derive a single aggregate "mood" from recent results and SHALL
play a short sound on notable events. An `error` result SHALL produce a worried
mood + alert chirp; a `success` result SHALL produce a happy mood + soft chirp;
`queued`/`skipped_*` results SHALL be shown silently without changing the mood
to alarmed.

#### Scenario: Error draws attention
- **WHEN** a new entry has `result: error`
- **THEN** the pet shows a worried/alert state and plays the alert chirp

#### Scenario: Success celebrates briefly
- **WHEN** a new entry has `result: success`
- **THEN** the pet shows a happy state with a soft chirp, then settles

#### Scenario: Skipped/queued stays calm
- **WHEN** a new entry has `result` of `queued`, `skipped_busy`,
  `skipped_paused`, or `skipped_no_match`
- **THEN** the feed updates but the pet does not enter the alarmed state and stays
  silent

### Requirement: Sleep when idle

After a configurable period with no new trigger firings, the device SHALL enter a
low-activity SLEEP state (dozing pet, dimmed/clearable screen) and SHALL wake on
the next new firing.

#### Scenario: Doze after quiet
- **WHEN** no new firing arrives for the idle threshold
- **THEN** the pet enters SLEEP and the screen dims to reduce battery use

#### Scenario: Wake on activity
- **WHEN** a new firing arrives while SLEEPing
- **THEN** the pet wakes, the screen restores, and the new entry is shown

### Requirement: Scroll the feed with the keyboard

The device SHALL let the user scroll the feed history using the keyboard. In v1
the keyboard is navigation-only; it SHALL NOT send any data back to Agent Farm.

#### Scenario: Scroll through history
- **WHEN** the user presses the up/down navigation keys
- **THEN** the feed scrolls through buffered recent entries

#### Scenario: No write-back in v1
- **WHEN** any key is pressed
- **THEN** no request is sent to Agent Farm (read-only device in v1)
