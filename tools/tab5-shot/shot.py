#!/usr/bin/env python3
"""Capture the Tab5 screen to a PNG via the running bridge daemon.

The daemon owns the Tab5's USB-CDC serial port, so we ask it (over its unix
socket) to take a screenshot. The daemon sends {"cmd":"shot"} to the device,
captures the SHOT…ENDSHOT framebuffer frame, writes a PNG, and replies with the
path. Print the path on success so a human can `open` it (or an agent can read
it directly).

Usage:
  python3 tools/tab5-shot/shot.py [--socket PATH] [--timeout SEC]

Socket defaults to $CURSOR_BRIDGE_SOCKET, then $CC_BRIDGE_SOCKET, then
/tmp/cursor-bridge.sock.
"""
import argparse
import json
import os
import socket
import sys


def main() -> int:
    default_sock = (os.environ.get("CURSOR_BRIDGE_SOCKET")
                    or os.environ.get("CC_BRIDGE_SOCKET")
                    or "/tmp/cursor-bridge.sock")
    ap = argparse.ArgumentParser(description="Capture the Tab5 screen to a PNG.")
    ap.add_argument("--socket", default=default_sock, help="daemon unix socket")
    ap.add_argument("--timeout", type=float, default=10.0, help="seconds to wait")
    a = ap.parse_args()

    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(a.timeout)
        s.connect(a.socket)
    except OSError as e:
        print(f"cannot reach daemon socket {a.socket}: {e}", file=sys.stderr)
        return 2

    try:
        s.sendall((json.dumps({"action": "screenshot",
                               "timeout": a.timeout}) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
    except OSError as e:
        print(f"screenshot request failed: {e}", file=sys.stderr)
        return 2
    finally:
        s.close()

    try:
        resp = json.loads(buf.splitlines()[0])
    except (ValueError, IndexError):
        print(f"bad response: {buf!r}", file=sys.stderr)
        return 2

    if resp.get("ok") and resp.get("path"):
        print(resp["path"])
        return 0
    print(f"screenshot failed: {resp.get('error', 'unknown')}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
