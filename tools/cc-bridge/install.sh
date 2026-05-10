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

# Hook events we care about. PreToolUse/PostToolUse without a matcher
# fires for all tools.
HOOK_EVENTS=(
  SessionStart
  Stop
  SessionEnd
  PreToolUse
  PostToolUse
  PermissionRequest
  Notification
  UserPromptSubmit
)

uninstall() {
  echo "→ unloading launchd agent"
  launchctl bootout "gui/$(id -u)/${PLIST_LABEL}" 2>/dev/null || true
  rm -f "${PLIST_DST}"
  rm -f "${SOCKET_PATH}"

  if [[ -f "${SETTINGS}" ]]; then
    local hook_cmd_path="${HERE}/hook.py"
    echo "→ removing hook entries from ${SETTINGS}"
    tmp="$(mktemp)"
    jq --arg path "${hook_cmd_path}" '
      .hooks //= {}
      | .hooks |= with_entries(
          .value |= map(
            .hooks |= map(select((.command // "") | contains($path) | not))
          )
          | .value |= map(select(.hooks | length > 0))
        )
    ' "${SETTINGS}" > "${tmp}" && mv "${tmp}" "${SETTINGS}"
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
echo "→ installing bleak into venv"
"${VENV}/bin/pip" install --quiet --upgrade pip bleak

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
launchctl bootstrap "gui/$(id -u)" "${PLIST_DST}"

# ─── 3. patch ~/.claude/settings.json ──────────────────────────────────
if ! command -v jq >/dev/null 2>&1; then
  echo "✗ jq not found. Install with: brew install jq"
  exit 1
fi
mkdir -p "$(dirname "${SETTINGS}")"
[[ -f "${SETTINGS}" ]] || echo '{}' > "${SETTINGS}"

HOOK_CMD="${VENV}/bin/python3 ${HERE}/hook.py"

for ev in "${HOOK_EVENTS[@]}"; do
  tmp="$(mktemp)"
  jq --arg ev "${ev}" --arg cmd "${HOOK_CMD}" '
    .hooks //= {}
    | .hooks[$ev] //= []
    # Only add if no existing entry references this exact command path.
    | if (.hooks[$ev] | map(.hooks // []) | flatten | map(.command // "") | any(. == $cmd))
      then .
      else .hooks[$ev] += [{
        "hooks": [{
          "type": "command",
          "command": $cmd,
          "timeout": 1000,
          "async": true
        }]
      }]
      end
  ' "${SETTINGS}" > "${tmp}" && mv "${tmp}" "${SETTINGS}"
done
echo "→ wired hooks for: ${HOOK_EVENTS[*]}"

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
