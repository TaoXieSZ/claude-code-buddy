# daemon-event-mapping

## Purpose

The bridge daemons (`tools/cc-bridge/bridge.py`, `tools/cursor-bridge/bridge.py`)
translate IDE hook events into mutations of the shared `BuddyState`
(`tools/buddy_core/core.py`). `apply_event(state, ev) -> bool` is the pure core of
that translation: it mutates `state` in place and returns `True` when the change is
material enough to warrant an immediate heartbeat emit.

This spec captures the *current* behaviour of that mapping. It is the source of truth
for what the firmware HUD's R (running) / W (waiting) / token counters and the status
message mean.

## Requirements

### Requirement: Session counting

`apply_event` MUST track the number of live IDE sessions in `state.total`, keyed by
session id in `state._sessions`.

#### Scenario: Session starts
- GIVEN a fresh `BuddyState`
- WHEN a `SessionStart` event arrives with a new session id
- THEN `state.total` is incremented and the id is recorded in `state._sessions`

#### Scenario: Session ends
- GIVEN a `BuddyState` with one tracked session
- WHEN a `SessionEnd` event arrives for that id
- THEN the id is removed from `state._sessions` and `state.total` is decremented (floored at 0)

#### Scenario: UserPromptSubmit without a prior SessionStart
- GIVEN a `BuddyState` with no tracked sessions
- WHEN a `UserPromptSubmit` event arrives for an unknown id
- THEN the session is created and `state.total` is incremented (treated as an implicit start)

### Requirement: Running counter

`state.running` MUST reflect the number of sessions with an in-flight assistant turn.

#### Scenario: Prompt submitted
- GIVEN a tracked session that is not running
- WHEN a `UserPromptSubmit` event arrives for it
- THEN the session is marked running, `state.running` is incremented, and `state.msg` becomes `"thinking…"`

#### Scenario: Turn ends
- GIVEN a tracked session that is running
- WHEN a `Stop` event arrives for it
- THEN the session is marked not-running, `state.running` is decremented (floored at 0), and `state.msg` becomes `"ready"`

### Requirement: Tool activity message

While a tool runs, `state.msg` MUST reflect the tool name so the firmware can map it
to the BUSY state.

#### Scenario: Tool starts
- GIVEN any `BuddyState`
- WHEN a `PreToolUse` event arrives with `tool_name`
- THEN `state.msg` becomes `"running: <tool>"` and an entry is added

#### Scenario: Tool finishes
- GIVEN any `BuddyState`
- WHEN a `PostToolUse` event arrives with `tool_name`
- THEN `state.msg` becomes `"done: <tool>"`

### Requirement: Permission waiting state

When the IDE blocks on a user decision, `apply_event` MUST surface it via
`state.waiting` and `state.prompt` so the firmware can show the ATTENTION state.

#### Scenario: Permission requested
- GIVEN a `BuddyState` with `waiting == 0`
- WHEN a `PermissionRequest` event arrives for a non-`SAFE_TOOLS` tool
- THEN `state.waiting` is set to 1, `state.prompt` is populated with `{id, tool, hint}`, and `state.msg` becomes `"approve: <tool>"`

#### Scenario: Safe tools never block
- GIVEN a `PermissionRequest` event
- WHEN the tool is in `SAFE_TOOLS` (AskUserQuestion, *PlanMode, TodoWrite, Task*)
- THEN `state.waiting` and `state.prompt` are left untouched (asking to approve being asked is a logic loop)

> NOTE: the cc-bridge async `apply_event` path currently has no scenario that
> resets `state.waiting` back to 0 — only the synchronous `hook_permission.py`
> path (`core.py:_handle_wait_permission`) clears it. This gap is tracked by
> change `0001-heartbeat-counter-lifecycle`.

### Requirement: Token accounting

When the IDE reports token usage, the daemon SHALL accumulate it into
`state.tokens` and `state.tokens_today`.

#### Scenario: Cursor reports output tokens
- GIVEN a Cursor `afterAgentResponse` event carrying `output_tokens`
- WHEN `apply_event` (cursor-bridge) processes it
- THEN `state.tokens` and `state.tokens_today` are incremented by that amount

> NOTE: cc-bridge does not currently accumulate tokens — whether Claude Code hook
> events carry token data is an open question tracked by change
> `0001-heartbeat-counter-lifecycle`.
