## ADDED Requirements

### Requirement: Join the LAN and reach the Agent Farm admin API

The Cardputer SHALL connect as a WiFi station using build-time credentials and
SHALL resolve the Agent Farm `trigger-cursor` admin host by mDNS name or static
IP. All requests SHALL target the existing admin HTTP server (which listens on
`0.0.0.0:<admin.port>`) and SHALL NOT require any new Agent Farm endpoint.

#### Scenario: Connects and resolves host
- **WHEN** the device boots with valid WiFi credentials and a configured host
- **THEN** it joins the LAN and resolves the admin base URL
  (`http://<host>:<admin.port>`) by mDNS or static IP

#### Scenario: WiFi unavailable
- **WHEN** the WiFi network cannot be joined
- **THEN** the device shows an on-screen "offline" state and retries with backoff,
  without crashing or hanging

### Requirement: Authenticate with the admin Bearer secret

Every admin API request SHALL include `Authorization: Bearer <admin.secret>`.
The secret SHALL be provided at build time and SHALL NOT be committed to git.

#### Scenario: Authorized request
- **WHEN** the device polls the admin API
- **THEN** it sends the Bearer secret and receives the trigger log payload

#### Scenario: Rejected request
- **WHEN** the secret is missing or wrong (HTTP 401/403)
- **THEN** the device shows an "auth error" state and stops retrying tightly
  (backoff), surfacing the misconfiguration rather than hammering the server

### Requirement: Poll the trigger log on an interval

The device SHALL poll `GET /api/logs?limit=<n>` on a configurable interval
(default a few seconds) and SHALL treat the response `items` (`TriggerLog[]`) as
the source of trigger firings. The poll SHALL be read-only.

#### Scenario: Periodic poll
- **WHEN** the poll interval elapses and the host is reachable
- **THEN** the device fetches the latest log items and hands them to the feed

#### Scenario: Host unreachable mid-session
- **WHEN** a poll fails (timeout / connection refused)
- **THEN** the device marks itself offline, applies backoff, and resumes polling
  when the host returns — without losing its last-seen cursor

### Requirement: Detect new entries with a dedup cursor

The device SHALL maintain a last-seen cursor over the log and SHALL emit a "new
trigger" event only for entries newer than the cursor. On host log reset
(restart clears the in-memory log), the device SHALL re-sync to the newest entry
WITHOUT replaying or alerting on the existing backlog.

#### Scenario: Only new firings alert
- **WHEN** a poll returns entries already seen plus one newer entry
- **THEN** exactly the newer entry is emitted as new; previously seen entries are
  not re-emitted

#### Scenario: Log reset re-sync
- **WHEN** the host restarts and `/api/logs` returns a shorter / reset list
- **THEN** the device adopts the current newest entry as the cursor and does not
  alert on the pre-restart backlog
