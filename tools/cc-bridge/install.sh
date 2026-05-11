#!/usr/bin/env bash
# cc-bridge installer (macOS)
#
# Sets up:
#   1. Python venv at ~/.cc-bridge/venv with bleak
#   2. launchd agent at ~/Library/LaunchAgents/com.cc-bridge.plist
#   3. Hook entries in ~/.claude/settings.json that fire hook.py for
#      the relevant Claude Code events
#
# Idempotent — re-run any time. Won't double-install hooks; will refresh
# the venv if it exists.
#
# After install:
#   - System Settings → Bluetooth, pair the stick once (passkey shown
#     on the stick screen). bleak needs the bond.
#   - Power-cycle stick if it was previously paired with Claude Desktop.
#   - The daemon will scan and connect within ~10s.
#
# Uninstall: ./install.sh uninstall

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_ROOT="${HOME}/.cc-bridge"
VENV="${INSTALL_ROOT}/venv"
LOG_DIR="${HOME}/Library/Logs"
SOCKET_PATH="/tmp/cc-bridge.sock"
PLIST_LABEL="com.cc-bridge"
PLIST_DST="${HOME}/Library/LaunchAgents/${PLIST_LABEL}.plist"
SETTINGS="${HOME}/.claude/settings.json"

# Fire-and-forget events → hook.py (async, non-blocking).
HOOK_EVENTS_ASYNC=(
  SessionStart
  Stop
  SessionEnd
  PostToolUse
  PermissionRequest
  Notification
  UserPromptSubmit
)
# Synchronous events → hook_permission.py. PreToolUse waits up to a few
# seconds for the user to press A on the stick, then returns a Claude
# Code permissionDecision (allow/deny/ask) so the buddy can gate tools.
HOOK_EVENTS_SYNC=(
  PreToolUse
)

uninstall() {
  echo "→ unloading launchd agent"
  launchctl bootout "gui/$(id -u)/${PLIST_LABEL}" 2>/dev/null || true
  rm -f "${PLIST_DST}"
  rm -f "${SOCKET_PATH}"

  if [[ -f "${SETTINGS}" ]]; then
    echo "→ removing hook entries from ${SETTINGS}"
    for hook_path in "${HERE}/hook.py" "${HERE}/hook_permission.py"; do
      tmp="$(mktemp)"
      jq --arg path "${hook_path}" '
        .hooks //= {}
        | .hooks |= with_entries(
            .value |= map(
              .hooks |= map(select((.command // "") | contains($path) | not))
            )
            | .value |= map(select(.hooks | length > 0))
          )
      ' "${SETTINGS}" > "${tmp}" && mv "${tmp}" "${SETTINGS}"
    done
  fi
  echo "✓ uninstalled. venv at ${VENV} left in place — rm -rf manually if you want."
}

if [[ "${1:-}" == "uninstall" ]]; then
  uninstall
  exit 0
fi

# ─── 1. Python venv ────────────────────────────────────────────────────
mkdir -p "${INSTALL_ROOT}" "${LOG_DIR}"
if [[ ! -d "${VENV}" ]]; then
  echo "→ creating venv at ${VENV}"
  python3 -m venv "${VENV}"
fi
echo "→ installing bleak + pyobjc-framework-Quartz into venv"
# Quartz: needed for the mic PTT relay. Stick sends {"cmd":"mic"…} and
# daemon simulates an F5 keystroke (or CC_BRIDGE_PTT_KEYCODE) so Typeless
# or any PTT dictation app picks it up.
"${VENV}/bin/pip" install --quiet --upgrade pip bleak pyobjc-framework-Quartz

# ─── 2. launchd plist ──────────────────────────────────────────────────
echo "→ writing launchd plist to ${PLIST_DST}"
mkdir -p "$(dirname "${PLIST_DST}")"
sed \
  -e "s|__VENV_PYTHON__|${VENV}/bin/python3|g" \
  -e "s|__BRIDGE_PY__|${HERE}/bridge.py|g" \
  -e "s|__LOG_DIR__|${LOG_DIR}|g" \
  -e "s|__SOCKET_PATH__|${SOCKET_PATH}|g" \
  "${HERE}/com.cc-bridge.plist.template" > "${PLIST_DST}"

echo "→ (re)loading launchd agent"
launchctl bootout "gui/$(id -u)/${PLIST_LABEL}" 2>/dev/null || true
sleep 1   # macOS sometimes needs a beat for the prior agent to fully release.
if ! launchctl bootstrap "gui/$(id -u)" "${PLIST_DST}"; then
  echo "  ! bootstrap failed (already loaded or transient I/O). Trying kickstart."
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}" 2>/dev/null || \
    echo "  ! kickstart failed too — bring it up manually:" \
         "launchctl bootstrap gui/\$(id -u) ${PLIST_DST}"
fi

# ─── 3. patch ~/.claude/settings.json ──────────────────────────────────
if ! command -v jq >/dev/null 2>&1; then
  echo "✗ jq not found. Install with: brew install jq"
  exit 1
fi
mkdir -p "$(dirname "${SETTINGS}")"
[[ -f "${SETTINGS}" ]] || echo '{}' > "${SETTINGS}"

HOOK_CMD_ASYNC="${VENV}/bin/python3 ${HERE}/hook.py"
HOOK_CMD_SYNC="${VENV}/bin/python3 ${HERE}/hook_permission.py"

# Idempotent install: first STRIP any prior cc-bridge hook entries (under
# either hook.py or hook_permission.py) so we re-converge to whatever the
# script wires below — even if the slots changed between runs.
echo "→ stripping any prior cc-bridge hook entries"
for hook_path in "${HERE}/hook.py" "${HERE}/hook_permission.py"; do
  tmp="$(mktemp)"
  jq --arg path "${hook_path}" '
    .hooks //= {}
    | .hooks |= with_entries(
        .value |= map(
          .hooks |= map(select((.command // "") | contains($path) | not))
        )
        | .value |= map(select(.hooks | length > 0))
      )
  ' "${SETTINGS}" > "${tmp}" && mv "${tmp}" "${SETTINGS}"
done

# Sync hook gets a longer timeout because it waits for stick approval.
# Async hooks need to return fast (just forwards to a Unix socket).
add_hook() {
  local ev="$1" cmd="$2" timeout_ms="$3" is_async="$4"
  local tmp
  tmp="$(mktemp)"
  jq --arg ev "${ev}" \
     --arg cmd "${cmd}" \
     --argjson timeout "${timeout_ms}" \
     --argjson is_async "${is_async}" '
    .hooks //= {}
    | .hooks[$ev] //= []
    | if (.hooks[$ev] | map(.hooks // []) | flatten | map(.command // "") | any(. == $cmd))
      then .
      else .hooks[$ev] += [{
        "hooks": [{
          "type": "command",
          "command": $cmd,
          "timeout": $timeout,
          "async": $is_async
        }]
      }]
      end
  ' "${SETTINGS}" > "${tmp}" && mv "${tmp}" "${SETTINGS}"
}

for ev in "${HOOK_EVENTS_ASYNC[@]}"; do
  add_hook "${ev}" "${HOOK_CMD_ASYNC}" 1000 true
done
for ev in "${HOOK_EVENTS_SYNC[@]}"; do
  # Match cc-bridge's wait_permission timeout (~6s) plus headroom.
  add_hook "${ev}" "${HOOK_CMD_SYNC}" 10000 false
done
echo "→ wired async hooks: ${HOOK_EVENTS_ASYNC[*]}"
echo "→ wired sync hooks:  ${HOOK_EVENTS_SYNC[*]}"

# ─── done ──────────────────────────────────────────────────────────────
cat <<EOF

✓ cc-bridge installed.

Next steps:
  1. Pair the stick with macOS once via System Settings → Bluetooth
     (enter the 6-digit passkey shown on the stick screen).
  2. Make sure Claude Desktop's BLE bridge is OFF — only one central can
     connect to the stick at a time.
  3. Watch the daemon log:
       tail -f ${LOG_DIR}/cc-bridge.log
  4. Open Claude Code (terminal) — within ~10s the stick should react.

Tweak:
  - Change the stick name prefix:
      launchctl setenv CC_BRIDGE_DEVICE_PREFIX MyStick-
      launchctl kickstart -k gui/\$(id -u)/${PLIST_LABEL}
  - Stop the daemon:
      launchctl bootout gui/\$(id -u)/${PLIST_LABEL}
  - Uninstall everything:
      $0 uninstall
EOF
