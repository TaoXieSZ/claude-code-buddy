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
`state.waiting` and `state.prompt` so the firmware can show the ATTENTION state,
and MUST clear them once the session is no longer blocked.

#### Scenario: Permission requested
- GIVEN a `BuddyState` with `waiting == 0`
- WHEN a `PermissionRequest` event arrives for a non-`SAFE_TOOLS` tool
- THEN `state.waiting` is set to 1, `state.prompt` is populated with `{id, tool, hint}`, and `state.msg` becomes `"approve: <tool>"`

#### Scenario: Safe tools never block
- GIVEN a `PermissionRequest` event
- WHEN the tool is in `SAFE_TOOLS` (AskUserQuestion, *PlanMode, TodoWrite, Task*)
- THEN `state.waiting` and `state.prompt` are left untouched

#### Scenario: Waiting clears when the turn ends
- GIVEN a `BuddyState` with `waiting == 1` from a prior `PermissionRequest`
- WHEN a `Stop` event arrives
- THEN `state.waiting` is reset to 0 and `state.prompt` is set to `None`

#### Scenario: Waiting clears when a tool starts
- GIVEN a `BuddyState` with `waiting == 1` from a prior `PermissionRequest`
- WHEN a `PreToolUse` event arrives (the pending permission was granted)
- THEN `state.waiting` is reset to 0 and `state.prompt` is set to `None`

#### Scenario: Waiting clears when a new turn starts
- GIVEN a `BuddyState` with `waiting == 1` from a prior `PermissionRequest`
- WHEN a `UserPromptSubmit` event arrives
- THEN `state.waiting` is reset to 0 and `state.prompt` is set to `None`

### Requirement: Token accounting

When the IDE reports token usage, the daemon SHALL accumulate it into
`state.tokens` and `state.tokens_today`.

#### Scenario: Cursor reports output tokens
- GIVEN a Cursor `afterAgentResponse` event carrying `output_tokens`
- WHEN `apply_event` (cursor-bridge) processes it
- THEN `state.tokens` and `state.tokens_today` are incremented by that amount

#### Scenario: Claude Code provides no token data
- GIVEN the cc-bridge daemon
- WHEN it processes Claude Code hook events
- THEN `state.tokens` is left at 0 — Claude Code's standard hook events carry no
  token-usage field, so cc-bridge has no source to accumulate from. Surfacing a
  live token count for this path needs a separate data source (e.g. transcript
  tailing) and is tracked as future work, not a defect in this mapping.

### Requirement: HUD metrics event

`apply_event` MUST accept a `hud` event carrying live statusline metrics and copy
them onto `BuddyState` so the firmware HUD can display context window usage, real
token counts, rate-limit pressure, the active model, and session elapsed time.

The `hud` event is pure telemetry — it MUST NOT mutate the session / running /
waiting lifecycle counters.

#### Scenario: HUD event populates metric fields
- GIVEN any `BuddyState`
- WHEN a `hud` event arrives with `context_pct`, `tokens`, `limit_5h`, `limit_7d`, `model`, `session_ms`
- THEN each present field is copied onto `state` and `apply_event` returns `True`

#### Scenario: Partial HUD event leaves missing fields untouched
- GIVEN a `BuddyState` with `model` already set
- WHEN a `hud` event arrives that omits `model`
- THEN `state.model` keeps its previous value

#### Scenario: HUD event does not disturb lifecycle counters
- GIVEN a `BuddyState` with `running == 1` and `waiting == 1`
- WHEN a `hud` event arrives
- THEN `state.running` and `state.waiting` are unchanged

### Requirement: HUD metrics in the heartbeat

`BuddyState.to_payload()` MUST include the HUD metric fields on every heartbeat so
the firmware always has the current values (they are state, not one-shot events).

#### Scenario: Heartbeat carries HUD fields
- GIVEN a `BuddyState` with HUD fields populated by a prior `hud` event
- WHEN `to_payload()` is called
- THEN the payload includes `context_pct`, `tokens`, `limit_5h`, `limit_7d`, `model`, `session_ms`

