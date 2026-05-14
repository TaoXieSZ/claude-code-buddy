# 0002 — HUD metrics integration

## Why

The stackchan on-device HUD shows R / W / token counts, but `tokens` is always 0 on
the Claude Code path — change `0001-heartbeat-counter-lifecycle` established that
Claude Code *hook* events carry no token data.

Claude Code's **statusline** stdin does. On every statusline render Claude Code hands
the script a `StatuslineStdin` payload with `context_window` (used %, real input /
cache token counts), `rate_limits` (5h / 7d used %), and `model`. OMC HUD already
consumes exactly this. We can tap the same feed to give the stackchan the live
metrics it currently can't show.

## What changes

- **New statusline proxy** `tools/cc-bridge/statusline_hud.py` — set as Claude Code's
  `statusLine.command`. It reads `StatuslineStdin`, fire-and-forwards a `hud` event to
  the cc-bridge Unix socket (same fire-and-forget pattern as `hook.py`), then chains
  to the real OMC HUD (`~/.claude/hud/omc-hud.mjs`) so the terminal statusline is
  unchanged.
- **New `hud` event** in `apply_event` (cc-bridge) — populates new `BuddyState`
  fields: `context_pct`, `tokens` (real count, replacing the always-0), `limit_5h`,
  `limit_7d`, `model`, `session_ms`.
- **Heartbeat gains those fields** via `BuddyState.to_payload()`.
- **Firmware** — the top HUD card becomes a 2-row ACNH card showing
  model · session · context% / tokens · 5h · 7d.

## Out of scope

- cursor-bridge (Cursor has its own statusline story; this is the Claude Code path).
- Any OMC HUD element beyond the four metric groups the user picked.
- Replacing OMC HUD — the proxy is additive; the terminal statusline is untouched.
