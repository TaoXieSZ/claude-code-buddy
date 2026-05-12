# cc-bridge

Claude Code (CLI) → M5StickC buddy bridge. Same wire protocol as Claude
Desktop's built-in BLE bridge — stick firmware needs zero changes.

## What it does

Claude Desktop ships a BLE bridge that pushes session state (running,
waiting on permission, recent transcript lines, token totals) to a paired
M5StickC running this firmware. The buddy reacts: sleeps when nothing's
happening, gets impatient on permission prompts, celebrates on level-ups.

Claude Code (the terminal CLI) does **not** ship that bridge. It does ship
a hooks system. This bridge plugs into the hooks, aggregates them into the
same heartbeat schema documented in
[`REFERENCE.md`](../../REFERENCE.md), and writes them to the stick over
BLE NUS.

```
Claude Code (terminal)
   │  fires hooks (event JSON on stdin)
   ▼
hook.py  (per-event, fires-and-forgets)
   │  writes JSON line to /tmp/cc-bridge.sock
   ▼
bridge.py  (long-running launchd agent)
   │  aggregates events → REFERENCE.md heartbeat schema
   │  writes JSON to NUS RX (encrypted, requires macOS bond)
   ▼
M5StickC
```

## Install

```bash
./tools/cc-bridge/install.sh
```

The script:

1. Creates a Python venv at `~/.cc-bridge/venv` and installs `bleak`.
2. Renders a launchd plist into `~/Library/LaunchAgents/com.cc-bridge.plist`
   and bootstraps it (auto-starts at login, auto-restarts on crash).
3. Patches `~/.claude/settings.json` to fire `hook.py` for the relevant
   Claude Code events. Existing hooks are preserved; re-running the
   installer is idempotent.

You also need:

- `jq` — `brew install jq`
- One-time **Bluetooth pairing**: System Settings → Bluetooth, click the
  stick (advertises as `Claude-XXXX`), enter the 6-digit passkey shown on
  the stick's screen. macOS bonds the device; bleak can connect from then
  on without prompting.
- **Claude Desktop must NOT have its BLE bridge connected** to the same
  stick — only one BLE central can hold the connection at a time.

## Verify

After install:

```bash
tail -f ~/Library/Logs/cc-bridge.log
```

You should see something like:

```
INFO listening on /tmp/cc-bridge.sock
INFO scanning for stick (prefix=Claude-)
INFO connecting to Claude-F7C2 (XX:XX:XX:XX:XX:XX)
INFO connected
```

Then in another terminal start Claude Code. The buddy should:

- Wake from sleep within ~10s of the first session start
- Switch to attention (and chirp/blink LED) when a permission prompt fires
- Return to idle a few seconds after the session ends

If the stick is in BugC2 mode, you'll also see motion: in-place spin on
busy, attention twitch on permission, pink heartbeat on heart, etc.

## Uninstall

```bash
./tools/cc-bridge/install.sh uninstall
```

Removes the launchd agent and strips our hook entries from
`~/.claude/settings.json` (other hooks are left alone). The venv at
`~/.cc-bridge/venv` is left in place; remove with `rm -rf ~/.cc-bridge`
if you want a full clean.

## Configuration

Environment variables (set via `launchctl setenv` then `launchctl
kickstart -k gui/$(id -u)/com.cc-bridge`):

| Var | Default | What |
|---|---|---|
| `CC_BRIDGE_SOCKET` | `/tmp/cc-bridge.sock` | Unix socket path |
| `CC_BRIDGE_DEVICE_PREFIX` | `Claude-` | BLE name prefix to match |
| `CC_BRIDGE_LOG` | `~/Library/Logs/cc-bridge.log` | log file |

## Hook event mapping

| Claude Code event | Buddy state effect |
|---|---|
| `SessionStart` | `total++`, `running++`, msg=session start |
| `Stop` / `SessionEnd` | `running--`, `completed=true` (one-shot) |
| `PreToolUse` | msg=`running: <tool>`, push tool+input to entries |
| `PostToolUse` | msg=`done: <tool>` |
| `PermissionRequest` | `waiting=1`, fill `prompt` object, msg=`approve: <tool>` |
| `Notification` | msg=event message |
| `UserPromptSubmit` | push `you: <prompt>` to entries, msg=thinking… |
| `PostCompact` | push "compacted" to entries |

The stick also sends back `{"cmd":"mic","state":"down|up"}` when the user
taps A then holds A for ≥250ms. The daemon simulates a keystroke
(`CC_BRIDGE_PTT_KEYCODE`, default right Option) to trigger PTT dictation
apps. See README § Controls for gesture details.

State emits to the stick on every change + every 10s as keepalive.

## Limitations

- **One device at a time.** The stick is a single BLE peripheral and only
  one central can connect. If you want to switch between Claude Desktop
  and Claude Code, disconnect one before starting the other.
- **Permission echo not implemented.** The stick can show a pending
  permission prompt but pressing A on the stick won't approve back into
  Claude Code — its permission UX is in-terminal, not gated on a hook
  ack. v2 could intercept via a custom permission policy plugin.
- **Token counts are approximate.** Claude Code hooks don't expose live
  token-rate yet; we increment loosely on UserPromptSubmit.

## Architecture notes

`bridge.py` is now a thin adapter (~30 LoC) that calls `buddy_core.run()`
with an `apply_event` function and config. All shared logic — BLE writer,
socket server, heartbeat loop, reconnect watchdog, mic/permission relaying —
lives in `tools/buddy_core/core.py` (~300 LoC, zero duplication with
cursor-bridge).

Core tasks inside `buddy_core.run()`:

1. **Unix socket server** — accepts hook events, calls your `apply_event` to
   mutate `BuddyState`, signals dirty.
2. **Heartbeat loop** — emits a fresh JSON payload to the stick on dirty
   OR on keepalive timeout, whichever fires first.
3. **Reconnect watchdog** — keeps the BLE connection alive with capped
   backoff (2/4/8/16/30s).

cc-bridge config:
- `keepalive_s=10` — 10s heartbeat interval
- `rtc_sync_on_connect=False` — Claude Desktop sends time via the main app

The hook script is intentionally bare-bones (~30 LoC). It's a shim that
forwards stdin to the daemon and exits in <100ms so Claude Code is never
blocked.
