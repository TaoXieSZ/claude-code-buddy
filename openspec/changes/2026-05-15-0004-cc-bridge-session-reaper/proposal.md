# 0004 — cc-bridge session reaper

## Why

The HUD shows `running=N` stuck above zero for hours after Claude Code goes
idle: the daemon's session map accumulates entries whose `Stop` event was
dropped (Unix socket hiccup, process restart, hook timeout), and `state.running`
counts those forever-"running" sessions. Symptom: StackChan stays BUSY long
after the user has stopped using Claude Code. Fix today is a manual
`launchctl kickstart -k gui/$(id -u)/com.cc-bridge` to reset BuddyState —
unacceptable as a steady-state UX.

cursor-bridge already has this problem solved (`reaper_loop` in
`tools/cursor-bridge/bridge.py`, 10-minute stale threshold) because Cursor
never fires SessionEnd hooks. cc-bridge has the same shape of problem but
no reaper.

## What changes

- **`tools/cc-bridge/bridge.py` `apply_event`**: every branch that creates or
  touches `state._sessions[sid]` MUST also stamp `s["last_seen"] = time.monotonic()`.
  Tracks staleness uniformly so the reaper has a single source of truth.
- **`tools/cc-bridge/bridge.py` new `reaper_loop`**: an async task started via
  `extra_tasks` that wakes every 60s, removes sessions older than
  `STALE_SESSION_SEC` (default 600 = 10 min), and recomputes `state.total` /
  `state.running` from the post-reap session map. Counters can't drift past
  the reaper interval.
- **Tests** (`tests/test_cc_bridge.py`): cover the touch-on-every-event invariant,
  the reaper dropping a stale session, and the recompute correcting a drifted
  counter.

## Out of scope

- The root cause of dropped `Stop` events (Unix socket reliability, hook
  delivery semantics). The reaper is a safety net, not a root-cause fix —
  acceptable because Claude Code is the upstream and we don't control its
  hook delivery.
- cursor-bridge — already has its own reaper with the same shape.
- Per-event timestamps in the heartbeat payload. The reaper uses
  `time.monotonic()` internally only.
