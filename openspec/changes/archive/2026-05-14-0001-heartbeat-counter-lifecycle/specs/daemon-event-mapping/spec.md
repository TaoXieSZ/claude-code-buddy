# daemon-event-mapping (delta)

## MODIFIED Requirements

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
