# Design

## W reset — where and when

`state.waiting` / `state.prompt` are set when the IDE blocks on a user decision.
They must clear when the block ends. In the async `apply_event` path there is no
explicit "permission resolved" event, so the trigger is **the next event that proves
the session has moved on**:

- `Stop` — the assistant turn ended.
- `PreToolUse` — a tool started running, which means a pending permission was granted.
- `UserPromptSubmit` — the user started a fresh turn.

On any of those, `apply_event` sets `state.waiting = 0` and `state.prompt = None`.

This mirrors `core.py:_handle_wait_permission`, which already does
`state.waiting = 0` / `state.prompt = None` on the synchronous path — the async path
just never had an equivalent.

Implementation: a tiny local helper `_clear_waiting(state)` called at the top of those
three branches in `tools/cc-bridge/bridge.py`. Surgical — no change to the BLE,
socket, or heartbeat layers.

## Token gap — why it's not fixed here

Confirmed by inspecting `~/Library/Logs/cc-bridge.log` and the Claude Code hook
schema: the standard hook events cc-bridge receives (`PreToolUse`, `PostToolUse`,
`Stop`, `UserPromptSubmit`, `SessionStart/End`, `Notification`, `PostCompact`) carry
**no token-usage field**. cursor-bridge can accumulate tokens only because Cursor's
`afterAgentResponse` hook includes `output_tokens`.

Showing a live token count for the Claude Code path would need a separate source
(e.g. tailing the session transcript JSONL). That is a larger change with its own
proposal; this change records the limitation in the spec so the gap is explicit
rather than looking like an oversight.
