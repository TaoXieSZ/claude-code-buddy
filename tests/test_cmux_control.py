"""Unit tests for the cmux routing core (tools/control_plane/cmux_control.py).

Sessions are cmux *surfaces* (terminal panes). Pure building (filter + numbering),
number→UUID resolution, and argv building are exercised with a mock runner — no
real cmux needed. JSON samples mirror the real `cmux rpc workspace.list` and
`cmux rpc surface.list` shapes observed on the dev machine.
"""

import json

import pytest

from control_plane import cmux_control as cc
from control_plane.cmux_control import (
    BOARD_MARKER,
    NATO,
    CmuxClient,
    Session,
    build_sessions,
    label_from_title,
    resolve,
    resolve_target,
)


@pytest.fixture(autouse=True)
def _isolate_nickname_registry(tmp_path, monkeypatch):
    """Route every NicknameRegistry write to a tmp file so tests don't touch ~/.cache."""
    monkeypatch.setattr(cc, "NICKNAMES_PATH", tmp_path / "nicknames.json")
    yield


class FakeRegistry:
    """In-memory nickname registry — no file I/O for tests.

    Mirrors NicknameRegistry's `assign()` / `resolve()` so build_sessions can
    operate without touching the real ~/.cache file.
    """
    def __init__(self):
        self._m: dict[str, str] = {}

    def assign(self, sid: str) -> str:
        if sid in self._m:
            return self._m[sid]
        used = set(self._m.values())
        for n in NATO:
            if n not in used:
                self._m[sid] = n
                return n
        raise RuntimeError("exhausted in test")

    def resolve(self, t: str):
        t = t.lower().strip()
        for sid, nick in self._m.items():
            if nick == t:
                return sid
        return None

    def get(self, sid: str):
        return self._m.get(sid)


def _build(ws=None, surf=None):
    """build_sessions with a fresh FakeRegistry — common test boilerplate."""
    return build_sessions(ws or WS, surf or SURF, registry=FakeRegistry())

# Two workspaces; the first holds the board pane, a Claude Code pane, and the
# voice-agent browser; the second holds a plain shell.
WS = json.dumps(
    {
        "workspaces": [
            {"id": "WSA", "ref": "workspace:1", "index": 0, "selected": True,
             "current_directory": "/Users/txie/proj/a"},
            {"id": "WSB", "ref": "workspace:2", "index": 1, "selected": False,
             "current_directory": "/tmp"},
        ]
    }
)
SURF = {
    "WSA": json.dumps({"surfaces": [
        {"id": "SB", "ref": "surface:20", "index": 0, "type": "terminal",
         "title": "cd '/x/tools' && python3 -m control_plane.board --watch",
         "focused": False},
        {"id": "S1", "ref": "surface:22", "index": 1, "type": "terminal",
         "title": "claude-desktop-buddy · 229a873b", "focused": True,
         "resume_binding": {"kind": "claude", "checkpoint_id": "CKPT-S1"}},
        {"id": "SBR", "ref": "surface:21", "index": 2, "type": "browser",
         "title": "Talk to your voice agent | Agora", "focused": False},
    ]}),
    "WSB": json.dumps({"surfaces": [
        {"id": "S2", "ref": "surface:30", "index": 0, "type": "terminal",
         "title": "txie@host:/tmp", "focused": False},
    ]}),
}


# ─── build_sessions ────────────────────────────────────────────────────

def test_build_numbers_terminals_across_workspaces():
    s = _build()
    assert [x.number for x in s] == [1, 2]
    assert [x.surface for x in s] == ["S1", "S2"]


def test_build_excludes_board_and_browser():
    surfaces = {x.surface for x in _build()}
    assert "SB" not in surfaces   # board pane (BOARD_MARKER in title)
    assert "SBR" not in surfaces  # browser surface (voice agent)
    assert BOARD_MARKER == "control_plane.board"


def test_build_carries_workspace_cwd_and_focus():
    s = _build()
    assert s[0].cwd == "/Users/txie/proj/a"  # owning workspace's dir
    assert s[0].workspace == "WSA"
    assert s[0].focused is True and s[0].selected is True  # selected aliases focused
    assert s[1].cwd == "/tmp"
    assert s[1].focused is False


def test_focus_only_in_selected_workspace():
    # A pane can be `focused` within its own (background) workspace; only the
    # one in the SELECTED workspace is the globally active pane.
    ws = json.dumps({"workspaces": [
        {"id": "SEL", "index": 0, "selected": True, "current_directory": "/s"},
        {"id": "BG", "index": 1, "selected": False, "current_directory": "/b"},
    ]})
    surf = {
        "SEL": json.dumps({"surfaces": [
            {"id": "sel1", "index": 0, "type": "terminal", "title": "a", "focused": True}]}),
        "BG": json.dumps({"surfaces": [
            {"id": "bg1", "index": 0, "type": "terminal", "title": "b", "focused": True}]}),
    }
    s = _build(ws, surf)
    assert [(x.surface, x.focused) for x in s] == [("sel1", True), ("bg1", False)]


def test_build_orders_by_index_not_array_order():
    ws = json.dumps({"workspaces": [
        {"id": "B", "index": 1, "current_directory": "/b"},
        {"id": "A", "index": 0, "current_directory": "/a"},
    ]})
    surf = {
        "A": json.dumps({"surfaces": [
            {"id": "a1", "index": 0, "type": "terminal", "title": "a"}]}),
        "B": json.dumps({"surfaces": [
            {"id": "b1", "index": 0, "type": "terminal", "title": "b"}]}),
    }
    s = _build(ws, surf)
    assert [(x.number, x.surface, x.cwd) for x in s] == [(1, "a1", "/a"), (2, "b1", "/b")]


def test_build_empty():
    assert _build(json.dumps({"workspaces": []}), {}) == []


def test_build_extracts_claude_checkpoint_id():
    # A claude pane exposes its session_id via resume_binding.checkpoint_id;
    # a plain shell (no resume_binding) gets "".
    by_surf = {x.surface: x.checkpoint_id for x in _build()}
    assert by_surf["S1"] == "CKPT-S1"
    assert by_surf["S2"] == ""


def test_build_ignores_checkpoint_for_non_claude_binding():
    ws = json.dumps({"workspaces": [
        {"id": "W", "index": 0, "selected": True, "current_directory": "/x"}]})
    surf = {"W": json.dumps({"surfaces": [
        {"id": "sh", "index": 0, "type": "terminal", "title": "shell",
         "resume_binding": {"kind": "shell", "checkpoint_id": "NOPE"}}]})}
    assert _build(ws, surf)[0].checkpoint_id == ""


# ─── nicknames ─────────────────────────────────────────────────────────

def test_nicknames_assigned_in_order():
    s = _build()
    assert [x.nickname for x in s] == ["alpha", "bravo"]


def test_nicknames_stable_across_calls_with_same_registry():
    reg = FakeRegistry()
    s1 = build_sessions(WS, SURF, registry=reg)
    s2 = build_sessions(WS, SURF, registry=reg)
    assert [x.nickname for x in s1] == [x.nickname for x in s2]


def test_nicknames_dont_recycle_when_pane_disappears():
    reg = FakeRegistry()
    build_sessions(WS, SURF, registry=reg)  # assigns alpha→S1, bravo→S2
    # now S1 disappears (only S2 remains in cmux); a NEW pane appears as S3
    ws2 = json.dumps({"workspaces": [
        {"id": "WSB", "index": 0, "selected": True, "current_directory": "/tmp"}]})
    surf2 = {"WSB": json.dumps({"surfaces": [
        {"id": "S2", "index": 0, "type": "terminal", "title": "old"},
        {"id": "S3", "index": 1, "type": "terminal", "title": "new"}]})}
    s = build_sessions(ws2, surf2, registry=reg)
    by_surf = {x.surface: x.nickname for x in s}
    assert by_surf["S2"] == "bravo"     # kept its name
    assert by_surf["S3"] == "charlie"   # next free, NOT alpha (which is retired)


def test_resolve_target_by_nickname_exact():
    s = _build()
    assert resolve_target("alpha", s) == "S1"
    assert resolve_target("BRAVO", s) == "S2"  # case-insensitive


def test_resolve_target_by_unambiguous_prefix():
    s = _build()
    assert resolve_target("alph", s) == "S1"
    assert resolve_target("b", s) == "S2"


def test_resolve_target_legacy_number_string():
    s = _build()
    assert resolve_target("1", s) == "S1"
    assert resolve_target(2, s) == "S2"


def test_resolve_target_unknown_returns_none():
    s = _build()
    assert resolve_target("zulu", s) is None
    assert resolve_target("", s) is None


# ─── resolve ───────────────────────────────────────────────────────────

def test_resolve_hit():
    assert resolve(2, _build()) == "S2"


def test_resolve_miss():
    assert resolve(9, _build()) is None


# ─── CmuxClient with mock runner ───────────────────────────────────────

class MockRunner:
    """Answers workspace.list / surface.list / surface.read_text; records argv."""

    def __init__(self, screen=""):
        self.calls = []
        self.screen = screen

    def __call__(self, argv):
        self.calls.append(list(argv))
        method = argv[2] if len(argv) > 2 and argv[1] == "rpc" else ""
        if method == "window.list":
            # Single window in the fake fleet (SURF/WS span one window).
            return 0, json.dumps({"windows": [{"id": "W1"}]}), ""
        if method == "workspace.list":
            return 0, WS, ""
        if method == "surface.list":
            ws_id = json.loads(argv[3]).get("workspace_id")
            return 0, SURF.get(ws_id, '{"surfaces": []}'), ""
        if method == "surface.read_text":
            return 0, json.dumps({"text": self.screen}), ""
        return 0, "", ""  # focus / send_text / send_key


def _method_call(calls, method):
    return next(c for c in calls if len(c) > 2 and c[1] == "rpc" and c[2] == method)


def test_route_builds_surface_argv_focus_send_enter():
    m = MockRunner()
    c = CmuxClient(binary="CMUX", runner=m)
    surface = c.route(1, "run the tests")
    assert surface == "S1"
    assert _method_call(m.calls, "surface.focus") == \
        ["CMUX", "rpc", "surface.focus", json.dumps({"surface_id": "S1"})]
    assert _method_call(m.calls, "surface.send_text") == \
        ["CMUX", "rpc", "surface.send_text",
         json.dumps({"surface_id": "S1", "text": "run the tests"})]
    assert _method_call(m.calls, "surface.send_key") == \
        ["CMUX", "rpc", "surface.send_key",
         json.dumps({"surface_id": "S1", "key": "Enter"})]


def test_route_unknown_number_raises_and_sends_nothing():
    m = MockRunner()
    c = CmuxClient(binary="CMUX", runner=m)
    try:
        c.route(9, "boom")
        assert False, "expected KeyError"
    except KeyError:
        pass
    assert all(not (len(call) > 2 and call[2] == "surface.send_text") for call in m.calls)


def test_route_verbatim_text_not_rewritten():
    m = MockRunner()
    c = CmuxClient(binary="CMUX", runner=m)
    payload = "git commit -m \"fix: 修复 bug\" && echo done"
    c.route(2, payload)
    send = _method_call(m.calls, "surface.send_text")
    assert json.loads(send[3])["text"] == payload  # exact, including CJK + quotes


def test_list_sessions_spans_multiple_windows():
    """workspace.list defaults to caller's window; we fan out via window.list
    so panes in OTHER cmux windows are still enumerable."""

    class MultiWindow:
        def __init__(self):
            self.calls = []

        def __call__(self, argv):
            self.calls.append(list(argv))
            m = argv[2] if len(argv) > 2 and argv[1] == "rpc" else ""
            if m == "window.list":
                return 0, json.dumps({"windows": [{"id": "WA"}, {"id": "WB"}]}), ""
            if m == "workspace.list":
                wid = json.loads(argv[3]).get("window_id")
                if wid == "WA":
                    return 0, json.dumps({"workspaces": [
                        {"id": "WSA", "index": 0, "selected": True,
                         "current_directory": "/a"}]}), ""
                if wid == "WB":
                    return 0, json.dumps({"workspaces": [
                        {"id": "WSB", "index": 0, "selected": True,
                         "current_directory": "/b"}]}), ""
                return 0, json.dumps({"workspaces": []}), ""
            if m == "surface.list":
                ws = json.loads(argv[3]).get("workspace_id")
                payload = {"WSA": [{"id": "a1", "type": "terminal", "title": "a", "index": 0}],
                           "WSB": [{"id": "b1", "type": "terminal", "title": "b", "index": 0}]}
                return 0, json.dumps({"surfaces": payload.get(ws, [])}), ""
            return 0, "", ""

    c = CmuxClient(binary="CMUX", runner=MultiWindow())
    sessions = c.list_sessions()
    surfaces = [s.surface for s in sessions]
    assert "a1" in surfaces and "b1" in surfaces  # both windows visible
    assert len(sessions) == 2


def test_focus_by_checkpoint_focuses_matching_surface():
    m = MockRunner()
    c = CmuxClient(binary="CMUX", runner=m)
    assert c.focus_by_checkpoint("CKPT-S1") == "S1"
    assert _method_call(m.calls, "surface.focus") == \
        ["CMUX", "rpc", "surface.focus", json.dumps({"surface_id": "S1"})]


def test_focus_by_checkpoint_raises_cmux_app():
    # After focusing the pane, the cmux app is raised to the macOS foreground
    # so the switch is visible even when another app was frontmost.
    m = MockRunner()
    c = CmuxClient(binary="CMUX", runner=m)  # no "/Contents/" → open -a fallback
    assert c.focus_by_checkpoint("CKPT-S1") == "S1"
    assert ["open", "-a", "cmux"] in m.calls


def test_app_bundle_derived_from_binary_path():
    c = CmuxClient(binary="/Applications/cmux.app/Contents/Resources/bin/cmux")
    assert c._app_bundle() == "/Applications/cmux.app"


def test_focus_by_checkpoint_unknown_returns_none_and_no_focus():
    m = MockRunner()
    c = CmuxClient(binary="CMUX", runner=m)
    assert c.focus_by_checkpoint("no-such-session") is None
    assert all(not (len(call) > 2 and call[2] == "surface.focus") for call in m.calls)


def test_focus_by_checkpoint_empty_id_makes_no_cmux_calls():
    m = MockRunner()
    c = CmuxClient(binary="CMUX", runner=m)
    assert c.focus_by_checkpoint("") is None
    assert m.calls == []  # short-circuits before any subprocess


def test_label_from_title_pure_autoname():
    # auto-name generated → title is a single pure name.
    assert label_from_title("hardware-buddies-setup") == "hardware-buddies-setup"


def test_label_from_title_repo_prompt_sid():
    # before auto-name: "<repo> · <prompt> · <sid>" → take the middle part.
    assert label_from_title(
        "hardware-buddies · Please analyze this · 41af42bb-fb94") == "Please analyze this"


def test_label_from_title_empty():
    assert label_from_title("") == ""
    assert label_from_title("   ") == ""


def test_session_labels_maps_checkpoint_to_label():
    # Only S1 carries a claude checkpoint_id (CKPT-S1); its title's middle
    # segment is the label.
    m = MockRunner()
    c = CmuxClient(binary="CMUX", runner=m)
    assert c.session_labels() == {"CKPT-S1": "229a873b"}


def test_parse_pending_questions_extracts_fields():
    # feed.list (rpc) shape: top-level request_id / question_* ; header in questions[0].
    from control_plane.cmux_control import parse_pending_questions
    feed = json.dumps({"items": [
        {"kind": "question", "status": "pending", "workstreamId": "claude-SID1",
         "request_id": "RID1", "question_prompt": "pick one", "question_multi_select": False,
         "question_options": [{"id": "opt0", "label": "A", "description": "d"},
                              {"id": "opt1", "label": "B"}],
         "questions": [{"header": "H", "prompt": "pick one", "multi_select": False,
                        "options": [{"id": "opt0", "label": "A"}, {"id": "opt1", "label": "B"}]}]},
        {"kind": "toolUse", "status": {"telemetry": {}}},
    ]})
    qs = parse_pending_questions(feed)
    assert len(qs) == 1
    q = qs[0]
    assert q["rid"] == "RID1" and q["header"] == "H" and q["prompt"] == "pick one"
    assert q["multi"] is False and q["sid"] == "SID1"
    assert q["options"] == [{"id": "opt0", "label": "A"}, {"id": "opt1", "label": "B"}]


def test_parse_pending_questions_skips_expired_and_telemetry():
    from control_plane.cmux_control import parse_pending_questions
    feed = json.dumps({"items": [
        {"kind": "question", "status": "expired", "request_id": "X",
         "question_options": [{"id": "o", "label": "l"}]},
        {"kind": "question", "status": {"telemetry": {}}, "request_id": "Y",
         "question_options": [{"id": "o", "label": "l"}]},
    ]})
    assert parse_pending_questions(feed) == []


def _epoch(iso: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def test_parse_pending_questions_drops_stale_zombie():
    # cmux leaves a natively-answered question at status='pending' forever
    # (updated_at == created_at). Age-gate drops it so the buddy firmware
    # doesn't auto-approve every permission on a zombie pending question.
    from control_plane.cmux_control import parse_pending_questions
    feed = json.dumps({"items": [
        {"kind": "question", "status": "pending", "request_id": "OLD",
         "created_at": "2026-06-24T05:25:06Z",
         "question_options": [{"id": "o", "label": "l"}]},
    ]})
    one_hour_later = _epoch("2026-06-24T05:25:06Z") + 3600
    assert parse_pending_questions(feed, now=one_hour_later) == []


def test_parse_pending_questions_keeps_fresh_and_reads_snake_case_sid():
    from control_plane.cmux_control import parse_pending_questions
    feed = json.dumps({"items": [
        {"kind": "question", "status": "pending", "request_id": "FRESH",
         "created_at": "2026-06-24T05:25:06Z", "workstream_id": "claude-SID9",
         "question_options": [{"id": "o", "label": "l"}]},
    ]})
    five_s_later = _epoch("2026-06-24T05:25:06Z") + 5
    qs = parse_pending_questions(feed, now=five_s_later)
    assert len(qs) == 1 and qs[0]["rid"] == "FRESH"
    assert qs[0]["sid"] == "SID9"  # snake_case workstream_id resolved


def test_answer_question_builds_reply_argv():
    m = MockRunner()
    c = CmuxClient(binary="CMUX", runner=m)
    assert c.answer_question("RID1", ["A"]) is True
    assert _method_call(m.calls, "feed.question.reply") == \
        ["CMUX", "rpc", "feed.question.reply",
         json.dumps({"request_id": "RID1", "selections": ["A"]})]


def test_answer_question_empty_args_no_call():
    m = MockRunner()
    c = CmuxClient(binary="CMUX", runner=m)
    assert c.answer_question("", ["A"]) is False
    assert c.answer_question("RID", []) is False
    assert all(c[2] != "feed.question.reply" for c in m.calls if len(c) > 2)


def test_read_status_returns_last_nonempty_line():
    m = MockRunner(screen="line one\n\n  last meaningful line  \n\n")
    c = CmuxClient(binary="CMUX", runner=m)
    assert c.read_status(1) == "last meaningful line"


def test_read_surface_empty_on_blank():
    m = MockRunner(screen="   \n\n")
    c = CmuxClient(binary="CMUX", runner=m)
    assert c.read_surface("S1") == ""


# ─── board.build_board ─────────────────────────────────────────────────

def test_build_board_rows_from_client():
    from control_plane.board import build_board

    class FakeClient:
        def list_sessions(self):
            return _build()

        def read_surface(self, surface):
            return {"S1": "$ npm test", "S2": "ready"}.get(surface, "")

    rows = build_board(FakeClient())
    assert [r["number"] for r in rows] == [1, 2]
    assert rows[0]["status"] == "$ npm test"
    assert rows[0]["selected"] is True
    assert rows[0]["title"] == "claude-desktop-buddy · 229a873b"
    assert rows[1]["cwd"] == "/tmp"


def test_build_board_tolerates_read_error():
    from control_plane.board import build_board

    class FlakyClient:
        def list_sessions(self):
            return _build()

        def read_surface(self, surface):
            raise RuntimeError("boom")

    rows = build_board(FlakyClient())
    assert all(r["status"] == "" for r in rows)  # errors degrade to empty
