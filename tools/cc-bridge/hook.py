#!/usr/bin/env python3
"""
Claude Code hook → cc-bridge daemon shim.

Claude Code fires this for each registered hook event. We read the JSON
payload off stdin, forward it to the bridge daemon over a Unix socket,
and exit immediately. Any failure (daemon down, socket missing, etc.)
exits 0 silently — the hook MUST NOT block Claude Code on a side
channel that may be temporarily offline.

Wired up by tools/cc-bridge/install.sh into ~/.claude/settings.json
under the relevant hook event names (PreToolUse, PostToolUse,
SessionStart, Stop, PermissionRequest, UserPromptSubmit, ...).
"""

import json
import os
import socket
import sys

SOCKET_PATH = os.environ.get("CC_BRIDGE_SOCKET", "/tmp/cc-bridge.sock")
TIMEOUT_S = 0.5  # don't slow Claude Code down for any reason


def main():
    # Read until EOF — read(N) sometimes returns early when Claude Code
    # writes incrementally before closing stdin, producing truncated JSON
    # the daemon then rejects.
    try:
        raw = sys.stdin.buffer.read()
    except Exception:
        return 0
    if not raw:
        return 0

    # Validate it's JSON; if not, just forward verbatim and let the
    # daemon log + skip.
    try:
        json.loads(raw)
    except Exception:
        pass

    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(TIMEOUT_S)
        s.connect(SOCKET_PATH)
        if not raw.endswith(b"\n"):
            raw += b"\n"
        s.sendall(raw)
        s.close()
    except Exception:
        # Daemon not running, socket missing, or broken — silently skip
        # so Claude Code never stalls on this side channel.
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
