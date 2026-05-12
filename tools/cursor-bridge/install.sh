#!/usr/bin/env bash
# cursor-bridge installer (macOS)
#
# Sets up:
#   1. Python venv at ~/.cursor-bridge/venv with bleak
#   2. launchd agent at ~/Library/LaunchAgents/com.cursor-bridge.plist
#   3. Hook entries in ~/.cursor/hooks.json that fire cursor_hook.js for
#      the relevant Cursor agent events (sessionStart, beforeSubmitPrompt,
#      beforeShellExecution, beforeMCPExecution, beforeReadFile,
#      afterShellExecution, afterMCPExecution, afterFileEdit,
#      afterAgentResponse, stop, sessionEnd).
#
# Idempotent — re-run any time. Won't double-install hooks; will refresh
# the venv if it exists. Always backs up ~/.cursor/hooks.json before
# editing it (other tools — vibe-island, ahakey, omc, omr,
# clawd-on-desk — share that file, so we merge instead of replace).
#
# After install:
#   - Pair the second stick with macOS once (System Settings → Bluetooth,
#     enter the 6-digit passkey shown on the stick screen). bleak needs
#     the bond.
#   - With the -cursor firmware variant the stick advertises as
#     "Cursor-XXXX" and this daemon scans for the "Cursor-" prefix by
#     default, so it won't race cc-bridge (which scans "Claude-"). No
#     manual pinning needed in the common case. Only if both sticks
#     happen to advertise the same prefix (e.g. you flashed both with
#     the plain `m5stickc-plus2` env), pin by MAC suffix:
#       launchctl setenv CURSOR_BRIDGE_DEVICE_PREFIX Cursor-6DE2
#       launchctl kickstart -k gui/$(id -u)/com.cursor-bridge
#   - Open Cursor, fire any agent action — within ~10s the stick should
#     react.
#
# Uninstall: ./install.sh uninstall

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_ROOT="${HOME}/.cursor-bridge"
VENV="${INSTALL_ROOT}/venv"
LOG_DIR="${HOME}/Library/Logs"
SOCKET_PATH="/tmp/cursor-bridge.sock"
PLIST_LABEL="com.cursor-bridge"
PLIST_DST="${HOME}/Library/LaunchAgents/${PLIST_LABEL}.plist"
HOOKS_JSON="${HOME}/.cursor/hooks.json"

# All Cursor hook events we wire. v1 = fire-and-forget only; permission
# echo (button-on-stick allow/deny) is intentionally not implemented yet
# because Cursor's permission protocol differs from Claude Code's
# hookSpecificOutput shape.
HOOK_EVENTS=(
  sessionStart
  beforeSubmitPrompt
  beforeShellExecution
  beforeMCPExecution
  beforeReadFile
  afterShellExecution
  afterMCPExecution
  afterFileEdit
  afterAgentResponse
  stop
  sessionEnd
)

# ─── helpers ───────────────────────────────────────────────────────────
require_jq() {
  if ! command -v jq >/dev/null 2>&1; then
    echo "✗ jq not found. Install with: brew install jq"
    exit 1
  fi
}

detect_node() {
  if command -v node >/dev/null 2>&1; then
    command -v node
  elif [[ -x /opt/homebrew/bin/node ]]; then
    echo "/opt/homebrew/bin/node"
  elif [[ -x /usr/local/bin/node ]]; then
    echo "/usr/local/bin/node"
  else
    echo "✗ node not found. Install Node.js (brew install node) and re-run." >&2
    exit 1
  fi
}

backup_hooks_json() {
  if [[ -f "${HOOKS_JSON}" ]]; then
    local bak="${HOOKS_JSON}.bak.$(date +%s)"
    cp "${HOOKS_JSON}" "${bak}"
    echo "→ backed up ${HOOKS_JSON} → ${bak}"
  fi
}

# ─── uninstall ─────────────────────────────────────────────────────────
uninstall() {
  echo "→ unloading launchd agent"
  launchctl bootout "gui/$(id -u)/${PLIST_LABEL}" 2>/dev/null || true
  rm -f "${PLIST_DST}"
  rm -f "${SOCKET_PATH}"

  if [[ -f "${HOOKS_JSON}" ]]; then
    require_jq
    backup_hooks_json
    echo "→ stripping cursor-bridge hook entries from ${HOOKS_JSON}"
    local hook_path="${HERE}/cursor_hook.js"
    local tmp
    tmp="$(mktemp)"
    # Cursor hooks.json shape: { "hooks": { "<event>": [ {command, timeout?}, ... ] } }
    # Remove any entry whose .command contains our cursor_hook.js path,
    # then drop now-empty event arrays.
    jq --arg p "${hook_path}" '
      .hooks //= {}
      | .hooks |= with_entries(
          .value |= map(select((.command // "") | contains($p) | not))
        )
      | .hooks |= with_entries(select(.value | length > 0))
    ' "${HOOKS_JSON}" > "${tmp}" && mv "${tmp}" "${HOOKS_JSON}"
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
  "${HERE}/com.cursor-bridge.plist.template" > "${PLIST_DST}"

echo "→ (re)loading launchd agent"
launchctl bootout "gui/$(id -u)/${PLIST_LABEL}" 2>/dev/null || true
sleep 1   # macOS sometimes needs a beat for the prior agent to fully release.
if ! launchctl bootstrap "gui/$(id -u)" "${PLIST_DST}"; then
  echo "  ! bootstrap failed (already loaded or transient I/O). Trying kickstart."
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}" 2>/dev/null || \
    echo "  ! kickstart failed too — bring it up manually:" \
         "launchctl bootstrap gui/\$(id -u) ${PLIST_DST}"
fi

# ─── 3. patch ~/.cursor/hooks.json ─────────────────────────────────────
require_jq
NODE_BIN="$(detect_node)"
echo "→ using node: ${NODE_BIN}"

mkdir -p "$(dirname "${HOOKS_JSON}")"
[[ -f "${HOOKS_JSON}" ]] || echo '{ "version": 1, "hooks": {} }' > "${HOOKS_JSON}"

# Make sure cursor_hook.js is executable (we don't actually rely on its
# shebang since we invoke node explicitly, but keep it tidy).
chmod +x "${HERE}/cursor_hook.js" 2>/dev/null || true

backup_hooks_json

# Idempotent install: first STRIP any prior cursor_hook.js entries so we
# re-converge to whatever the script wires below — even if the slot list
# changed between runs. Other consumers (vibe-island, ahakey, omc, omr,
# clawd-on-desk) are left intact because we only filter rows whose
# .command contains OUR cursor_hook.js path.
HOOK_PATH="${HERE}/cursor_hook.js"
echo "→ stripping any prior cursor-bridge hook entries"
tmp="$(mktemp)"
jq --arg p "${HOOK_PATH}" '
  .hooks //= {}
  | .hooks |= with_entries(
      .value |= map(select((.command // "") | contains($p) | not))
    )
' "${HOOKS_JSON}" > "${tmp}" && mv "${tmp}" "${HOOKS_JSON}"

# Ensure top-level "version" stays present (Cursor expects it).
tmp="$(mktemp)"
jq '.version //= 1' "${HOOKS_JSON}" > "${tmp}" && mv "${tmp}" "${HOOKS_JSON}"

# Cursor hook command shape: { "command": "<node-path> <hook-script>" }
# We single-quote-wrap each part so paths with spaces stay intact.
HOOK_CMD="\"${NODE_BIN}\" \"${HOOK_PATH}\""

add_hook() {
  local ev="$1" cmd="$2"
  local tmp
  tmp="$(mktemp)"
  jq --arg ev "${ev}" --arg cmd "${cmd}" '
    .hooks //= {}
    | .hooks[$ev] //= []
    | if (.hooks[$ev] | map(.command // "") | any(. == $cmd))
      then .
      else .hooks[$ev] += [{"command": $cmd}]
      end
  ' "${HOOKS_JSON}" > "${tmp}" && mv "${tmp}" "${HOOKS_JSON}"
}

for ev in "${HOOK_EVENTS[@]}"; do
  add_hook "${ev}" "${HOOK_CMD}"
done
echo "→ wired hooks: ${HOOK_EVENTS[*]}"

# ─── done ──────────────────────────────────────────────────────────────
cat <<EOF

✓ cursor-bridge installed.

Next steps:
  1. Flash stick #2 with the -cursor firmware variant (firmware + character pack):
       pio run -e m5stickc-plus2-cursor -t upload -t uploadfs \\
              --upload-port /dev/cu.usbserial-XXXXXX
     This builds a binary that advertises as "Cursor-XXXX" (vs cc-bridge's
     "Claude-XXXX") and ships with the clawd character pack as default.
  2. Pair the stick with macOS via System Settings → Bluetooth (enter the
     6-digit passkey shown on the stick screen). One-time bond per Mac.
  3. Watch the daemon log:
       tail -f ${LOG_DIR}/cursor-bridge.log
     You should see "scanning for stick (prefix=Cursor-)" → "connecting to
     Cursor-XXXX" within ~10s of the launchd agent starting.
  4. Open Cursor, run any agent action — within ~10s the stick should
     react.

  (If you flashed stick #2 with the plain m5stickc-plus2 env instead, it
  still advertises as Claude-XXXX; in that case pin by MAC suffix:
       launchctl setenv CURSOR_BRIDGE_DEVICE_PREFIX Claude-XXXX
       launchctl kickstart -k gui/\$(id -u)/${PLIST_LABEL})

Tweak:
  - Disable temporarily:
      launchctl bootout gui/\$(id -u)/${PLIST_LABEL}
  - Uninstall everything:
      $0 uninstall
  - The original ~/.cursor/hooks.json was backed up next to the file
    with a timestamp suffix — restore it if anything looks off.
EOF
