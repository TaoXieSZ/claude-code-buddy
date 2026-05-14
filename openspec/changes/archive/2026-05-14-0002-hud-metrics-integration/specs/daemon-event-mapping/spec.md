# daemon-event-mapping (delta)

## ADDED Requirements

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
