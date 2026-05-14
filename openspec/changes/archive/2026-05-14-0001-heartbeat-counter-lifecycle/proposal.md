# 0001 — heartbeat counter lifecycle

## Why

The on-device HUD shows R (running) / W (waiting) / token counters sourced from
`BuddyState`. Two are broken for the cc-bridge (Claude Code) path:

1. **W sticks at 1.** `apply_event` sets `state.waiting = max(state.waiting, 1)` on a
   `PermissionRequest` / permission `Notification`, but the async `apply_event` path
   never resets it. Only the *synchronous* `hook_permission.py` echo path
   (`core.py:_handle_wait_permission`) clears `waiting`. So once any permission prompt
   fires, the HUD shows W=1 forever.
2. **token never moves.** cc-bridge's `apply_event` has no token handling at all.
   cursor-bridge accumulates `output_tokens` from Cursor's `afterAgentResponse`
   events; Claude Code's standard hook events (PreToolUse / PostToolUse / Stop /
   UserPromptSubmit / …) carry no token-usage field, so cc-bridge has nothing to read.

## What changes

- **Fix W:** `apply_event` (cc-bridge) MUST reset `state.waiting` and `state.prompt`
  when the session stops being blocked on the user — i.e. when the turn progresses
  (`UserPromptSubmit`, `PreToolUse`, `Stop`).
- **Document the token gap:** Claude Code hooks don't expose token usage. cc-bridge
  cannot show a live token count without a different data source. This is recorded
  as a known limitation in the spec, not implemented in this change.

## Out of scope

- Token accounting for cc-bridge (no data source — see `design.md`).
- The `running` counter, which behaves correctly (R=1 while a turn is genuinely
  in flight is the intended reading).
