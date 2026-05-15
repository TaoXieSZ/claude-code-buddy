# Tasks

- [ ] `tools/cc-bridge/bridge.py`: import `time` (already imported); add
      `STALE_SESSION_SEC = 600` constant.
- [ ] `tools/cc-bridge/bridge.py` `apply_event`: stamp
      `state._sessions[sid]["last_seen"] = time.monotonic()` in every branch
      that creates or accesses `_sessions[sid]` (SessionStart, UserPromptSubmit,
      Stop, PreToolUse, PostToolUse, Permission/Notification).
- [ ] `tools/cc-bridge/bridge.py`: new `reaper_loop(state, dirty)` coroutine.
      60s sleep cadence. Drops sessions whose `last_seen` is older than
      `STALE_SESSION_SEC`. Recomputes `state.total` and `state.running` from
      the post-reap session map. Sets `dirty` if anything changed.
- [ ] `tools/cc-bridge/bridge.py`: pass `extra_tasks=[reaper_loop]` to `run()`.
- [ ] Tests (`tests/test_cc_bridge.py`):
      - last_seen set on every event type (parameterised).
      - reaper drops a stale session and decrements counters.
      - reaper recomputes a drifted counter (Stop missed → running stuck at 1
        → after reaper, running=0).
      - non-stale sessions left alone.
- [ ] `make test` green. No firmware changes.
- [ ] `openspec archive 0004-cc-bridge-session-reaper`.
