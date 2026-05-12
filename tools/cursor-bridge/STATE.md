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
| `prompt` | obj | `{id, tool, hint}` for pending stick-button approval | wait_permission RPC ✅ wired for `beforeShellExecution` + `beforeMCPExecution` |
| `completed` | bool | One-shot CELEBRATE trigger | reserved (level-up only) |

## Cursor hook event coverage

| Cursor event | Translated to | What it feeds | Status |
|---|---|---|---|
| `sessionStart` | `SessionStart` | `total++`, entries: "session start" | Cursor doesn't actually emit this |
| `sessionEnd` | `SessionEnd` | `total--` | Cursor doesn't actually emit this |
| `beforeSubmitPrompt` | `UserPromptSubmit` | `running++`, msg="thinking…", entries: "you: …" | ✅ verified live |
| `afterAgentResponse` | `Stop` | `running--`, tokens accum, entries: "buddy: …" | ✅ verified live |
| `afterAgentThought` / `stop` | `Stop` | `running--`, msg="ready" | ✅ verified live |
| `beforeShellExecution` | `wait_permission` RPC (sync) | msg="approve: shell", prompt={shell, command} → A=allow / B=deny / 8s=ask | ✅ permission echo |
| `afterShellExecution` | `PostToolUse(shell)` | msg="done: shell" | ✅ verified live |
| `beforeMCPExecution` | `wait_permission` RPC (sync) | msg="approve: mcp:…", prompt={mcp:tool, hint} → A/B/timeout | ✅ permission echo |
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
- **Permission echo** — `cursor_hook_permission.js` is wired into
  `beforeShellExecution` and `beforeMCPExecution` only. It speaks the
  same `wait_permission` RPC as cc-bridge's `hook_permission.py`, so
  the daemon-side machinery (`PENDING` futures, `on_stick_line` ack
  handler) is shared. Cursor's response shape differs:

  ```json
  // What cursor_hook_permission.js writes to stdout
  {
    "permission": "allow" | "deny" | "ask",
    "user_message": "buddy stick: <decision>",
    "agent_message": "buddy stick: <decision>"
  }
  ```

  Stick decision → Cursor permission mapping:
  - `once` / `always` → `allow`
  - `deny` → `deny`
  - `ask` (timeout, daemon down, hook disabled) → no JSON body, exit 0
    so Cursor falls through to its own permission flow. Fail-open by
    default — set `failClosed: true` in `~/.cursor/hooks.json` per-script
    if you'd rather block on hook failures.

  Disable wholesale with `launchctl setenv CURSOR_BRIDGE_PERMISSION_ECHO 0`.
  Per-event matchers (e.g. only gate `curl|wget|rm -rf` shell commands)
  are configured in `~/.cursor/hooks.json` directly using Cursor's
  built-in `matcher` field — no daemon change needed.

## Known not-yet-wired

| Item | Why | Priority if needed later |
|---|---|---|
| `beforeReadFile` permission gate | Cursor reads files constantly; gating each one would tank the UX. Set `failClosed: true` + matcher manually in `hooks.json` if you want it for sensitive paths only | low |
| `subagentStart` / `preToolUse` gate | Would interrupt Multitask Mode workers and built-in tools. Skipped on purpose | low |
| Per-tool "always allow" memory on stick | Daemon respects `always` decision but stick has no UI to issue it yet (only A=once, B=deny) | medium if approval prompts get noisy |
| Model name display | Cursor doesn't include `model` in non-base hook payload (verified) | low — buddy has limited screen real estate |
| Workspace name | `workspace_roots[]` arrives but isn't surfaced | low |
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
