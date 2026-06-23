"""cmux control: enumerate terminal *surfaces* (panes) and route verbatim text
into a chosen one.

Backs the voice control-plane secretary. A "session" here is a cmux **surface**
(a terminal pane), NOT a workspace — coding agents commonly run as splits inside
one workspace, so surface-level addressing is what matches real usage. (cmux's
fundamental unit is the surface; a workspace is just a tab holding panes.)

All cmux interaction goes through an injectable `runner`, so the pure logic
(filtering, numbering, argv building) is host-testable and the subprocess is
mockable.

Targeting binds to the stable **surface UUID** (`id`), never the positional
`surface:N` ref (refs shift as panes open/close).

The board's own pane (running `control_plane.board`) and browser surfaces (the
voice agent) are excluded by a deterministic filter, so the board and the daemon
compute the SAME numbering independently.

Session metadata note: cmux exposes `current_directory` per workspace but NOT
per surface, so `cwd` is the owning workspace's directory; the surface `title`
(repo·id for a Claude Code pane, user@host:~/path for a shell) disambiguates.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

DEFAULT_CMUX = "/Applications/cmux.app/Contents/Resources/bin/cmux"

# A surface whose title contains this is the board itself — never a target.
# Kept as a fallback for boards that haven't registered (e.g. legacy panes);
# the primary exclusion is the registry under BOARD_REGISTRY_DIR (cmux
# overwrites surface titles based on rendered content, so the title check
# alone is fragile once the watcher starts emitting frames).
BOARD_MARKER = "control_plane.board"

# Live registry of board panes (one empty file per surface UUID). Each
# `--watch` process touches a file here on start and removes it on exit
# (board.register_self_as_board). The enumerator excludes anything in this
# directory.
import os as _os
from pathlib import Path as _Path
BOARD_REGISTRY_DIR = _Path(
    _os.environ.get("CONTROL_PLANE_BOARD_REGISTRY")
    or (_Path.home() / ".cache" / "control-plane" / "board-surfaces")
)


def registered_board_surfaces() -> set[str]:
    """Return the set of surface UUIDs registered as live board panes."""
    try:
        return {p.name for p in BOARD_REGISTRY_DIR.iterdir() if p.is_file()}
    except FileNotFoundError:
        return set()
    except OSError:
        return set()


# ─── nickname registry ────────────────────────────────────────────────
#
# The user addresses ships by NATO phonetic nickname (alpha, bravo, …), NOT
# positional number, because numbers shift as panes open/close. Nicknames are
# keyed by stable surface UUID and persisted in `nicknames.json`, so a ship's
# name is forever once assigned. Closed-pane entries linger in the file —
# nicknames are NEVER recycled within the same registry, which prevents a
# new pane from being addressed by a stale name a future user might still
# have in mind.

NATO = (
    "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel",
    "india", "juliet", "kilo", "lima", "mike", "november", "oscar", "papa",
    "quebec", "romeo", "sierra", "tango", "uniform", "victor", "whiskey",
    "xray", "yankee", "zulu",
)

NICKNAMES_PATH = _Path(
    _os.environ.get("CONTROL_PLANE_NICKNAMES_PATH")
    or (_Path.home() / ".cache" / "control-plane" / "nicknames.json")
)


class NicknameRegistry:
    """Persistent surface_uuid → nickname mapping.

    `assign(surface_id)` returns the existing name or hands out the next-free
    NATO phonetic name; falls back to `ship-N` if the alphabet is exhausted.
    Writes are atomic (tmp + rename). `resolve(target)` does the inverse:
    nickname → surface_id, with case-insensitive prefix match.
    """

    def __init__(self, path: "_Path" = NICKNAMES_PATH):
        self._path = path
        self._mapping: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        try:
            self._mapping = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(self._mapping, dict):
                self._mapping = {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self._mapping = {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self._mapping, indent=2, sort_keys=True, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(self._path)
        except OSError:
            pass

    def get(self, surface_id: str) -> Optional[str]:
        return self._mapping.get(surface_id)

    def all(self) -> dict[str, str]:
        return dict(self._mapping)

    def assign(self, surface_id: str) -> str:
        """Return the existing nickname for `surface_id`, or assign a new one."""
        if surface_id in self._mapping:
            return self._mapping[surface_id]
        used = set(self._mapping.values())
        chosen = next((n for n in NATO if n not in used), None)
        if chosen is None:
            i = 1
            while f"ship-{i}" in used:
                i += 1
            chosen = f"ship-{i}"
        self._mapping[surface_id] = chosen
        self._save()
        return chosen

    def resolve(self, target: str) -> Optional[str]:
        """Inverse: nickname → surface_id (case-insensitive, exact or unambiguous prefix)."""
        t = (target or "").strip().lower()
        if not t:
            return None
        for sid, nick in self._mapping.items():
            if nick == t:
                return sid
        matches = [sid for sid, nick in self._mapping.items() if nick.startswith(t)]
        return matches[0] if len(matches) == 1 else None

# A Runner runs an argv and returns (returncode, stdout, stderr).
Runner = Callable[[Sequence[str]], "tuple[int, str, str]"]


@dataclass(frozen=True)
class Session:
    number: int        # 1-based positional, used for layout/sorting; NOT user-facing
    nickname: str      # stable NATO phonetic name (alpha/bravo/…), the user-facing id
    surface: str       # stable surface UUID — ALL routing actually targets this
    workspace: str     # owning workspace UUID
    title: str         # terminal title (repo·id for Claude Code, user@host:~/p for a shell)
    cwd: str           # owning workspace current_directory (cmux has no per-pane cwd)
    focused: bool      # the currently focused pane (globally — see build_sessions)
    checkpoint_id: str = ""  # Claude session_id (= resume_binding.checkpoint_id for a
                             # claude agent pane); "" for shells / non-claude surfaces.

    # Back-compat alias for board.build_board(), which reads `.selected`.
    @property
    def selected(self) -> bool:
        return self.focused


def build_sessions(
    workspaces_json: str,
    surfaces_by_ws: dict[str, str],
    excluded_surfaces: Optional[set[str]] = None,
    registry: Optional[NicknameRegistry] = None,
) -> list[Session]:
    """Build the ordered session list (pure) from workspace + surface JSON.

    `surfaces_by_ws` maps a workspace UUID to that workspace's
    `cmux rpc surface.list` JSON. Terminal surfaces are numbered 1-based across
    all workspaces in (workspace index, surface index) order, AND each gets a
    stable NATO-phonetic nickname (alpha/bravo/…) from the supplied
    `registry`. Browser surfaces, explicitly excluded surfaces (e.g. live
    board panes), and surfaces whose title still matches the legacy
    `BOARD_MARKER` are skipped.
    """
    excluded = excluded_surfaces or set()
    if registry is None:
        registry = NicknameRegistry()
    workspaces = json.loads(workspaces_json).get("workspaces", [])
    workspaces = sorted(workspaces, key=lambda w: w.get("index", 0))
    sessions: list[Session] = []
    n = 0
    for w in workspaces:
        ws_id = w.get("id", "")
        cwd = w.get("current_directory") or ""
        ws_selected = bool(w.get("selected"))  # only the active tab carries the focus
        raw = surfaces_by_ws.get(ws_id)
        if not raw:
            continue
        surfaces = json.loads(raw).get("surfaces", [])
        surfaces = sorted(surfaces, key=lambda s: s.get("index", 0))
        for s in surfaces:
            if s.get("type") != "terminal":
                continue
            sid = s.get("id", "")
            if sid in excluded:
                continue
            title = s.get("title") or ""
            if BOARD_MARKER in title:
                continue
            n += 1
            sid = s.get("id", "")
            nickname = registry.assign(sid) if sid else ""
            # A claude agent pane carries its Claude session_id under
            # resume_binding.checkpoint_id (cmux's --resume key). Only trust it
            # when kind == "claude"; shells / other agents get "".
            rb = s.get("resume_binding") or {}
            checkpoint_id = rb.get("checkpoint_id", "") if rb.get("kind") == "claude" else ""
            sessions.append(
                Session(
                    number=n,
                    nickname=nickname,
                    surface=sid,
                    workspace=ws_id,
                    title=title,
                    cwd=cwd,
                    # `focused` is per-workspace; the globally active pane is the
                    # focused surface inside the *selected* workspace.
                    focused=bool(s.get("focused")) and ws_selected,
                    checkpoint_id=checkpoint_id,
                )
            )
    return sessions


def resolve(number: int, sessions: Sequence[Session]) -> Optional[str]:
    """Return the surface UUID for the given 1-based number, or None.

    Kept for back-compat. New code should use `resolve_target`, which accepts
    a nickname string (alpha/bravo/…) and falls back to numeric strings.
    """
    for s in sessions:
        if s.number == number:
            return s.surface
    return None


def resolve_target(target, sessions: Sequence[Session]) -> Optional[str]:
    """Surface UUID for `target`: nickname (str), legacy number (int or digit str).

    Matches case-insensitively against the LIVE sessions' nicknames, with
    exact match preferred over unambiguous-prefix. Digit strings ("2") and
    bare ints route to the legacy positional number for back-compat.
    """
    if isinstance(target, int):
        return resolve(target, sessions)
    t = str(target).strip()
    if not t:
        return None
    if t.isdigit():
        return resolve(int(t), sessions)
    tl = t.lower()
    for s in sessions:
        if s.nickname == tl:
            return s.surface
    matches = [s for s in sessions if s.nickname.startswith(tl)]
    return matches[0].surface if len(matches) == 1 else None


def label_from_title(title: str) -> str:
    """cmux surface title → short human label for a session list.

    cmux's auto-name hook rewrites a surface title to a pure LLM name once it
    runs (e.g. "hardware-buddies-setup"); before that the title is
    "<repo> · <prompt> · <sid-tail>". We want the most identifying middle part:
    the pure name when there's one segment, else the second segment (the
    prompt / interim name), dropping the leading repo and trailing sid tail.
    """
    t = (title or "").strip()
    if not t:
        return ""
    parts = [p.strip() for p in t.split(" · ") if p.strip()]
    if not parts:
        return ""
    return parts[1] if len(parts) >= 2 else parts[0]


def _last_nonempty(text: str) -> str:
    nonempty = [ln for ln in text.splitlines() if ln.strip()]
    return nonempty[-1].strip() if nonempty else ""


# Noise lines a board-status reader should skip — Claude Code's persistent
# bottom banners, the OMC HUD, the cmux/shell horizontal-rule separators, and
# bare prompt characters that carry no content. The smart-status walker
# returns the most recent line that ISN'T noise, so the board shows what the
# pane is actually doing (a recap, a response, a prompt, an activity verb)
# instead of the fixed banner.
import re as _re
_NOISE_PATTERNS = [
    _re.compile(r"⏵⏵\s*bypass\s*permissions"),
    _re.compile(r"←\s*for\s*agents"),
    _re.compile(r"^\[OMC#"),
    _re.compile(r"^[─━═╌╍┄┅\-]{8,}$"),       # horizontal rules
    _re.compile(r"^[❯>]\xa0?$"),               # bare prompt char only
]
# Claude Code's signal glyphs. We split them into "live" and "recap" so the
# board prefers what the session is actually DOING now over Claude's static
# summary (which Claude Code rewrites on idle and so always wins a naive
# "most recent priority line" walk):
#   live:  ✻ spinner verb · ⏺ response · ❯ prompt
#   recap: ※ (fallback only — same head treated separately)
_LIVE_HEADS = ("✻", "⏺", "❯", ">")
_RECAP_HEAD = "※"
_ALL_HEADS = _LIVE_HEADS + (_RECAP_HEAD,)

# A glyph line needs SOME content after the glyph to be useful — cmux often
# captures a half-rendered spinner frame like "✻ C" while text is animating.
# Below this many post-glyph chars, treat the line as noise.
_MIN_GLYPH_CONTENT = 4


@dataclass(frozen=True)
class PaneDetails:
    """Multi-signal extract from a pane — what to put on a board card."""
    activity: str = ""   # most recent ✻ verb (live thinking)
    response: str = ""   # most recent ⏺ assistant response
    prompt: str = ""     # most recent ❯ user prompt
    recap: str = ""      # most recent ※ idle summary
    hud: str = ""        # OMC HUD reformatted compactly (e.g. "ctx 5% · 5h 24% · sn 30m"); "" if absent


_HUD_CTX_RE   = _re.compile(r"ctx:(\d+%)")
_HUD_5H_RE    = _re.compile(r"5h:(\d+%)")
_HUD_WK_RE    = _re.compile(r"wk:(\d+%)")
_HUD_SESS_RE  = _re.compile(r"session:(\d+)m")


def _parse_hud(line: str) -> str:
    """Reformat an OMC HUD line into a compact dim-grey-friendly summary.

    Raw:  `[OMC#4.13.4] | 5h:24%(2h48m) wk:5%(0h18m) sn:0% | session:1069m | ctx:5%`
    Out:  `ctx 5% · 5h 24% · wk 5% · sn 17.8h`
    Empty if `line` isn't a recognisable HUD line.
    """
    if not line.lstrip().startswith("[OMC#"):
        return ""
    parts: list[str] = []
    if (m := _HUD_CTX_RE.search(line)):
        parts.append(f"ctx {m.group(1)}")
    if (m := _HUD_5H_RE.search(line)):
        parts.append(f"5h {m.group(1)}")
    if (m := _HUD_WK_RE.search(line)):
        parts.append(f"wk {m.group(1)}")
    if (m := _HUD_SESS_RE.search(line)):
        mins = int(m.group(1))
        parts.append(f"sn {mins / 60:.1f}h" if mins >= 60 else f"sn {mins}m")
    return " · ".join(parts)


def _extract_details(text: str, scan_lines: int = 80) -> PaneDetails:
    """Pull the most recent live signal of each kind from a pane's tail.

    Walks reverse, filling each field once with the most recent qualifying
    line. OMC HUD is read from `[OMC#…]` lines (otherwise noise) and
    reformatted via `_parse_hud`.
    """
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return PaneDetails()
    tail = lines[-scan_lines:]
    bag = {"activity": "", "response": "", "prompt": "", "recap": "", "hud": ""}
    for ln in reversed(tail):
        s = ln.strip()
        # HUD is technically noise to _smart_status but useful here — check first.
        if not bag["hud"] and s.lstrip().startswith("[OMC#"):
            hud = _parse_hud(s)
            if hud:
                bag["hud"] = hud
            continue
        # Skip banner / separator / half-rendered spinner noise.
        if any(p.search(s) for p in _NOISE_PATTERNS):
            continue
        head = s[:1]
        if head in _ALL_HEADS and len(s[1:].lstrip()) < _MIN_GLYPH_CONTENT:
            continue
        if head == "✻" and not bag["activity"]:
            bag["activity"] = s
        elif head == "⏺" and not bag["response"]:
            bag["response"] = s
        elif head in ("❯", ">") and not bag["prompt"]:
            bag["prompt"] = s
        elif head == "※" and not bag["recap"]:
            bag["recap"] = s
        if all(bag.values()):
            break
    return PaneDetails(**bag)


def _smart_status(text: str, scan_lines: int = 60) -> str:
    """Most informative status line for a pane's last `scan_lines` lines.

    Walks the tail in reverse and returns, in order of preference:
      1. the most recent LIVE-signal line (✻ / ⏺ / ❯) — what's happening now;
      2. otherwise the most recent recap (※) — the idle summary;
      3. otherwise any non-noise line — generic fallback for non-Claude panes.
    Banner / HUD / separator rules and half-rendered glyph fragments are
    filtered out throughout.
    """
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""
    tail = lines[-scan_lines:]

    def is_noise(s: str) -> bool:
        if any(p.search(s) for p in _NOISE_PATTERNS):
            return True
        # Half-rendered spinner fragment: glyph head but almost nothing after.
        if s[:1] in _ALL_HEADS and len(s[1:].lstrip()) < _MIN_GLYPH_CONTENT:
            return True
        return False

    # Pass 1: live conversation signal — overrides recap even when older.
    for ln in reversed(tail):
        s = ln.strip()
        if is_noise(s):
            continue
        if s[:1] in _LIVE_HEADS:
            return s
    # Pass 2: recap is the idle fallback.
    for ln in reversed(tail):
        s = ln.strip()
        if not is_noise(s) and s[:1] == _RECAP_HEAD:
            return s
    # Pass 3: any non-noise line (plain shell output, build progress, etc.).
    for ln in reversed(tail):
        s = ln.strip()
        if not is_noise(s):
            return s
    return tail[-1].strip()


def _default_runner(argv: Sequence[str]) -> "tuple[int, str, str]":
    p = subprocess.run(list(argv), capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


class CmuxClient:
    """Thin wrapper over the cmux CLI with an injectable runner."""

    def __init__(self, binary: str = DEFAULT_CMUX, runner: Optional[Runner] = None):
        self.binary = binary
        self.run = runner or _default_runner

    # --- argv builders (pure, asserted in tests) ---------------------------
    def _rpc_argv(self, method: str, params: dict) -> list[str]:
        return [self.binary, "rpc", method, json.dumps(params)]

    def _send_text_argv(self, surface: str, text: str) -> list[str]:
        return self._rpc_argv("surface.send_text", {"surface_id": surface, "text": text})

    def _send_key_argv(self, surface: str, key: str) -> list[str]:
        return self._rpc_argv("surface.send_key", {"surface_id": surface, "key": key})

    def _focus_argv(self, surface: str) -> list[str]:
        return self._rpc_argv("surface.focus", {"surface_id": surface})

    def _read_argv(self, surface: str) -> list[str]:
        return self._rpc_argv("surface.read_text", {"surface_id": surface})

    # --- operations --------------------------------------------------------
    def list_sessions(self) -> list[Session]:
        """All terminal panes across ALL cmux windows.

        `workspace.list` with an empty argument only returns the caller's
        window — splitting the board into its own window would otherwise
        hide every agent pane. We fan out: enumerate windows, then list each
        window's workspaces, then list each workspace's surfaces. The merged
        JSON is fed to `build_sessions` so nicknames/numbers stay
        deterministic across the entire fleet.
        """
        wrc, wout, werr = self.run(self._rpc_argv("window.list", {}))
        if wrc != 0:
            raise RuntimeError(f"cmux window.list failed: {werr.strip() or wrc}")
        window_ids = [w.get("id", "") for w in json.loads(wout).get("windows", []) if w.get("id")]
        if not window_ids:
            window_ids = [""]  # fall back to caller-window behaviour

        all_workspaces: list[dict] = []
        surfaces_by_ws: dict[str, str] = {}
        # window_pos = ordinal of the window in the fleet → stable cross-window
        # ordering even though each window restarts its own `index` at 0.
        for window_pos, wid in enumerate(window_ids):
            params = {"window_id": wid} if wid else {}
            rc, out, err = self.run(self._rpc_argv("workspace.list", params))
            if rc != 0:
                continue
            for w in json.loads(out).get("workspaces", []):
                ws_id = w.get("id", "")
                if not ws_id or ws_id in surfaces_by_ws:
                    continue
                w = dict(w)  # don't mutate cmux response
                w["index"] = window_pos * 1000 + int(w.get("index", 0) or 0)
                all_workspaces.append(w)
                src, sout, _ = self.run(self._rpc_argv("surface.list", {"workspace_id": ws_id}))
                surfaces_by_ws[ws_id] = sout if src == 0 else '{"surfaces": []}'

        merged = json.dumps({"workspaces": all_workspaces})
        return build_sessions(merged, surfaces_by_ws, registered_board_surfaces())

    def read_surface_text(self, surface: str) -> str:
        """Full visible text of a surface's screen (empty on any failure)."""
        rc, out, err = self.run(self._read_argv(surface))
        if rc != 0 or not out.strip():
            return ""
        try:
            return json.loads(out).get("text", "") or ""
        except (ValueError, AttributeError):
            return ""

    def read_surface(self, surface: str) -> str:
        """Most informative recent status line — what the pane is actually doing.

        Skips Claude Code's persistent bottom banner, the OMC HUD, separator
        rules, and bare prompts; prefers a Claude Code recap (`※`) or activity
        verb (`✻`) over a raw prompt/response line.
        """
        return _smart_status(self.read_surface_text(surface))

    def read_surface_details(self, surface: str) -> PaneDetails:
        """Structured multi-signal extract for a rich board card."""
        return _extract_details(self.read_surface_text(surface))

    def route(self, target, text: str) -> str:
        """Focus the targeted ship, type `text` verbatim, and submit (Enter).

        `target` is a nickname (`"alpha"`), an unambiguous prefix (`"alph"`),
        or for back-compat a 1-based number (int or digit string). Focuses
        first so the pane pops to the front and the captain watches the order
        land; sends text then a separate Enter key. Returns the target surface
        UUID. Raises KeyError if `target` doesn't match any live ship.
        """
        surface = resolve_target(target, self.list_sessions())
        if not surface:
            raise KeyError(f"no session named {target!r}")
        self.run(self._focus_argv(surface))
        self.run(self._send_text_argv(surface, text))
        self.run(self._send_key_argv(surface, "Enter"))
        return surface

    def focus_by_checkpoint(self, checkpoint_id: str) -> Optional[str]:
        """Focus the cmux surface running the Claude session `checkpoint_id`.

        Backs the cardputer physical session switcher: the firmware sends the
        selected Claude session_id, and we pop its terminal pane (and its
        owning workspace + window) to the front. `checkpoint_id` is cmux's
        resume key for an agent pane — for a Claude Code pane it equals the
        Claude `--session-id`, so the match is exact, not heuristic.

        Returns the focused surface UUID on success, or None if no live surface
        carries that session id (e.g. a manually-started claude with no cmux
        agent-hook record) or the focus call fails — the caller treats None as
        a no-op, never an error.
        """
        cid = (checkpoint_id or "").strip()
        if not cid:
            return None
        match = next(
            (s for s in self.list_sessions() if s.checkpoint_id == cid), None
        )
        if match is None:
            return None
        rc, _out, _err = self.run(self._focus_argv(match.surface))
        return match.surface if rc == 0 else None

    def session_labels(self) -> dict:
        """{checkpoint_id: human label} for all live claude surfaces.

        Backs the cardputer session-list labels: the firmware shows the cmux
        auto-name (or interim prompt) instead of a raw UUID. Keyed by
        checkpoint_id (= Claude session_id) so the bridge can attach a label to
        each payload session by sid. Sessions with no checkpoint_id are skipped.
        """
        out = {}
        for s in self.list_sessions():
            if s.checkpoint_id:
                out[s.checkpoint_id] = label_from_title(s.title)
        return out

    def read_status(self, target) -> str:
        """Return the last non-empty line of the targeted ship's pane."""
        surface = resolve_target(target, self.list_sessions())
        if not surface:
            raise KeyError(f"no session named {target!r}")
        return self.read_surface(surface)
