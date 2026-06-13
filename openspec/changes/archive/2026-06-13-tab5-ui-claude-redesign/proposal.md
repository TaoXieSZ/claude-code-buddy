## Why

The Tab5's 5″ 1280×720 dashboard is the flagship desk-buddy surface, but its
transcript and chrome were built during the design pass and still read like a
debug HUD: a single `>`-prefixed line per entry, one flat text color, no
role distinction between the user's prompt, the assistant's answer, tool calls,
and errors. After the recent wrap/scroll/font work the layout is functional but
visually plain — the user's direct feedback was "太丑" (ugly). We want the
device to look like a first-class extension of Claude Code: warm, calm, legible
across the room, with a conversation that is instantly scannable.

## What Changes

- **Conversation-style transcript with role lanes.** Replace the uniform
  `>`-prefixed rows with role-aware rendering: distinct gutter badge + color +
  (optional) background tint for `you` / assistant / tool / error / permission
  / system entries. Monospace face for tool/command lines; proportional face
  for prose.
- **Claude Code visual language.** Coral (`#D97757`) accent system, warm
  neutral dark surfaces in clear elevations, soft 14–18px rounded cards,
  consistent 8px spacing grid, a refined type scale with proper line-height
  and a readable max measure.
- **Session tab bar.** Restyle the two session cards (Claude Code / Cursor)
  into a Claude-Code-like tab strip with active-state accent, per-session
  status dot, live token/elapsed metering, and clear selected affordance.
- **Header & status chip.** Session title + animated state chip
  (IDLE/BUSY/ACTION/DONE/ERROR) + current tool + clock, laid out on a calm
  grid with the divider and breathing room.
- **Avatar expression.** Tie the clawd avatar (GIF + vector fallback) state
  and "mouth"/mic reactivity more tightly to session state for personality.
- **Permission card polish.** Redesign the 权限请求 card with clearer
  hierarchy, larger touch targets, and motion that reads as "waiting on you".
- **Scroll affordances.** Visible scroll position indicator / "more below"
  hint, smooth bottom-pinning, and a back-to-latest affordance.
- This is a **visual + interaction** change; the wire protocol (heartbeat
  schema, `app` routing, permission round-trip) is unchanged.

## Capabilities

### New Capabilities
- `tab5-dashboard-ui`: the on-device Tab5 dashboard's visual language and
  rendering/interaction behavior — layout regions, transcript role rendering,
  session tab bar, header/status, avatar state mapping, permission card, and
  scroll/selection interaction. Codifies what the firmware must render so the
  look stays consistent as features land.

### Modified Capabilities
<!-- None. The daemon event mapping and camera pipeline specs are unaffected;
     this change is firmware-render only and does not alter the heartbeat
     contract or permission wire protocol. -->

## Impact

- **Firmware (`src/tab5/`)**: `ui.cpp` (transcript render, sidebar/tab bar,
  header, permission card, scroll), `ui.h` (any new render API), `avatar.cpp`
  (state mapping), possibly `theme` constants. No daemon or protocol changes.
- **Fonts (`data/fonts/`, `tools/make_vlw.py`)**: may add a monospace VLW face
  for tool/command lines and/or additional weights; regenerated locally
  (assets are gitignored, derived from system fonts).
- **No change** to `tools/buddy_core`, `tools/cursor-bridge`, `tools/cc-bridge`,
  `REFERENCE.md`, or the `daemon-event-mapping` spec.
- Memory: richer per-row metadata + an optional mono font increase DIRAM/
  LittleFS usage; must stay within the 512KB RAM / 9.6MB LittleFS budget.
