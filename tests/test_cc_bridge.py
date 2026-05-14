"""Tests for tools/cc-bridge/bridge.py apply_event().

These encode the scenarios in openspec/specs/daemon-event-mapping/spec.md.
"""


def ev(name, **kw):
    d = {"hook_event_name": name, "session_id": "s1"}
    d.update(kw)
    return d


# ─── session counting ─────────────────────────────────────────────────

def test_session_start_increments_total(cc, fresh_state):
    assert cc.apply_event(fresh_state, ev("SessionStart")) is True
    assert fresh_state.total == 1
    assert "s1" in fresh_state._sessions


def test_session_end_decrements_total(cc, fresh_state):
    cc.apply_event(fresh_state, ev("SessionStart"))
    cc.apply_event(fresh_state, ev("SessionEnd"))
    assert fresh_state.total == 0
    assert "s1" not in fresh_state._sessions


def test_user_prompt_without_session_start_is_implicit_start(cc, fresh_state):
    cc.apply_event(fresh_state, ev("UserPromptSubmit"))
    assert fresh_state.total == 1
    assert fresh_state.running == 1


# ─── running counter ──────────────────────────────────────────────────

def test_user_prompt_submit_sets_running_and_msg(cc, fresh_state):
    cc.apply_event(fresh_state, ev("SessionStart"))
    cc.apply_event(fresh_state, ev("UserPromptSubmit"))
    assert fresh_state.running == 1
    assert fresh_state.msg == "thinking…"


def test_stop_decrements_running_and_sets_ready(cc, fresh_state):
    cc.apply_event(fresh_state, ev("SessionStart"))
    cc.apply_event(fresh_state, ev("UserPromptSubmit"))
    cc.apply_event(fresh_state, ev("Stop"))
    assert fresh_state.running == 0
    assert fresh_state.msg == "ready"


def test_running_does_not_double_count_same_session(cc, fresh_state):
    cc.apply_event(fresh_state, ev("SessionStart"))
    cc.apply_event(fresh_state, ev("UserPromptSubmit"))
    cc.apply_event(fresh_state, ev("UserPromptSubmit"))
    assert fresh_state.running == 1


# ─── tool activity message ────────────────────────────────────────────

def test_pre_tool_use_sets_running_message(cc, fresh_state):
    cc.apply_event(fresh_state, ev("PreToolUse", tool_name="Bash"))
    assert fresh_state.msg == "running: Bash"


def test_post_tool_use_sets_done_message(cc, fresh_state):
    cc.apply_event(fresh_state, ev("PostToolUse", tool_name="Read"))
    assert fresh_state.msg == "done: Read"


# ─── permission waiting state ─────────────────────────────────────────

def test_permission_request_sets_waiting_and_prompt(cc, fresh_state):
    cc.apply_event(fresh_state, ev("PermissionRequest", tool_name="Bash",
                                   request_id="r1", message="rm -rf /tmp/x"))
    assert fresh_state.waiting == 1
    assert fresh_state.prompt["tool"] == "Bash"
    assert fresh_state.prompt["id"] == "r1"
    assert fresh_state.msg == "approve: Bash"


def test_safe_tool_permission_does_not_block(cc, fresh_state):
    # AskUserQuestion is in SAFE_TOOLS — gating it is a logic loop.
    cc.apply_event(fresh_state, ev("PermissionRequest", tool_name="AskUserQuestion",
                                   request_id="r2"))
    assert fresh_state.waiting == 0
    assert fresh_state.prompt is None


def test_safe_tools_set_contents(cc):
    # The gate must cover the interactive + planning/state-only tools.
    for t in ("AskUserQuestion", "ExitPlanMode", "TodoWrite", "TaskCreate"):
        assert t in cc.SAFE_TOOLS


# ─── "waiting for your input" notification (the merged BUSY-msg fix) ──

def test_waiting_for_input_notification_clears_running(cc, fresh_state):
    # A turn is in flight, then Claude Code fires the idle Notification.
    cc.apply_event(fresh_state, ev("SessionStart"))
    cc.apply_event(fresh_state, ev("UserPromptSubmit"))
    assert fresh_state.running == 1
    cc.apply_event(fresh_state, ev("Notification",
                                   message="Claude is waiting for your input"))
    # Semantically a turn-end — running must drop so the firmware maps IDLE,
    # not BUSY, behind the "waiting for your input" message.
    assert fresh_state.running == 0
    assert fresh_state.msg == "Claude is waiting for your input"


# ─── waiting clears when the turn progresses (Change 0001) ───────────

def _block_on_permission(cc, state):
    cc.apply_event(state, ev("PermissionRequest", tool_name="Bash", request_id="r1"))
    assert state.waiting == 1 and state.prompt is not None


def test_waiting_clears_on_stop(cc, fresh_state):
    cc.apply_event(fresh_state, ev("SessionStart"))
    cc.apply_event(fresh_state, ev("UserPromptSubmit"))
    _block_on_permission(cc, fresh_state)
    cc.apply_event(fresh_state, ev("Stop"))
    assert fresh_state.waiting == 0
    assert fresh_state.prompt is None


def test_waiting_clears_on_pre_tool_use(cc, fresh_state):
    # A tool starting means the pending permission was granted.
    _block_on_permission(cc, fresh_state)
    cc.apply_event(fresh_state, ev("PreToolUse", tool_name="Bash"))
    assert fresh_state.waiting == 0
    assert fresh_state.prompt is None


def test_waiting_clears_on_new_user_prompt(cc, fresh_state):
    _block_on_permission(cc, fresh_state)
    cc.apply_event(fresh_state, ev("UserPromptSubmit"))
    assert fresh_state.waiting == 0
    assert fresh_state.prompt is None


# ─── post-compact ─────────────────────────────────────────────────────

def test_post_compact_adds_entry(cc, fresh_state):
    cc.apply_event(fresh_state, ev("PostCompact"))
    assert fresh_state.entries[0] == "compacted"
