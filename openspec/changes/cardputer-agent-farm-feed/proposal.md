## Why

The buddy fleet today (Plus2, StackChan, Tab5) mirrors **one local** coding
session via BLE/serial through a Mac daemon. But the user also runs a remote-ish
**Agent Farm** (`agent-farm`: `api-server-cursor` + `trigger-cursor`) — a fleet
of Cursor SDK agents driven by Slack/cron/Jira **triggers** — and there is no
physical, glanceable surface for it. It is only visible via Slack or the admin
web UI.

The idle **M5 Cardputer ADV** is the right device to fill this gap, and it is
*complementary* to the existing buddies rather than redundant:

- **WiFi-first** (ESP32-S3): it can talk to the Agent Farm HTTP server **directly
  over the LAN**, with no BLE and no Mac daemon middleman. The existing buddies
  are BLE→Mac.
- **Real 56-key keyboard**: no other buddy has one (later milestones can type
  replies/prompts back; v1 only uses it to scroll the feed).
- Mic + ES8311 + speaker, IMU, IR: room for later voice/gesture milestones.

This change scopes **v1 only**: a read-only **desk pet that tracks every Trigger
firing** in the Agent Farm, over WiFi, with **zero changes to Agent Farm**.

## What Changes

- **New WiFi-direct transport (firmware).** The Cardputer joins the LAN and polls
  the Agent Farm `trigger-cursor` admin API directly. The admin server already
  listens on `0.0.0.0:<admin.port>` (`trigger-cursor/src/server.ts:768`), so it
  is reachable from the LAN with the `admin.secret` Bearer token
  (`trigger-cursor/src/config.ts:13`).
- **Trigger event feed = existing endpoint.** Every trigger firing is already
  recorded as a `TriggerLog` (`trigger-cursor/src/dispatcher.ts:17`) and exposed
  via `GET /api/logs?limit=&type=` (`trigger-cursor/src/server.ts:171`). The
  Cardputer keeps a client-side cursor, polls every few seconds, and renders new
  entries. **No new Agent Farm endpoint is needed for v1.**
- **Desk-pet UX (firmware).** The 240×135 LCD shows a live feed plus a pet that
  reacts to `TriggerLog.result` (error/success/queued/skipped) and shows an icon
  per `TriggerLog.trigger_type` (slack/cron/jira/manual). Goes to SLEEP after
  inactivity. Reuses the existing character-pack art/renderer concept; transport
  and data model are new.
- **No BLE, no Mac daemon** in this path. The Cardputer is a standalone WiFi
  client of Agent Farm.

## Capabilities

### New Capabilities
- `cardputer-agentfarm-transport`: WiFi station + HTTP polling client for the
  Agent Farm admin API — host discovery (mDNS or static IP), Bearer auth,
  `GET /api/logs` polling with a dedup cursor, and graceful offline/backoff
  behavior. Read-only; no Agent Farm code change.
- `cardputer-trigger-pet`: the on-device feed + pet UX — render newest
  `TriggerLog` entries on the 240×135 LCD, map `result`/`trigger_type` to pet
  reactions and sounds, aggregate idle→SLEEP, and use the keyboard to scroll the
  feed (navigation only in v1).

### Modified Capabilities
<!-- None. This is a new device target + new transport. The existing
     daemon-event-mapping / BLE heartbeat path is untouched. -->

## Impact

- **Firmware (new target).** Add a Cardputer build (e.g. PlatformIO env
  `cardputer-agentfarm`) under the buddy repo: WiFi STA + HTTP client, the poll
  loop + cursor, and the feed/pet renderer for the 240×135 ST7789V2 LCD. Reuse
  character-pack assets; do **not** reuse the BLE/serial transport.
- **Agent Farm: no change for v1.** Reads the existing `/api/logs` (and
  optionally `/api/status`). A future milestone may add a `TriggerLog` SSE/WS
  broadcast for instant push + lower power, but v1 is poll-only.
- **Config / secrets.** The Cardputer needs the LAN host (mDNS name or static IP)
  and the `admin.secret`. Keep credentials out of git (build-time creds via a
  skipped-worktree `wifi_secrets.ini`, matching the existing camera-gesture
  pattern).
- **Known v1 limits (documented, not bugs).** Polling latency of a few seconds;
  `/api/logs` is in-memory (max 500, cleared on restart) so it is a live feed,
  not history; continuous WiFi (~132 mA) drains the 1750 mAh battery in ~10 h, so
  the pet assumes desk power or a longer poll interval + screen-off when idle.
