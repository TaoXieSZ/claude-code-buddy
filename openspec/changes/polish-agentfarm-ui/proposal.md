## Why

The Agent Farm desk-pet UI shipped fast but looks rough next to the polished
buddy dashboard (`src/tab5/ui.cpp`): the Tab5 layout is loose, the clawd avatar
sits awkwardly with too much dead space, the palette/card texture is flat, and
the header/status bar is plain. The user wants both the Tab5 (primary) and
Cardputer agent-farm UIs brought up to the buddy dashboard's visual quality.

## What Changes

- **Adopt the buddy dashboard's visual language as a shared style** for the
  Agent Farm UIs (Tab5 + Cardputer): the `th::` three-elevation dark palette,
  Claude coral accent, semantic state colors, rounded lift-border cards.
- **Tab5 layout/composition rework**: a proper sidebar + main split with
  consistent padding, alignment, and visual hierarchy; the feed becomes a clean
  card column; the header/status bar gets the dashboard treatment (title, state
  chip, divider) instead of the current plain bar.
- **Avatar area**: size, center, and frame the clawd GIF deliberately (stage
  card + balanced whitespace + mood tag), matching how `ui.cpp` seats the avatar
  — no more floating face with dead space.
- **Typography**: switch Tab5 from the blocky built-in GFX fonts to the
  anti-aliased **VLW smooth fonts** (mono/proportional/bold) the buddy dashboard
  uses, with built-in fallback when `/fonts` is absent.
- **Cardputer (240×135)**: a lighter polish of the same language — palette,
  accent, compact cards, tidied header — within the small-screen constraints.
- No change to the data path, polling, dedup, or pet-mood semantics — this is
  purely presentation.

## Capabilities

### New Capabilities
- `agentfarm-ui-style`: the shared visual design system for the Agent Farm
  desk-pet UIs — palette and card texture, layout/composition (sidebar + feed),
  avatar framing, header/status bar, and typography — applied to the Tab5 and
  Cardputer targets at their respective resolutions.

### Modified Capabilities
<!-- None at the spec level. The cardputer-trigger-pet feed behavior, transport,
     dedup, and mood mapping are unchanged; this change only restyles the
     presentation. -->

## Impact

- **Firmware**: `src/tab5_agentfarm/feed_ui_tab5.{h,cpp}` (major rework toward
  the dashboard look), `src/cardputer/feed_ui.cpp` (lighter restyle). Reuses the
  `th::` palette and `card()/pill()/statusDot()` helper patterns from
  `src/tab5/ui.cpp`.
- **Fonts**: Tab5 gains VLW smooth fonts loaded from LittleFS `/fonts`
  (generated via `tools/make_vlw.py`, pushed with `uploadfs`); built-in GFX
  fonts remain the fallback. May add the `/fonts` assets to the tab5-agentfarm
  filesystem image.
- **Build**: `platformio.ini` `tab5-agentfarm` already has LittleFS; no new env.
- **Docs**: update `docs/cardputer-agentfarm.md` UI section.
- No change to `src/agentfarm_feed/` (transport), Agent Farm, or the wire data.
