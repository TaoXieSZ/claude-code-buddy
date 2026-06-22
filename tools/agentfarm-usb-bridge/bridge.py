#!/usr/bin/env python3
"""Agent Farm → Tab5 USB-serial bridge.

Polls the Agent Farm trigger-cursor admin API on localhost (always reachable on
the laptop, regardless of which WiFi it's on) and pushes new trigger firings to
the Tab5 over USB-CDC as newline-delimited JSON. The Tab5's SerialFeedClient
(src/agentfarm_feed/serial_feed_client.*) parses these lines.

Wire format (one JSON object per line):
  {"t":"<iso>","n":"<trigger_name>","ty":"<type>","a":"<agent>","r":"<result>","new":<bool>}
  {"hb":1}                                  # heartbeat, keeps the device "online"

On connect it sends the latest snapshot with "new":false (history only, no pet
reaction), then live firings with "new":true.

Usage:
  python3 tools/agentfarm-usb-bridge/bridge.py [--port /dev/cu.usbmodemXXXX]
      [--host 127.0.0.1] [--admin-port 60360] [--config <config.yaml>]
      [--interval 3]

Requires: pyserial. Token is read from the trigger-cursor config.yaml admin.secret.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

try:
    import serial  # pyserial
    from serial.tools import list_ports
except ImportError:
    sys.exit("pyserial not installed — run: pip3 install pyserial")

DEFAULT_CONFIG = os.path.expanduser(
    "~/OpenSourceProjects/agent-farm/trigger-cursor/config.yaml")


def read_admin_secret(config_path: str) -> str:
    text = open(config_path).read()
    m = re.search(r"^admin:\s*$.*?^\s*secret:\s*(\S+)", text, re.M | re.S)
    if not m:
        sys.exit(f"admin.secret not found in {config_path}")
    return m.group(1).strip()


def find_port() -> str | None:
    # Espressif USB-Serial-JTAG is VID 0x303A; pick the first match.
    for p in list_ports.comports():
        if (p.vid == 0x303A) or ("usbmodem" in (p.device or "")):
            return p.device
    return None


def fetch_logs(base: str, token: str, limit: int):
    req = urllib.request.Request(
        f"{base}/api/logs?limit={limit}",
        headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=4) as r:
        return json.load(r).get("items", [])  # newest-first


def to_line(it: dict, is_new: bool) -> bytes:
    obj = {
        "t": it.get("timestamp", ""),
        "n": it.get("trigger_name", ""),
        "ty": it.get("trigger_type", ""),
        "a": it.get("agent_name", ""),
        "r": it.get("result", ""),
        "new": is_new,
    }
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", default="", help="serial port (auto-detect if omitted)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--admin-port", type=int, default=60360)
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--interval", type=float, default=3.0, help="poll seconds")
    ap.add_argument("--limit", type=int, default=12)
    a = ap.parse_args()

    base = f"http://{a.host}:{a.admin_port}"
    token = read_admin_secret(a.config)
    print(f"[bridge] Agent Farm {base}  config={a.config}")

    while True:  # reconnect loop
        port = a.port or find_port()
        if not port:
            print("[bridge] no serial port found; retrying…")
            time.sleep(2)
            continue
        try:
            ser = serial.Serial(port, 115200, timeout=0.2)
        except Exception as e:
            print(f"[bridge] open {port} failed: {e}; retrying…")
            time.sleep(2)
            continue
        print(f"[bridge] connected {port}")

        last_seen = ""
        primed = False
        last_hb = 0.0
        try:
            while True:
                try:
                    items = fetch_logs(base, token, a.limit)
                except Exception as e:
                    print(f"[bridge] poll failed: {e}")
                    items = None

                if items is not None and items:
                    newest = items[0].get("timestamp", "")
                    if not primed:
                        # snapshot oldest->newest, no pet reaction
                        for it in reversed(items):
                            ser.write(to_line(it, False))
                        primed = True
                        last_seen = newest
                        print(f"[bridge] primed {len(items)} entries")
                    elif newest <= last_seen:
                        pass  # host log reset or nothing new
                    else:
                        fresh = [it for it in reversed(items)
                                 if it.get("timestamp", "") > last_seen]
                        for it in fresh:
                            ser.write(to_line(it, True))
                        last_seen = newest
                        if fresh:
                            print(f"[bridge] pushed {len(fresh)} new")

                now = time.time()
                if now - last_hb >= 2.0:
                    ser.write(b'{"hb":1}\n')
                    last_hb = now

                ser.reset_input_buffer()  # discard device debug chatter
                time.sleep(a.interval)
        except (serial.SerialException, OSError) as e:
            print(f"[bridge] serial lost: {e}; reconnecting…")
            try:
                ser.close()
            except Exception:
                pass
            time.sleep(2)
        except KeyboardInterrupt:
            print("\n[bridge] bye")
            ser.close()
            return 0


if __name__ == "__main__":
    sys.exit(main())
