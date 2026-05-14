# Design

## Data path

```
Claude Code ──StatuslineStdin──▶ statusline_hud.py ──{event:hud}──▶ /tmp/cc-bridge.sock
                                       │                                  │
                                       └──stdin──▶ omc-hud.mjs             ▼
                                                   (terminal statusline)  cc-bridge apply_event
                                                                            │
                                                                          BuddyState ──heartbeat──▶ firmware HUD
```

Why a statusline proxy and not a separate poller: Claude Code already invokes the
statusline command on a natural cadence with a fresh `StatuslineStdin`. Tapping that
needs no new daemon, no polling loop, and no patch to the user-global, regenerable
`omc-hud.mjs`. The proxy is a thin, repo-owned script; the user just repoints
`statusLine.command` at it.

## statusline_hud.py

- Reads all of stdin (the `StatuslineStdin` JSON).
- Extracts: `context_window.used_percentage` → `context_pct`;
  `context_window.current_usage.{input,cache_creation_input,cache_read}_tokens`
  summed → `tokens`; `rate_limits.five_hour.used_percentage` → `limit_5h`;
  `rate_limits.seven_day.used_percentage` → `limit_7d`; `model.display_name` → `model`;
  session elapsed derived from the transcript / `sessionStartTimestamp` → `session_ms`.
- Fire-and-forgets `{"event":"hud", ...}` to the cc-bridge socket with a short
  timeout, swallowing every error — the statusline MUST NOT stall or fail if the
  daemon is down (same contract as `hook.py`).
- Re-feeds the captured stdin to the real OMC HUD and prints its stdout verbatim.
  OMC HUD path resolved from `CC_BRIDGE_HUD_TARGET` env, default
  `${CLAUDE_CONFIG_DIR:-~/.claude}/hud/omc-hud.mjs`. If the target is missing, the
  proxy still forwards metrics and prints nothing (degrades gracefully).

## cc-bridge `hud` event

`apply_event` gains an `elif name == "hud"` branch that copies the six fields onto
`state` (missing fields left untouched, so a partial payload doesn't zero things).
Returns `True` so the heartbeat re-emits.

The `hud` event does **not** touch sessions / running / waiting — it is pure metric
telemetry, orthogonal to the lifecycle events.

## BuddyState fields

New: `context_pct: int`, `model: str`, `limit_5h: int`, `limit_7d: int`,
`session_ms: int`. `tokens` already exists — the `hud` event sets it directly
(absolute value from the statusline, not accumulated like cursor-bridge does).
`to_payload()` emits all of them every heartbeat (not one-shot — they are current
state, not events).

## Firmware HUD layout

The top card grows from one row to two (HUD_H 32 → ~50; the face box shrinks
accordingly — it has slack). Row 1: `model · session` left, `context%` right.
Row 2: `tokens` left, `5h% · 7d%` right. Existing R/W move into row 1 or are
dropped in favour of the richer metrics — decided during implementation against
the actual pixel budget.
