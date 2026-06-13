## Context

The Tab5 dashboard renders a full-frame 1280×720 PSRAM sprite (`M5Canvas spr`)
with M5GFX primitives + VLW smooth fonts loaded from LittleFS, pushed over
MIPI-DSI with dirty-band regions (full pushes are a visible right-to-left wipe
under rotation-3). Current layout (`src/tab5/ui.cpp`):

- **Sidebar** (0..300px): logo + "Claude Buddy", AGENTS list (2 session cards),
  clawd avatar (GIF or vector fallback), status pill.
- **Main** (300..1280px): header (name + state chip + tool + clock), transcript
  (now wrapped + scrollable at `F_SMALL22`), permission card.

Constraints that shape the design:
- **Fonts are pre-rasterized VLW** (fixed px size, no runtime scaling that looks
  good). New sizes/weights/faces require regenerating `data/fonts/*.vlw` via
  `tools/make_vlw.py` and `uploadfs`. The `full` charset = GB2312 L1 (3755
  hanzi); the `ui` charset only covers ASCII + a fixed literal set. **A
  transcript face MUST be `full`** or arbitrary Chinese garbles (just fixed).
- **Full-frame DSI pushes are expensive** (~1.8MB); redraws are gated by dirty
  bits (`DR_SIDEBAR|DR_HEADER|DR_BODY`). The redesign must preserve dirty-band
  discipline, not repaint everything every tick.
- **Render budget**: ~19 transcript rows at 30px pitch; per-frame wrap over up
  to 64 logical lines is fine at 400MHz but the wrap pass mutates the line
  buffer in place (temporary null-terminate) — keep that pattern.
- **Memory**: 512KB DIRAM (currently ~16% RAM), 9.6MB LittleFS (~8.7MB used
  after the full-charset small22). A monospace `full` face is ~1.9MB — adding
  one is feasible but tightens LittleFS; weigh against value.
- Heartbeat schema, `app` routing, and the permission round-trip are fixed —
  this change only touches rendering and on-device interaction.

## Goals / Non-Goals

**Goals:**
- A transcript that is instantly scannable by **role** (you / assistant / tool /
  error / permission / system) using color + gutter badge, not a single `>`.
- A cohesive Claude-Code visual language: coral accent, warm-neutral dark
  surfaces, soft rounded cards, an 8px spacing grid, a deliberate type scale.
- A session **tab bar** that reads as Claude-Code tabs (active accent, status
  dot, live metrics).
- Polished header, permission card, avatar state mapping, and scroll
  affordances — all within the dirty-band render budget.
- Legible at desk distance (~50–80cm): minimum body ~22px, strong contrast.

**Non-Goals:**
- No wire-protocol / daemon / `REFERENCE.md` changes.
- No new transport, no animation framework — motion stays cheap (per-tick
  phase off `millis()`), no tweening engine.
- Not a theming/skinning system; one curated Claude theme, tokens in code.
- No per-glyph runtime font scaling (VLW is fixed-size).
- Not changing the camera/gesture or RoverC paths.

## Decisions

### D1 — Transcript model: typed entries with a role, not a flat string

Today each entry is a raw string (`you: …` / `buddy: …` / tool text) and the
renderer prefixes a `>`. Decision: parse a **role** for each logical line and
render role-specific chrome.

- **Roles**: `USER`, `ASSISTANT`, `TOOL`, `ERROR`, `PERMISSION`, `SYSTEM`.
- **Source of role**: keep it firmware-side and zero-protocol by classifying on
  the existing text prefixes the daemons already emit
  (`you:` → USER, `buddy:` → ASSISTANT, `!fail ` → ERROR, `compacted`/
  `session …` → SYSTEM, otherwise → TOOL). The permission card is already a
  separate state (`permPending`), so PERMISSION rows are implicit.
  - *Alternative considered*: add a `role` field per entry in the heartbeat.
    Rejected — violates the "no protocol change" goal and couples the firmware
    redesign to two daemon branches. Prefix-classification is good enough and
    reversible.
- **Per-row visual**: a left **gutter badge** (e.g. small rounded chip or a
  2px color rail) colored by role + the role's text color; the badge replaces
  the uniform `>`. Strip the `you:`/`buddy:` prefix from the displayed text
  (the badge conveys it).

| Role | Accent | Badge | Text color | Face |
|---|---|---|---|---|
| USER | coral `#D97757` | `›` / "you" rail | TEXT | proportional |
| ASSISTANT | blue `#4493F8` | clawd dot | TEXT (newest), DIM (older) | proportional |
| TOOL | dim `#8B949E` | `⏵` | DIM | **mono** |
| ERROR | red `#F85149` | `✗` | ERR | mono |
| PERMISSION | amber `#D29922` | `?` | ATTN | proportional |
| SYSTEM | faint `#4A5562` | `·` | FAINT | proportional |

### D2 — Typography scale and a monospace lane

- Keep the existing VLW faces; **add one monospace `full` face** (e.g. SF Mono
  / Menlo at ~22px) for TOOL/ERROR lines so commands and paths align and read
  as code — the core Claude-Code feel.
  - *Alternative*: reuse `F_SMALL22` proportional for everything. Rejected —
    command/path text is the strongest "code" signal; mono is worth ~1.9MB.
  - *Fallback*: if LittleFS budget is too tight, ship without mono and use
    DIM proportional for tool lines (graceful degradation, gated behind a
    `g_vlwOk` check like the others).
- Type scale (px, VLW): title 40 (`F_BOLD40`), tab/section 28 (`F_BOLD28`),
  label/chip 22 (`F_BOLD22`), body 22 proportional (`F_SMALL22`), body 22 mono
  (new `F_MONO22`). Row pitch 30px; section spacing on the 8px grid.

### D3 — Layout regions (pixel grid, rotation-3 landscape 1280×720)

- **Sidebar** 0..300, panel elevation. Top: logo (22,20,40²) + wordmark.
  AGENTS label + LIVE/DEMO pill. Tab cards stacked. Avatar anchored lower
  (`AV_CX=150, AV_CY=H-222`). Bottom: link/status pill.
- **Main** `x0 = 324` (SB_W+PAD), width `wMain = 1280-300-48 = 932`.
  - Header band y 0..96, divider hairline at y=96.
  - Transcript body y 112..(botY), botY = `H-28` normally / `H-200` when a
    permission card is shown.
  - Permission card occupies the lower band when pending.
- Maintain `TRANS_TOP=112`, `TRANS_ROWH=30`, hanging indent `TRANS_IND` aligned
  under the badge.

### D4 — Session tab bar (sidebar cards restyled)

- Two cards = Claude Code / Cursor. Active card: CARD fill + 1px coral border +
  a 3px coral left rail; inactive: PANEL fill, hairline border, dimmed text.
- Each card: status dot (state color, breathe on ACTION/BUSY), name (BOLD28),
  state word (state color), right-aligned `N.Nk tok`. Selected by touch tap or
  ←/→ keys; per-session scroll preserved.

### D5 — Header & status chip

- Left: session name (BOLD40). Right of name: state **chip** (pill) colored by
  state with the state word; ACTION/BUSY get the existing breathe (`(now/450)&1`
  → accent). Far right: current tool (DIM, SMALL22) + clock. Hairline divider.

### D6 — Avatar state mapping

- GIF pack drives expression when present (`avatarReady()`); vector fallback
  maps state→eyes/mouth (idle lids, attn wide, done arcs) and opens the mouth
  with `micLevel`. Tighten: avatar reflects the **selected** session's state
  (`g_sess[g_sel].state`) — already wired; ensure ACTION pulses with the chip.

### D7 — Permission card

- Lower-band card with amber rail, "权限请求" eyebrow (BOLD22 amber), tool/cmd
  (BOLD28 TEXT), and two large touch buttons: filled green ✓允许 / outlined red
  ✗拒绝, each ≥220×72 hit target. Optimistic clear on tap; authoritative state
  arrives next heartbeat. Keyboard Enter/y = allow, Esc/n = deny.

### D8 — Scroll affordances

- Bottom-pinned by default (`scroll==0`). When scrolled up, show a compact
  "▼ 底部还有更多" hint bottom-right (non-permission only) and a thin scroll
  position rail on the right edge of the body (track + thumb sized by
  visible/total rows). Touch drag in the body and ↑/↓ keys scroll; tapping the
  hint / pressing ↓ to bottom re-pins. Word-aware wrap (break at spaces, UTF-8
  safe) is already in place.

### D9 — Render/dirty discipline

- Keep `DR_SIDEBAR|DR_HEADER|DR_BODY`. Role classification + wrap happen in the
  body-draw path (already rebuilt each body redraw via `buildRows`); extend the
  `DRow` descriptor with a `role` so badge/color/face are chosen per row.
  Tab-bar and chip changes mark only their bands. No new full-frame pushes.

## Risks / Trade-offs

- [LittleFS budget: +1.9MB mono `full` face on top of 8.7MB used] → Verify
  total ≤ 9.6MB partition before `uploadfs`; if it overflows, ship the
  graceful-degradation path (D2 fallback) and/or drop an unused weight.
- [Role-classification by text prefix is heuristic] → It mirrors exactly what
  the daemons emit today; if a daemon changes its prefixes, rows fall back to
  TOOL styling (safe). Document the prefixes in the spec so both sides stay in
  sync.
- [Per-row mixed faces complicate wrap width] → `buildRows` must measure with
  the row's role face; set the font before measuring each logical line. Slight
  extra `setFont` churn, negligible at 400MHz.
- [More chrome per row could reduce visible lines] → Keep badges compact (rail
  or single glyph in the existing 28px indent), do not add vertical padding
  beyond the 30px pitch; target ≥18 visible rows.
- [Aesthetic is subjective] → Land behind the existing build/flash loop; iterate
  on-device with the user (the "太丑→改" loop), keep changes token-scoped.

## Migration Plan

1. Land firmware-only; no daemon redeploy needed.
2. If adding `F_MONO22`: regenerate `data/fonts/mono22.vlw` (`--charset full`),
   confirm LittleFS fits, `uploadfs`.
3. Flash firmware (`pio run -e m5stack-tab5 -t upload`), free the serial port
   first (`launchctl bootout com.cursor-bridge`), restart the bridge after.
4. Rollback: revert the `src/tab5/` change and re-flash; fonts are additive so
   no LittleFS rollback needed (old firmware ignores the extra face).

## Open Questions

- Ship the monospace face now, or start with proportional-only and add mono in a
  follow-up if LittleFS is tight? (Default: attempt mono, fall back per D2.)
- Should role backgrounds be tinted bands (stronger separation) or just gutter
  rails (cleaner, cheaper to draw)? (Default: gutter rails first, evaluate
  on-device.)
- Do we want a subtle per-message timestamp / elapsed marker, or keep the
  transcript timeless? (Default: timeless for now.)
