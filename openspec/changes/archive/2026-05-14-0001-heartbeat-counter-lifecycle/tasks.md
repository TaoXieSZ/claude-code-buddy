# Tasks

- [x] Verify whether Claude Code hook events carry token data
      (inspected `~/Library/Logs/cc-bridge.log` + the hook schema — they don't;
      see `design.md`).
- [x] Write failing tests in `tests/test_cc_bridge.py` for the W reset:
      `Stop`, `PreToolUse`, and `UserPromptSubmit` each clear `waiting`/`prompt`.
- [x] Fix `apply_event` in `tools/cc-bridge/bridge.py` — add `_clear_waiting()`
      and call it from the `Stop`, `PreToolUse`, `UserPromptSubmit` branches.
- [x] `pytest -q` green.
- [x] Token gap recorded in the delta spec (no implementation — no data source).
- [ ] `openspec archive 0001-heartbeat-counter-lifecycle` — merge the delta into
      `openspec/specs/daemon-event-mapping/spec.md`.
