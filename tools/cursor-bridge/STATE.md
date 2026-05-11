# cursor-bridge state model

What state the buddy surfaces for a Cursor IDE session, and which Cursor
hook events feed each field.

## Heartbeat schema (what bridge.py sends to the stick)

The stick consumes this JSON shape (full parser in `src/data.h _applyJson`):

| Field | Type | Meaning | Source |
|---|---|---|---|
| `total` | uint8 | Active Cursor sessions seen recently | session_id tracking + reaper |
| `running` | uint8 | Sessions whose assistant turn is in flight | UserPromptSubmit / Stop |
| `waiting` | uint8 | Sessions blocked on permission ack | PermissionRequest (not yet emitted by Cursor) |
| `msg` | str ≤24 | Current single-line status | last apply_event mutation |
| `entries` | str[≤8] | Recent transcript lines (newest first, ≤91 chars each) | UserPromptSubmit / PreToolUse / Stop / failures |
| `tokens` | uint32 | Tokens used in the last assistant turn | afterAgentResponse.output_tokens |
| `tokens_today` | uint32 | Cumulative tokens since daemon start | afterAgentResponse.output_tokens accum |
| `prompt` | obj | `{id, tool, hint}` for pending stick-button approval | wait_permission RPC (not yet wired for Cursor) |
| `completed` | bool | One-shot CELEBRATE trigger | reserved (level-up only) |

## Cursor hook event coverage

| Cursor event | Translated to | What it feeds | Status |
|---|---|---|---|
| `sessionStart` | `SessionStart` | `total++`, entries: "session start" | Cursor doesn't actually emit this |
| `sessionEnd` | `SessionEnd` | `total--` | Cursor doesn't actually emit this |
| `beforeSubmitPrompt` | `UserPromptSubmit` | `running++`, msg="thinking…", entries: "you: …" | ✅ verified live |
| `afterAgentResponse` | `Stop` | `running--`, tokens accum, entries: "buddy: …" | ✅ verified live |
| `afterAgentThought` / `stop` | `Stop` | `running--`, msg="ready" | ✅ verified live |
| `beforeShellExecution` | `PreToolUse(shell)` | msg="running: shell", entries: command | ✅ verified live |
| `afterShellExecution` | `PostToolUse(shell)` | msg="done: shell" | ✅ verified live |
| `beforeMCPExecution` | `PreToolUse(mcp:…)` | msg="running: mcp:…" | covered |
| `afterMCPExecution` | `PostToolUse(mcp)` | msg="done: mcp" | covered |
| `beforeReadFile` | `PreToolUse(read)` | msg="running: read", entries: file_path | covered |
| `afterFileEdit` | `PostToolUse(edit)` | msg="done: edit" | covered |
| `preToolUse` | `PreToolUse(<tool>)` | msg="running: <tool>" | generic fallback |
| `postToolUse` | `PostToolUse(<tool>)` | msg="done: <tool>" | generic fallback |
| `postToolUseFailure` | `PostToolUse(<tool>, failure=true)` | msg="failed: <tool>", entries: "!fail …" | ✅ failure surfaced |
| `subagentStart` | `PreToolUse(sub:<type>)` | msg="running: sub:<type>", entries: description | ✅ Multitask Mode visibility |
| `subagentStop` | `PostToolUse(sub:<type>)` | msg="done: sub:<type>" | ✅ |

## Daemon-side housekeeping

- **Stale session reaper** — every 60s, drops sessions with no events in
  10 min and recomputes `total` / `running` from the post-reap set. Without
  this, Cursor's missing `sessionEnd` would let `total` grow unbounded.
- **BLE reconnect** — backoff 2 → 4 → 8 → 16 → 30s. macOS CoreBluetooth
  drops idle GATT links so a 2s keepalive heartbeat fires regardless of
  state changes.
- **One-shot RTC sync on connect** — `{"time":[epoch, tz_offset]}` frame
  sent immediately after `start_notify` succeeds, every reconnect. The
  cursor stick has no Claude Desktop in the loop, so without this its
  clock display sits at 2000-01-01 (or whatever was on the coin cell).
- **Permission echo plumbing** — wire is there (`wait_permission` RPC,
  `PENDING` futures, `on_stick_line` ack handler) but Cursor's permission
  protocol is not yet hooked into `cursor_hook.js`. Out of scope for v1.

## Known not-yet-wired

| Item | Why | Priority if needed later |
|---|---|---|
| Stick-button permission approve for Cursor tool gates | Cursor's permission hook payload differs from Claude Code's `hookSpecificOutput`; needs translation | medium — only matters if Cursor ever exposes pre-tool gates we want stick to gate |
| Model name display | Cursor doesn't include `model` in its hook payload (verified) | low — buddy has limited screen real estate already |
| Workspace name | Cursor doesn't include `workspace` in its hook payload | low |
| PTT mic key relay | cc-bridge has it; intentionally omitted from cursor-bridge | not planned |

## Verification

To exercise the full pipeline manually:

1. Daemon connected? Tail `~/Library/Logs/cursor-bridge.log` and look
   for `subscribed to NUS TX`.
2. Hook firing? `CURSOR_HOOK_DEBUG=1` env var on the Cursor process makes
   `cursor_hook.js` append every event to `/tmp/cursor-hook-debug.jsonl`.
3. Heartbeat reaching stick? Daemon log emits one `emit:` line per
   heartbeat with the full payload summary.
4. Stick rendering? Look at the device — `entries[0]` shows on the main
   transcript scroll, `msg` shows in the status row, `running`/`waiting`
   light the badge counters.

If steps 1+2 work but 3 doesn't, BLE is wedged — see the "Peer removed
pairing information" footgun in this repo's git log, fix is to
System Settings → Bluetooth → Forget device, then daemon's next 30s retry
reconnects.
