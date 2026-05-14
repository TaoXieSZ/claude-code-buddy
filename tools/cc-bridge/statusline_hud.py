#!/usr/bin/env python3
"""
statusline_hud.py — Claude Code statusline proxy for the stackchan HUD.

Claude Code invokes the configured `statusLine.command` on every render and
feeds it a JSON `StatuslineStdin` payload (model, context_window, rate_limits,
transcript_path). This proxy:

  1. taps that payload and fire-and-forwards a `hud` event to the cc-bridge
     Unix socket — giving the stackchan the live context %, real token count,
     rate-limit pressure, model name and session time it can't get from hooks;
  2. chains to the real OMC HUD so the *terminal* statusline is unchanged.

It is a transparent proxy: set it as your `statusLine.command` in place of the
bare omc-hud.mjs call. See REFERENCE.md / openspec change 0002.

Robustness contract (same as hook.py): the statusline MUST NOT stall or fail if
the cc-bridge daemon is down or the HUD target is missing — every failure path
is swallowed.

Config (env):
  CC_BRIDGE_SOCKET      cc-bridge Unix socket  (default /tmp/cc-bridge.sock)
  CC_BRIDGE_HUD_TARGET  the real statusline script to chain to
                        (default ${CLAUDE_CONFIG_DIR:-~/.claude}/hud/omc-hud.mjs)
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

SOCKET_PATH = os.environ.get("CC_BRIDGE_SOCKET", "/tmp/cc-bridge.sock")
TIMEOUT_S = 0.3  # never slow the statusline down


def _hud_target() -> Path:
    explicit = os.environ.get("CC_BRIDGE_HUD_TARGET")
    if explicit:
        return Path(explicit)
    cfg = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(cfg) / "hud" / "omc-hud.mjs"


def _session_ms(transcript_path: str | None) -> int:
    """Best-effort session elapsed time: now − first transcript entry's
    timestamp. Returns 0 if the transcript is missing/unparseable."""
    if not transcript_path:
        return 0
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            first = f.readline()
        ts = json.loads(first).get("timestamp")
        if not ts:
            return 0
        # ISO 8601, may end in Z.
        from datetime import datetime, timezone
        started = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - started
        return max(0, int(delta.total_seconds() * 1000))
    except Exception:
        return 0


def _extract_metrics(stdin_json: dict) -> dict:
    """Pull the four metric groups out of Claude Code's StatuslineStdin."""
    cw = stdin_json.get("context_window") or {}
    usage = cw.get("current_usage") or {}
    tokens = sum(
        v for v in (
            usage.get("input_tokens"),
            usage.get("cache_creation_input_tokens"),
            usage.get("cache_read_input_tokens"),
        ) if isinstance(v, int)
    )
    rl = stdin_json.get("rate_limits") or {}
    five = rl.get("five_hour") or {}
    seven = rl.get("seven_day") or {}
    model = (stdin_json.get("model") or {}).get("display_name") or ""

    def pct(x):
        return int(round(x)) if isinstance(x, (int, float)) else 0

    return {
        # Match the hook-event convention (hook.py forwards `hook_event_name`)
        # so cc-bridge's logging + apply_event see it the same way.
        "hook_event_name": "hud",
        "context_pct": pct(cw.get("used_percentage")),
        "tokens": tokens,
        "limit_5h": pct(five.get("used_percentage")),
        "limit_7d": pct(seven.get("used_percentage")),
        "model": model,
        "session_ms": _session_ms(stdin_json.get("transcript_path")),
    }


def _forward(payload: dict) -> None:
    """Fire-and-forget the hud event to cc-bridge. Swallow every error."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(TIMEOUT_S)
        s.connect(SOCKET_PATH)
        s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        s.close()
    except Exception:
        pass  # daemon down / socket missing — statusline carries on regardless


def _chain(raw: bytes) -> None:
    """Run the real statusline script with the same stdin, print its stdout."""
    target = _hud_target()
    if not target.exists():
        return  # nothing to chain to — metrics still forwarded above
    try:
        out = subprocess.run(
            ["node", str(target)],
            input=raw,
            capture_output=True,
            timeout=5,
        )
        sys.stdout.buffer.write(out.stdout)
    except Exception:
        pass


def main() -> int:
    try:
        raw = sys.stdin.buffer.read()
    except Exception:
        return 0
    try:
        stdin_json = json.loads(raw) if raw else {}
    except Exception:
        stdin_json = {}

    if stdin_json:
        _forward(_extract_metrics(stdin_json))
    _chain(raw)
    return 0


if __name__ == "__main__":
    sys.exit(main())
