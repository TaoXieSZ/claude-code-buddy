# Tab5 M3 — dual live feed (Claude + Cursor on one device)

Goal: show **two live sessions simultaneously** on the Tab5 — `g_sess[0]`
"Claude Code" and `g_sess[1]` "Cursor" — both fed from the same laptop over
the single USB-CDC link. No WiFi (laptop roams networks; USB is zero-config
and always present when plugged in).

Status: design for review. Nothing implemented yet.

## Why this is needed (current behaviour)

- Firmware `src/tab5/ui.cpp` hardcodes every feed into `g_sess[0]`
  (`uiFeedState` / `uiFeedLine` / `uiFeedPrompt` / `uiFeedAlive` all write
  `g_sess[0]`). `g_sess[1]` ("Cursor") is seeded with demo text `等待任务…`
  and is **never** updated. So switching to the Cursor tab always shows the
  placeholder, even though cursor-bridge data is arriving — it lands on the
  "Claude Code" tab.
- Tab5 has exactly **one** USB-CDC serial port. cc-bridge and cursor-bridge
  are separate processes; only one can hold the port. So "two independent
  daemons each owning the wire" is impossible.

## Constraints / decisions

- **Transport = single USB serial, aggregated.** No WiFi/TCP (roaming laptop).
- Both daemons stay **independent processes** (separate hook systems:
  `~/.claude/settings.json` vs `~/.cursor/hooks.json`, separate sockets).
- Reuse the existing NDJSON heartbeat schema (REFERENCE.md) + the existing
  `{"cmd":"permission",...}` ack round-trip. Add the minimum needed for
  routing.

## Wire protocol additions

1. **Source tag on every heartbeat (host → Tab5):**
   ```json
   { "app": "claude" | "cursor", ...existing heartbeat fields... }
   ```
   Absent `app` ⇒ treat as `claude` (back-compatible with today's firmware).

2. **Source tag on the permission ack (Tab5 → host):**
   ```json
   { "cmd": "permission", "app": "cursor", "id": "...", "decision": "once" | "deny" }
   ```
   The firmware knows which session card the prompt is on, so it echoes that
   session's `app`. Ack ids are already globally unique per daemon
   (`cursor_*` vs cc's), so `app` is belt-and-suspenders for routing.

## Architecture: `tab5-hub` (serial multiplexer)

```
  Claude Code hooks ─▶ cc-bridge ─┐                         ┌─▶ g_sess[0] "Claude Code"
                                   │  unix socket   USB-CDC  │
                                   ├─▶ tab5-hub ───────────▶ Tab5 firmware (routes by app)
                                   │  (owns serial)          │
  Cursor hooks ─────▶ cursor-bridge┘                         └─▶ g_sess[1] "Cursor"
```

- **`tab5-hub`** (new, small): owns `/dev/cu.usbmodem*`; listens on a local
  unix socket (e.g. `/tmp/tab5-hub.sock`). Fans **in** every client's tagged
  heartbeat → writes to serial. Reads serial → routes each inbound line:
  - `{"cmd":"permission","app":X,...}` → the client whose app == X.
  - other cmds (mic / telemetry) → broadcast to all clients.
- **Daemon side**: replace `SerialPortWriter` with a `HubWriter` that has the
  same duck-typed surface (`any_connected` / `ensure_connected` / `write` /
  `close`) but talks the hub unix socket instead of a raw tty. It injects its
  own `app` tag into each payload and feeds inbound lines into the existing
  `on_stick_line` dispatcher (permission futures + mic relay unchanged).
- Daemons remain decoupled: either can be down without affecting the other;
  the hub just sees one fewer client.

Alternative considered: make cc-bridge the serial owner and have
cursor-bridge forward to it. Rejected — couples cursor's liveness to
cc-bridge and makes cc-bridge asymmetric. A dedicated hub keeps both daemons
symmetric.

## File-by-file change list

Firmware (`src/tab5/`, branch `feat/sticks3-buddy`):
- `ui.h` — `uiFeedAlive/State/Line/Prompt` gain a leading `int sess` arg.
- `ui.cpp` — index `g_sess[sess]` instead of hardcoded `[0]`; per-session
  `g_live`; retire `g_sess[1]` demo once its first real feed arrives; the
  permission ack writer includes the session's `app`.
- `feed.cpp` — parse `app` → session index; thread it through `uiFeed*`;
  permission ack line carries `app`.

Daemons (`tools/`):
- `buddy_core/core.py` — add `HubWriter`; `run()` gains `hub_socket` + `app`.
- `cc-bridge/bridge.py` — `app="claude"`, env `CC_BRIDGE_TAB5_HUB`.
- `cursor-bridge/bridge.py` — `app="cursor"`, env `CURSOR_BRIDGE_TAB5_HUB`.
- `tab5-hub/hub.py` (new) + `com.tab5-hub.plist.template` + `install.sh`.

Tests:
- `tests/test_buddy_core.py` — `HubWriter` round-trip (pty/socket), app-tag
  injection, inbound permission demux.
- hub unit test — fan-in to serial + ack routing by app.

## Phasing

- **Phase 1 — tagging + firmware routing (no hub yet).** Add `app` tag to
  both daemons; firmware routes by `app`. Effect: whichever single daemon
  currently holds the serial port shows on its **correct** tab (Cursor on
  the Cursor tab). Not simultaneous, but proves the routing end-to-end with
  minimal risk and no new process.
- **Phase 2 — `tab5-hub`.** Add the multiplexer so both daemons feed at once.
  Switch both daemons from `SerialPortWriter` to `HubWriter`.

## Cross-checkout coordination (important)

The pieces live on different working copies:
- Firmware + dev cc-bridge run from the `feat/sticks3-buddy` worktree.
- The running cursor-bridge daemon runs from `claude-desktop-buddy-cursor`
  on `feat/cursor-next`.
- `main` checkout has yet another line.

The protocol (`app` tag) must land consistently on every branch that ships a
daemon or the firmware. Plan: implement on `feat/sticks3-buddy` first
(firmware + buddy_core + hub), then port the daemon-side `app`/`HubWriter`
bits to `feat/cursor-next` (mirroring how the serial feed was ported).

## Open questions

1. Hub socket path + launchd label naming — `com.tab5-hub`? Auto-detect the
   Tab5 tty (only one `usbmodem` is the Tab5; `ioreg` serial `80:F1:B2:D1:51:7D`)
   or pin via env?
2. Do we want >2 apps later (e.g. codex)? If so, make session count + tabs
   data-driven instead of the fixed 2. (Current firmware is fixed at 2.)
3. mic/telemetry inbound routing — broadcast is fine for now; revisit if a
   per-app mic gesture is ever added on the Tab5.
