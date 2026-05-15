# daemon-event-mapping

## ADDED Requirements

### Requirement: Session staleness tracking

cc-bridge's `apply_event` MUST stamp `state._sessions[sid]["last_seen"]` with
`time.monotonic()` on every branch that creates or accesses a session's
record. This is the single source of truth the reaper uses to decide
whether a session is alive.

#### Scenario: SessionStart stamps last_seen
- GIVEN a fresh `BuddyState`
- WHEN a `SessionStart` event arrives for a new sid
- THEN `state._sessions[sid]["last_seen"]` is set to roughly the current monotonic time

#### Scenario: Every per-session event stamps last_seen
- GIVEN a `BuddyState` with one tracked session
- WHEN any of `UserPromptSubmit`, `Stop`, `PreToolUse`, `PostToolUse`,
  `PermissionRequest`, `Notification` arrives for that sid
- THEN `state._sessions[sid]["last_seen"]` is refreshed to roughly the
  current monotonic time

### Requirement: Stale-session reaper

cc-bridge MUST run a background reaper that drops sessions idle for longer
than `STALE_SESSION_SEC` (default 600 s) and recomputes `state.total` and
`state.running` from the surviving session map. The reaper protects against
unbounded counter drift when `Stop` events are dropped upstream.

#### Scenario: Stale session is removed
- GIVEN `state._sessions = {"s1": {"running": True, "last_seen": t-999}}`
  and `state.running == 1`
- WHEN the reaper runs with `STALE_SESSION_SEC = 600`
- THEN `"s1"` is removed from `state._sessions` and `state.running` is recomputed to 0

#### Scenario: Counter drift corrected by recompute
- GIVEN `state.running == 5` but `state._sessions` actually contains only
  two records both with `running: True`
- WHEN the reaper runs and none of them are stale
- THEN `state.running` is recomputed to 2 (the truthful count)

#### Scenario: Non-stale sessions are left alone
- GIVEN `state._sessions` contains a session with `last_seen = monotonic() - 10`
- WHEN the reaper runs with `STALE_SESSION_SEC = 600`
- THEN that session is unchanged
