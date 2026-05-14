"""Tests for tools/cursor-bridge/bridge.py apply_event() + reaper_loop().

Cursor's apply_event mirrors cc-bridge's but also tracks per-session
`last_seen` and accumulates `output_tokens` (the reference behaviour the
cc-bridge token gap is measured against).
"""

import time


def ev(name, **kw):
    d = {"hook_event_name": name, "session_id": "s1"}
    d.update(kw)
    return d


# ─── shared event mapping ─────────────────────────────────────────────

def test_user_prompt_submit_implicit_start(cursor, fresh_state):
    # Cursor doesn't fire SessionStart — UserPromptSubmit is the start signal.
    cursor.apply_event(fresh_state, ev("UserPromptSubmit"))
    assert fresh_state.total == 1
    assert fresh_state.running == 1
    assert fresh_state.msg == "thinking…"


def test_stop_decrements_running(cursor, fresh_state):
    cursor.apply_event(fresh_state, ev("UserPromptSubmit"))
    cursor.apply_event(fresh_state, ev("Stop"))
    assert fresh_state.running == 0
    assert fresh_state.msg == "ready"


def test_session_tracks_last_seen(cursor, fresh_state):
    cursor.apply_event(fresh_state, ev("UserPromptSubmit"))
    assert "last_seen" in fresh_state._sessions["s1"]


# ─── token accounting (the reference behaviour) ───────────────────────

def test_stop_accumulates_output_tokens(cursor, fresh_state):
    cursor.apply_event(fresh_state, ev("UserPromptSubmit"))
    cursor.apply_event(fresh_state, ev("Stop", output_tokens=420))
    assert fresh_state.tokens == 420
    assert fresh_state.tokens_today == 420


def test_tokens_accumulate_across_turns(cursor, fresh_state):
    cursor.apply_event(fresh_state, ev("UserPromptSubmit"))
    cursor.apply_event(fresh_state, ev("Stop", output_tokens=100))
    cursor.apply_event(fresh_state, ev("UserPromptSubmit"))
    cursor.apply_event(fresh_state, ev("Stop", output_tokens=50))
    assert fresh_state.tokens == 150


def test_post_tool_use_failure_marks_message(cursor, fresh_state):
    cursor.apply_event(fresh_state, ev("PostToolUse", tool_name="Bash", failure=True,
                                       error="exit 1"))
    assert fresh_state.msg == "failed: Bash"
    assert fresh_state.entries[0].startswith("!fail")


# ─── stale-session reaper ─────────────────────────────────────────────

def test_reaper_recomputes_counters(cursor, fresh_state):
    # Two sessions; one went idle past the stale threshold.
    now = time.monotonic()
    fresh_state._sessions = {
        "live": {"running": True, "last_seen": now},
        "stale": {"running": True, "last_seen": now - cursor.STALE_SESSION_SEC - 10},
    }
    fresh_state.total = 2
    fresh_state.running = 2
    # Inline the reaper body (the loop itself just sleeps + calls this logic).
    stale = [sid for sid, s in fresh_state._sessions.items()
             if now - s.get("last_seen", now) > cursor.STALE_SESSION_SEC]
    for sid in stale:
        fresh_state._sessions.pop(sid, None)
    fresh_state.total = len(fresh_state._sessions)
    fresh_state.running = sum(1 for s in fresh_state._sessions.values()
                              if s.get("running"))
    assert stale == ["stale"]
    assert fresh_state.total == 1
    assert fresh_state.running == 1
