#!/usr/bin/env python3
"""
PreToolUse hook with stick-approval echo.

Different from hook.py (fire-and-forget): this one synchronously asks the
cc-bridge daemon to wait for a stick decision. If the user presses A on
the stick within the timeout, we return a Claude Code permissionDecision
of "allow"; B returns "deny"; timeout returns "ask" (= defer to Claude
Code's normal terminal prompt). If the bridge or stick is unreachable we
also fall through to "ask" silently — so this hook never makes things
worse than vanilla.

Wired into PreToolUse via tools/cc-bridge/install.sh. The companion
fire-and-forget hook.py still handles SessionStart, Stop, etc.

Disable per-tool with the CC_BRIDGE_PERMISSION_ECHO env var: set to "0"
or unset to skip the wait entirely (hook returns no decision).
"""

import json
import os
import socket
import sys
import time

SOCKET_PATH = os.environ.get("CC_BRIDGE_SOCKET", "/tmp/cc-bridge.sock")
TIMEOUT_S = float(os.environ.get("CC_BRIDGE_PERMISSION_TIMEOUT_S", "8"))
SOCKET_DEADLINE_S = TIMEOUT_S + 2.0

# Map stick decisions to Claude Code permissionDecision values.
DECISION_MAP = {
    "once": "allow",
    "always": "allow",
    "deny": "deny",
    "ask": "ask",
}

# Tools that never need stick approval — they're interactive prompts or
# pure planning/state tools that don't touch the system. Asking the user
# to approve being asked (AskUserQuestion) is a logic loop.
SAFE_TOOLS = {
    "AskUserQuestion",
    "ExitPlanMode",
    "EnterPlanMode",
    "TodoWrite",
    "TaskCreate",
    "TaskUpdate",
    "TaskList",
    "TaskGet",
    "TaskOutput",
    "TaskStop",
}


def _emit(decision: str | None, reason: str = ""):
    """Print Claude Code's hookSpecificOutput JSON and exit."""
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse"}}
    if decision in ("allow", "deny", "ask"):
        out["hookSpecificOutput"]["permissionDecision"] = decision
    if reason:
        out["hookSpecificOutput"]["permissionDecisionReason"] = reason
    print(json.dumps(out))


def main() -> int:
    # Quick disable knob — when off, behave like a no-op.
    if os.environ.get("CC_BRIDGE_PERMISSION_ECHO", "1") == "0":
        return 0

    try:
        raw = sys.stdin.buffer.read()
    except Exception:
        return 0
    if not raw:
        return 0
    try:
        ev = json.loads(raw)
    except Exception:
        return 0

    # Bypass: when Claude Code is in --dangerously-skip-permissions or
    # acceptEdits mode, the tool is going to auto-run regardless of what
    # we say. Don't bother the stick (and don't burn the 8s wait); just
    # exit clean and let Claude Code's normal flow proceed.
    mode = (ev.get("permission_mode") or "").lower()
    if mode in ("bypasspermissions", "acceptedits"):
        return 0
    if os.environ.get("CLAUDE_BYPASS_PERMISSIONS") in ("1", "true", "yes"):
        return 0

    tool = ev.get("tool_name") or "tool"
    if tool in SAFE_TOOLS:
        return 0
    sid = (ev.get("session_id") or "anon")[:8]
    rid = ev.get("request_id") or f"req_{sid}_{int(time.time() * 1000)}"
    ti = ev.get("tool_input") or {}
    hint = (
        ti.get("command")
        or ti.get("description")
        or ti.get("file_path")
        or ""
    )[:120]

    req = {
        "action": "wait_permission",
        "id": rid,
        "tool": tool,
        "hint": str(hint),
        "timeout": TIMEOUT_S,
    }

    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(SOCKET_DEADLINE_S)
        s.connect(SOCKET_PATH)
        s.sendall((json.dumps(req) + "\n").encode())
        # The bridge keeps the connection open until it can reply with
        # the stick's decision (or timeout). Read until newline.
        buf = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
            if b"\n" in buf:
                break
        s.close()
    except Exception:
        # Bridge down or no stick — defer to Claude Code's normal flow.
        return 0

    try:
        resp = json.loads(buf.decode().strip().splitlines()[-1])
        stick_decision = resp.get("decision", "ask")
    except Exception:
        return 0

    cc_decision = DECISION_MAP.get(stick_decision, "ask")
    if cc_decision == "ask":
        # No-op (let Claude Code prompt as usual).
        return 0

    reason = f"buddy stick: {stick_decision}"
    _emit(cc_decision, reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
