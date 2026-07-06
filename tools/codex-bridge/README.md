# codex-bridge

Third agent feed for the cardputer buddy, after `cc-bridge` (Claude) and
`cursor-bridge` (Cursor). Reflects OpenAI **Codex CLI** session state on the
same cardputer. openspec change `cardputer-codex-sessions`.

## Why it's the simplest bridge

- **Codex hooks are already Claude-Code-shaped.** `~/.codex/hooks.json` fires
  `SessionStart` / `UserPromptSubmit` / `PreToolUse` / `PostToolUse` / `Stop` /
  `PermissionRequest` with the exact field names (`session_id`, `cwd`,
  `tool_name`, `tool_input`, `prompt`) that `apply_event` reads — so
  `codex_hook.js` is a near-identity forwarder and `bridge.py` mirrors cc-bridge.
- **Aggregation is free.** cc-bridge already keys `ext_sessions` by agent;
  codex-bridge pushes `{agent:"codex", sessions:[…]}` to its socket and cc-bridge
  merges it into the single-BLE-owner payload with **no cc-bridge change** to the
  merge. codex-bridge owns no BLE device.

## The one real difference — session identity by cwd

cmux gives a Codex pane **no session-id** (its title is just `codex`, unlike
Cursor's `cursor-<UUID>`). The only stable key shared by the Codex hook payload
AND the cmux pane is the working directory: hook `cwd` == cmux pane
`requested_working_directory` (verified byte-identical). So Codex sessions are
joined to live cmux panes **by cwd**, listed only when a live cmux Codex pane
exists, and focused by directory (`cmux_control.focus_by_codex_cwd`).

**Known limitation:** two Codex sessions in the same directory collide on cwd and
merge into one row (cmux exposes nothing finer). See the change's `design.md` D2.

## Files

| File | Role |
|---|---|
| `bridge.py` | daemon: hook socket → `apply_event` → per-session state → push `ext_sessions(codex)` to cc-bridge; cmux cwd reconcile + stale reaper |
| `codex_hook.js` | `~/.codex/hooks.json` shim — forwards each event's whitelisted fields to `/tmp/codex-bridge.sock`, fire-and-forget |
| `com.codex-bridge.plist.template` | launchd agent template |
| `install.sh` | venv + plist + `~/.codex/hooks.json` merge (nested Claude-Code schema; backs up + merges, never replaces the shared file) |

## Install

```bash
./tools/codex-bridge/install.sh           # idempotent
./tools/codex-bridge/install.sh uninstall
```

Then: cc-bridge must be running (it owns the cardputer BLE link); run codex once
and **approve the codex_hook.js trust prompt** (Codex won't run an untrusted
hook); open a Codex pane in cmux → the cardputer list shows it with a green
`cx` marker within ~15s.

## Status

Display pipeline (list / rotation / pin / `cx` marker / cwd focus) is wired and
unit-tested. The **permission echo** (device button → Codex allow/deny) is
deferred — `PermissionRequest` is wired async so the device SHOWS the waiting
state, but does not yet gate Codex (needs `codex_hook_permission.js`, a
follow-up; Codex's deny contract is the Claude-Code `{"decision":"block"}` JSON,
confirmed in spike).

## Env vars

| Var | Default | Meaning |
|---|---|---|
| `CODEX_BRIDGE_SOCKET` | `/tmp/codex-bridge.sock` | hook → daemon socket |
| `CC_BRIDGE_SOCKET` | `/tmp/cc-bridge.sock` | where ext_sessions are pushed (empty = disable) |
| `CODEX_BRIDGE_LOG` | `~/Library/Logs/codex-bridge.log` | log path |
| `CODEX_BRIDGE_DASH_PORT` | `0` (off) | dashboard port; off by default to avoid colliding with cc/cursor dashboards |
| `CMUX_BIN` | `/Applications/cmux.app/…/cmux` | cmux CLI for the cwd reconcile |
