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
- Translates them into the Claude Code hook schema that
  `bridge.py:apply_event()` already understands. (Translation table is in
  `cursor_hook.js`.)
- Forwards over a Unix socket (`/tmp/cursor-bridge.sock`) to a long-running
  launchd daemon that owns the BLE link to the stick.

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

Two daemons scanning `Claude-*` will fight for whichever device
advertises first. After install, pin each:

```bash
# stick #1, owned by cc-bridge:
launchctl setenv CC_BRIDGE_DEVICE_PREFIX Claude-F7C2
launchctl kickstart -k gui/$(id -u)/com.cc-bridge

# stick #2, owned by cursor-bridge:
launchctl setenv CURSOR_BRIDGE_DEVICE_PREFIX Claude-6DE2
launchctl kickstart -k gui/$(id -u)/com.cursor-bridge
```

Replace the last-4 with whatever your sticks advertise (`system_profiler
SPBluetoothDataType | grep Claude-` lists them).

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

## What's not in v1

- **Permission echo** — pressing A/B on the stick to allow/deny an agent
  tool call. Cursor's permission protocol differs from Claude Code's
  `hookSpecificOutput` shape; we'll add it as a separate sync hook in a
  follow-up.
- **Fancy state mapping** — `beforeMCPExecution` is currently
  surfaced as `tool_name="mcp:<method>"` and `afterMCPExecution` collapses
  back to plain `mcp`. Good enough for the buddy to show "running: mcs"
  but loses per-tool granularity.

See also: `tools/cc-bridge/README.md`, `docs/onboarding-next-stick.md`.
