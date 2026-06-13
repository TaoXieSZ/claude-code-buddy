# tab5-dashboard-ui Specification

## Purpose
TBD - created by archiving change tab5-ui-claude-redesign. Update Purpose after archive.
## Requirements
### Requirement: Role-aware transcript rendering

The Tab5 dashboard SHALL render each transcript entry according to its role
(USER, ASSISTANT, TOOL, ERROR, PERMISSION, SYSTEM), classified firmware-side
from the entry text prefix the daemons emit, using a per-role gutter badge and
text color rather than a uniform `>` marker. The displayed text SHALL have the
role prefix (`you:` / `buddy:` / `!fail`) stripped.

#### Scenario: User prompt styled as USER
- **WHEN** an entry begins with `you:`
- **THEN** it renders with the USER badge/rail and coral accent, and the
  literal `you:` prefix is not shown in the body text

#### Scenario: Assistant answer styled as ASSISTANT
- **WHEN** an entry begins with `buddy:`
- **THEN** it renders with the ASSISTANT badge and the answer text without the
  `buddy:` prefix

#### Scenario: Failed tool styled as ERROR
- **WHEN** an entry begins with `!fail`
- **THEN** it renders with the ERROR badge and error color

#### Scenario: Unclassified entry falls back to TOOL
- **WHEN** an entry matches no known role prefix
- **THEN** it renders with the TOOL style (no crash, no blank row)

### Requirement: Monospace lane for tool and error lines

Tool and error transcript lines SHALL render in a monospace face when a
monospace VLW font is available, so commands and file paths align. When the
monospace font fails to load, the firmware SHALL fall back to the proportional
face without garbling.

#### Scenario: Mono present
- **WHEN** the monospace VLW face loaded successfully
- **THEN** TOOL and ERROR rows use the monospace face

#### Scenario: Mono missing — graceful fallback
- **WHEN** the monospace VLW face is absent or failed to load
- **THEN** TOOL and ERROR rows render in the proportional face and remain legible

### Requirement: Transcript renders CJK without garbling

Every font used to render transcript text SHALL cover the CJK character set
(GB2312 level-1 / `full` charset). The firmware SHALL NOT render arbitrary
Chinese answer text with a `ui`-charset-only face.

#### Scenario: Chinese answer is legible
- **WHEN** an assistant answer containing common Chinese characters is displayed
- **THEN** the glyphs render correctly (no tofu/garbage) at the transcript font size

### Requirement: Session tab bar

The sidebar SHALL present the two sessions (Claude Code, Cursor) as a tab strip:
the active tab visually distinguished by accent border/rail and full-contrast
text; each tab showing a state dot (state color), the session name, the state
word, and a token count. The active session SHALL be selectable by touch tap or
left/right keys, and each session's scroll position SHALL be preserved across
switches.

#### Scenario: Active tab is distinguished
- **WHEN** a session is selected
- **THEN** its card shows the accent border/rail and full-contrast text while the
  other card is dimmed

#### Scenario: Switch by touch
- **WHEN** the user taps the inactive session card
- **THEN** the main area switches to that session and shows that session's
  preserved scroll position

#### Scenario: Switch by key
- **WHEN** the user presses the left or right arrow key
- **THEN** the selected session toggles

### Requirement: Header with animated state chip

The main header SHALL show the selected session name, a state chip colored by
state (IDLE/BUSY/ACTION/DONE/ERROR) with the state word, the current tool, and a
clock, separated from the transcript by a hairline divider. The chip SHALL
animate (breathe) while the state is ACTION or BUSY.

#### Scenario: Chip reflects state
- **WHEN** the selected session is in the BUSY state
- **THEN** the chip shows the BUSY color and word and breathes

#### Scenario: Chip on idle
- **WHEN** the selected session is IDLE
- **THEN** the chip shows the IDLE color and word and does not breathe

### Requirement: Permission card

The dashboard SHALL show a permission card when a permission request is pending
for the selected session — with an amber rail, the tool/command text, and two
touch targets (allow / deny) each at least 220×72 px. Tapping a target SHALL
optimistically clear the card; the authoritative state arrives on the next
heartbeat. Keyboard Enter/y SHALL allow and Esc/n SHALL deny.

#### Scenario: Tap allow
- **WHEN** a permission is pending and the user taps ✓允许
- **THEN** the card clears immediately and an allow verdict is queued for the
  originating session/app

#### Scenario: Keyboard deny
- **WHEN** a permission is pending and the user presses Esc or n
- **THEN** a deny verdict is queued for the originating session/app

### Requirement: Transcript scrolling and bottom-pinning

The transcript SHALL pin to the newest line by default and remain scrollable via
touch drag and up/down keys. When scrolled away from the bottom, the dashboard
SHALL show a "more below" affordance and a scroll-position indicator. New entries
arriving while pinned SHALL keep the view at the newest line.

#### Scenario: Auto-pin at bottom
- **WHEN** the view is at the bottom and a new entry arrives
- **THEN** the view stays pinned showing the newest line

#### Scenario: Scrolled-up affordance
- **WHEN** the user has scrolled up off the bottom (non-permission view)
- **THEN** a "more below" hint is shown

#### Scenario: Drag to scroll
- **WHEN** the user drags within the transcript body
- **THEN** the transcript scrolls, revealing older lines on downward drag

### Requirement: Dirty-band render discipline

The dashboard SHALL repaint only the regions affected by a change
(sidebar / header / body), preserving the existing dirty-band mechanism, and
SHALL NOT trigger a full-frame push for transcript-only or chip-only updates.

#### Scenario: Body-only update
- **WHEN** only transcript content or scroll changes
- **THEN** only the body band is repainted (no full-frame wipe)

