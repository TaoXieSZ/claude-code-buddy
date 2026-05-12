# cursor-bridge

Cursor IDE → M5StickC buddy bridge. Mirror image of `tools/cc-bridge/`
for Claude Code, but listens to Cursor's hook system instead.

Same wire protocol, same firmware, separate daemon — designed so you can
run **both** bridges on the same Mac, each pinned to its own stick.

## What it does

- Reads Cursor agent hook events (`sessionStart`, `beforeSubmitPrompt`,
  `beforeShellExecution`, `beforeMCPExecution`, `beforeReadFile`,
  `afterShellExecution`, `afterMCPExecution`, `afterFileEdit`,
  `afterAgentResponse`, `stop`, `sessionEnd`).
- Translates them into the Claude Code hook schema that the shared
  `buddy_core.run()` already understands. (Translation table is in
  `cursor_hook.js`.)
- Forwards over a Unix socket (`/tmp/cursor-bridge.sock`) to a long-running
  launchd daemon that owns the BLE link to the stick.
- **Shared core**: `tools/buddy_core/core.py` contains BLE writer, socket
  server, heartbeat loop, reconnect watchdog, and both mic + permission
  relaying. cursor-bridge provides only the `apply_event` function specific
  to Cursor hook semantics.

## Install

Prereqs: Python 3, Node.js, jq, a paired stick running this fork's
firmware (the bridge talks to the unencrypted debug NUS this fork adds —
upstream `anthropics/claude-desktop-buddy` firmware won't respond).

```bash
tools/cursor-bridge/install.sh
```

The installer:

1. Creates a venv at `~/.cursor-bridge/venv` and installs `bleak`.
2. Writes `~/Library/LaunchAgents/com.cursor-bridge.plist` and
   bootstraps it.
3. Backs up `~/.cursor/hooks.json` (other tools share that file) and
   merges 11 hook entries that point at `cursor_hook.js`.

Idempotent — re-run any time. Strips its old entries before re-adding,
so the merge stays clean.

## Pin to a specific stick

A stick flashed with `m5stickc-plus2-cursor` advertises as `Cursor-*`
and a stick flashed with `m5stickc-plus2-claude` advertises as
`Claude-*`, so the two daemons naturally don't fight for the same
advertisement — no env-var pinning needed in the standard two-stick
setup.

If you flashed both sticks with the **same** firmware variant for
testing, pin by MAC suffix:

```bash
launchctl setenv CURSOR_BRIDGE_DEVICE_PREFIX Cursor-6DE2
launchctl kickstart -k gui/$(id -u)/com.cursor-bridge
```

Replace the last-4 with whatever your stick advertises (`system_profiler
SPBluetoothDataType | grep -E '(Claude|Cursor)-' ` lists them).

## Operate

```bash
# log
tail -f ~/Library/Logs/cursor-bridge.log

# alive?
launchctl list | grep cursor-bridge

# restart
launchctl kickstart -k gui/$(id -u)/com.cursor-bridge

# uninstall
tools/cursor-bridge/install.sh uninstall
```

## State model

See `STATE.md` for the full heartbeat schema, Cursor hook event coverage
matrix, and known gaps. Short version: every meaningful Cursor IDE
session signal — prompt submit, tool start/end (incl. failures),
subagent spawn, assistant turn end + token usage — feeds the buddy.
Stale sessions get reaped after 10 min idle so counters don't drift.

## Mic PTT gesture

The stick sends `{"cmd":"mic","state":"down|up"}` on the PTT gesture
(tap A, then hold A ≥250ms). The daemon relays to a keystroke
(`CURSOR_BRIDGE_PTT_KEYCODE`, default right Option) so Typeless or other
dictation apps pick it up. Same as cc-bridge. Requires
`pyobjc-framework-Quartz`.

## What's not in v1

- **Fancy MCP state mapping** — `beforeMCPExecution` is surfaced as
  `tool_name="mcp:<method>"` and `afterMCPExecution` collapses back to
  plain `mcp`. Good enough for "running: mcp:..." display but loses
  per-tool granularity.

## Footgun: macOS BLE pairing after firmware re-flash

After `esptool.py erase_flash` or any operation that resets the stick's
BLE bonding state, macOS keeps a stale pairing record and rejects every
reconnect with:

```
connect failed: Error Domain=CBErrorDomain Code=14
"Peer removed pairing information"
```

The daemon will retry forever and never succeed. Fix:
**System Settings → Bluetooth → find the device → ⓘ → Forget This
Device.** NUS is unbonded so no PIN re-entry needed; the daemon's next
30s scan reconnects automatically.

See also: `tools/cc-bridge/README.md`, `docs/onboarding-next-stick.md`.
