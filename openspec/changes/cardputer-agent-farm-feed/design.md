# Design — Cardputer ADV as an Agent Farm desk pet (v1)

## Context

Two existing systems, no bridge between them:

```
  Existing buddy path (local session):
  ┌──────────┐   BLE    ┌─────────────┐  hooks  ┌────────────────┐
  │ StickC / │ ◀──────▶ │  Mac daemon │ ◀────── │ local Cursor / │
  │ StackChan│          │ cc/cursor-  │         │ Claude session │
  └──────────┘          │   bridge    │         └────────────────┘
                        └─────────────┘

  This change (remote fleet, WiFi-direct):
  ┌──────────┐  WiFi / HTTP poll  ┌──────────────────────────────┐
  │ Cardputer│ ◀────────────────▶ │ Agent Farm (trigger-cursor)  │
  │   ADV    │  GET /api/logs     │ admin HTTP @ 0.0.0.0:<port>  │
  └──────────┘  Bearer secret     └──────────────────────────────┘
```

The Agent Farm host may be a Mac on the same LAN or a server; either way it is an
HTTP server reachable at a LAN address. The transport is identical.

## Key decisions

### D1 — WiFi-direct, not BLE-via-Mac-daemon
Agent Farm is already HTTP-reachable on the LAN. Routing the Cardputer through
the existing BLE→Mac-daemon stack would (a) require the Mac to be on, (b) waste
the Cardputer's WiFi + keyboard advantages, and (c) bolt a remote-fleet data
source onto a per-session state machine. **Decision: the Cardputer is a direct
WiFi HTTP client of Agent Farm.** The BLE path is untouched and unrelated.

### D2 — Poll the existing `/api/logs`, add no Agent Farm endpoint (v1)
Every trigger firing is already a `TriggerLog` exposed by `GET /api/logs`. v1
polls it and diffs client-side. This keeps Agent Farm at **zero change** and
de-risks the device-side work. Push (SSE/WS broadcast of `TriggerLog`) is a
deliberate **v2** to cut latency and battery; not in scope here.

### D3 — Client-side dedup cursor
`/api/logs` returns the most recent entries (newest-first, capped at 500). The
device records the last-seen entry key (timestamp + trigger_name, or a stable
composite) and treats anything newer as "new". On host restart the log resets;
the device re-syncs to the new newest entry without replaying or alerting on the
backlog.

### D4 — Data model is a feed + aggregate state, not the BLE session state machine
The existing buddy maps ONE session to SLEEP/IDLE/BUSY/ATTN/DONE. Here the data
is a **stream of independent trigger firings across many agents**. So the model
is:
- a scrolling **feed** of recent `TriggerLog` rows, and
- one **aggregate pet mood** derived from the latest events (recent error →
  worried; recent success → happy; quiet for N seconds → SLEEP).

### D5 — `TriggerLog` → pet reaction mapping
| `result` | mood + sound |
|---|---|
| `error` | worried/red + alert chirp |
| `success` | happy + soft chirp |
| `queued` / `skipped_busy` | neutral + badge, silent |
| `skipped_paused` / `skipped_no_match` | neutral, silent |
| no events for N s | SLEEP (dozing) |

`trigger_type` (`slack_event`/`cron`/`manual`/`jira_poll`) selects a row icon.

### D6 — Firmware home + reuse
Lives in the buddy repo as a new PlatformIO env (e.g. `cardputer-agentfarm`),
reusing character-pack art and the pet-render concept. It does **not** reuse the
BLE/serial transport or the daemon. New code: WiFi STA, HTTP client, poll loop +
cursor, and a 240×135 feed/pet renderer.

### D7 — Secrets, host discovery, and the same-LAN prerequisite
Build-time credentials in a skipped-worktree `wifi_secrets.ini` — new keys
`af_host`, `af_port` (the trigger-cursor `admin.port`, e.g. `60360`), and
`af_secret` (the `admin.secret`) — kept separate from the existing buddy-daemon
`host`/`port`. Same hygiene as the camera-gesture path; the admin secret is baked
into the firmware binary, acceptable for a personal LAN device.

**Deployment prerequisite (hard):** the Cardputer and the Agent Farm host MUST be
on the **same LAN / subnet**, because the device reaches the host by its LAN
address. Specifics:
- The Cardputer ESP32-S3 radio is **2.4 GHz only** (802.11 b/g/n) — it must join a
  2.4 GHz SSID. The host may sit on 5 GHz as long as it is the **same router /
  same subnet** (2.4 G and 5 G are bridged by default on most routers).
- The router must **not** isolate clients (disable "AP isolation" / "guest
  network isolation"), or the device and host will not see each other even on the
  same WiFi.
- Use a **static IP** (or DHCP reservation) for the host, since the address is
  baked in at build time. mDNS (`<host>.local`) is a softer alternative but adds
  latency and a failure mode; the existing `wifi_secrets.ini` comment recommends
  the raw IP, so v1 defaults to the static IP.
- Off-LAN access (host on a corp network, or you away from home) is explicitly
  **out of scope for v1** — that needs a tunnel/public endpoint (v2) or the BLE
  buddy path, both with their own security trade-offs.

## Open questions (defer past v1)
- Push transport (SSE/WS broadcast of `TriggerLog`) for instant + low-power.
- Keyboard reply / voice (mic→ASR) / approval round-trip (`block_action`).
- Whether to also surface `/api/status` (live busy counts) alongside the feed.
