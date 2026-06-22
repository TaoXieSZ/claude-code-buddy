## ADDED Requirements

### Requirement: Shared dark palette and card texture

Both Agent Farm UIs (Tab5 and Cardputer) SHALL render with the buddy dashboard's
`th::` three-elevation dark palette, Claude coral accent, and semantic state
colors. Surfaces that group content (feed rows, status pills) SHALL use rounded
shapes with a lift border rather than flat fills.

#### Scenario: Tab5 uses the dashboard palette
- **WHEN** the Tab5 agent-farm UI renders
- **THEN** the background, panel, and card surfaces use the `th::` BG/PANEL/CARD
  elevations and the coral accent, matching `src/tab5/ui.cpp`

#### Scenario: Result colors are semantic and consistent
- **WHEN** a feed entry shows a result (`success`/`error`/`queued`/`skipped_*`)
- **THEN** its accent uses the semantic color (success=green, error=red,
  queued=blue, skipped=amber/grey) consistently on both screens

### Requirement: Tab5 dashboard composition

The Tab5 UI SHALL use a sidebar + main split with consistent padding and
alignment. The main area SHALL have a header band (title, status chip, divider)
and present the trigger feed as a vertically aligned column of cards.

#### Scenario: Header band
- **WHEN** the Tab5 UI renders
- **THEN** the main area shows a title, a status chip (LIVE/OFFLINE/AUTH), and a
  divider rule beneath them

#### Scenario: Feed is an aligned card column
- **WHEN** trigger entries are shown
- **THEN** each entry is a rounded card with a left result rail, aligned to a
  consistent left edge and padding, stacked vertically

### Requirement: Framed avatar with balanced whitespace

The clawd avatar SHALL be seated in a centered sidebar stage with deliberate
framing and balanced whitespace, with a mood tag beneath it — not floating with
uneven dead space.

#### Scenario: Avatar is centered and framed
- **WHEN** the sidebar renders
- **THEN** the clawd GIF is centered in the sidebar at a fixed position with
  even margins, with a mood label beneath it

#### Scenario: Mood reflects latest result
- **WHEN** the latest trigger result is success / error / long-idle
- **THEN** the avatar shows celebrate / dizzy / sleep respectively (GIF when the
  pack is present, vector face otherwise)

### Requirement: Smooth typography on Tab5 with fallback

The Tab5 UI SHALL render text with anti-aliased VLW fonts when the LittleFS
`/fonts` assets are present, and SHALL fall back to built-in GFX fonts when they
are absent, without failing to render.

#### Scenario: Smooth fonts when present
- **WHEN** `/fonts/*.vlw` are on LittleFS
- **THEN** titles, body, and monospace lines render with the anti-aliased faces

#### Scenario: Fallback when absent
- **WHEN** `/fonts` is missing (uploadfs not run)
- **THEN** the UI still renders fully using built-in GFX fonts

### Requirement: Cardputer compact restyle

The Cardputer (240×135) UI SHALL apply the same palette and accent in a compact
form that stays legible, rather than porting the full sidebar+main composition.

#### Scenario: Cardputer uses the shared palette
- **WHEN** the Cardputer agent-farm UI renders
- **THEN** it uses the `th::`-style dark palette and accent with compact feed
  rows and a tidy header

#### Scenario: Legible at 240x135
- **WHEN** feed rows render on the Cardputer
- **THEN** trigger name, result, and time remain readable (contrast and size
  prioritized over chrome/density)
