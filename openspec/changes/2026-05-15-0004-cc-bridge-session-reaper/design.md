# Design

## Why a reaper, not a root-cause fix

Claude Code's `Stop` event is occasionally dropped between the IDE and the
cc-bridge Unix socket — observed empirically as `running=N>0` persisting
hours after the user idled. The drop modes are outside this repo's control
(IDE hook delivery, socket pressure, daemon restart mid-turn). Symmetric
mitigation: `apply_event` keeps stamping per-session timestamps, and a
periodic reaper drops anything that hasn't been touched in 10 minutes and
recomputes counters from the live session set. The recompute is the part
that actually fixes the symptom — even if the reap window is wrong, the
counter can never grow unbounded past the next reap interval.

cursor-bridge already runs the same pattern for its own reason (Cursor
never fires SessionEnd at all). This change brings cc-bridge to parity;
the code mirrors cursor-bridge's `reaper_loop` (10-min stale threshold,
60-s cadence) so future eyes only need to learn the pattern once.

## Touch invariant

Every `apply_event` branch that creates or accesses `state._sessions[sid]`
also stamps `s["last_seen"] = time.monotonic()`. Tests parameterise over
the full event set to enforce this — adding a new event type without a
matching `last_seen` write would let stale records survive when they
shouldn't.

`time.monotonic()` (not `time.time()`): the reaper measures elapsed time
between events, not wall-clock. Monotonic is immune to NTP step / DST.

## Recompute, don't decrement

When the reaper drops a session, it does NOT do `state.running -=
s["running"]`. It rebuilds from scratch:

```python
state.total = len(state._sessions)
state.running = sum(1 for s in state._sessions.values() if s.get("running"))
```

Decrement-based bookkeeping is what got us into the drift in the first
place; recompute is impossible to underflow / overflow.

## Threshold tuning

`STALE_SESSION_SEC = 600` (10 min). Rationale:

- The longest legitimately-running tool we see is a long shell command or
  a `pytest` suite. Multi-minute, rarely >10. PreToolUse stamps `last_seen`
  at tool start, but no event fires *during* a tool. A 15-min tool would
  get reaped mid-flight.
- That's acceptable. The reaper isn't authoritative about session
  lifecycle — when PostToolUse eventually fires for a reaped sid,
  `apply_event` will recreate the session record (existing fall-through:
  "if sid not in state._sessions" → create with running=False), then
  continue. The drift fix is more valuable than perfectly tracking a
  hypothetical 15-min tool.
- If 10 min turns out to be too aggressive in practice, bump via an env
  var — the constant is module-level for that reason.

## Tests use sub-second thresholds

The unit tests construct a `BuddyState`, write a session record with
`last_seen` set to `time.monotonic() - 999`, run one reaper iteration
inline (factored out of the asyncio loop), and assert the session is
gone + counters recomputed. No real sleeps. The cadence/asyncio plumbing
is trusted from cursor-bridge.
