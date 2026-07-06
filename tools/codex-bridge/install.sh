#!/usr/bin/env bash
# codex-bridge installer (macOS)
#
# Sets up:
#   1. Python venv at ~/.codex-bridge/venv with bleak + pyserial + Quartz
#   2. launchd agent at ~/Library/LaunchAgents/com.codex-bridge.plist
#   3. Hook entries in ~/.codex/hooks.json that fire codex_hook.js for the
#      Codex agent lifecycle events (SessionStart, UserPromptSubmit,
#      PreToolUse, PostToolUse, Stop, PermissionRequest, SessionEnd).
#
# codex-bridge owns NO BLE device of its own — it pushes per-session
# snapshots to cc-bridge's socket (single-BLE-owner aggregation, openspec
# change cardputer-codex-sessions). So there is no stick to pair; cc-bridge
# must be running and own the cardputer's BLE link.
#
# Codex's hooks.json uses the Claude-Code NESTED schema
#   "EventName": [ { "hooks": [ {"type":"command","command":"…","timeout":N} ] } ]
# (NOT cursor's flat schema), so the jq merge below targets that shape. Other
# tools (vibe-island, oh-my-codex, termcanvas) share this file, so we MERGE
# (append our group) and back up before every edit — never replace.
#
# ⚠️ Hook trust: Codex requires each hook command to be trusted before it runs
# (a persisted sha256 in hooks.json "state", or the interactive "trust this
# hook?" prompt on first run, or `codex --dangerously-bypass-hook-trust` for
# automation). After install, run codex once interactively and approve the
# codex_hook.js trust prompt, or the bridge will see no events.
#
# NOTE: the permission echo (device button → allow/deny) is deferred — this
# installer wires only the fire-and-forget display hook (codex_hook.js). The
# PermissionRequest event is wired async too, so the device still SHOWS the
# waiting state; it just doesn't gate Codex yet. (openspec cardputer-codex-sessions)
#
# Idempotent — re-run any time. Uninstall: ./install.sh uninstall

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_ROOT="${HOME}/.codex-bridge"
VENV="${INSTALL_ROOT}/venv"
LOG_DIR="${HOME}/Library/Logs"
SOCKET_PATH="/tmp/codex-bridge.sock"
PLIST_LABEL="com.codex-bridge"
PLIST_DST="${HOME}/Library/LaunchAgents/${PLIST_LABEL}.plist"
HOOKS_JSON="${HOME}/.codex/hooks.json"

# Fire-and-forget Codex hook events → codex_hook.js. Codex event names are
# already Claude-Code-shaped, so the daemon's apply_event consumes them directly.
HOOK_EVENTS_ASYNC=(
  SessionStart
  UserPromptSubmit
  PreToolUse
  PostToolUse
  Stop
  PermissionRequest
  SessionEnd
)
HOOK_BASENAME="codex_hook.js"
ASYNC_HOOK_TIMEOUT_S=5

# ─── helpers ───────────────────────────────────────────────────────────
require_jq() {
  if ! command -v jq >/dev/null 2>&1; then
    echo "✗ jq not found. Install with: brew install jq"; exit 1
  fi
}

detect_node() {
  if command -v node >/dev/null 2>&1; then command -v node
  elif [[ -x /opt/homebrew/bin/node ]]; then echo "/opt/homebrew/bin/node"
  elif [[ -x /usr/local/bin/node ]]; then echo "/usr/local/bin/node"
  else echo "✗ node not found. brew install node and re-run." >&2; exit 1; fi
}

backup_hooks_json() {
  if [[ -f "${HOOKS_JSON}" ]]; then
    local bak="${HOOKS_JSON}.bak.$(date +%s)"
    cp "${HOOKS_JSON}" "${bak}"
    echo "→ backed up ${HOOKS_JSON} → ${bak}"
  fi
}

# Strip any group whose inner hooks reference our script (by basename), then
# drop now-empty event arrays. Nested Claude-Code schema.
strip_our_hooks() {
  require_jq
  local tmp
  tmp="$(mktemp)"
  jq --arg p "${HOOK_BASENAME}" '
    .hooks //= {}
    | .hooks |= with_entries(
        .value |= map(select(
          ([.hooks[]?.command // ""] | any(contains($p))) | not
        ))
      )
    | .hooks |= with_entries(select(.value | length > 0))
  ' "${HOOKS_JSON}" > "${tmp}" && mv "${tmp}" "${HOOKS_JSON}"
}

# ─── uninstall ─────────────────────────────────────────────────────────
uninstall() {
  echo "→ unloading launchd agent"
  launchctl bootout "gui/$(id -u)/${PLIST_LABEL}" 2>/dev/null || true
  rm -f "${PLIST_DST}" "${SOCKET_PATH}"
  if [[ -f "${HOOKS_JSON}" ]]; then
    backup_hooks_json
    echo "→ stripping codex-bridge hook entries from ${HOOKS_JSON}"
    strip_our_hooks
  fi
  echo "✓ uninstalled. venv at ${VENV} left in place — rm -rf manually if you want."
}

if [[ "${1:-}" == "uninstall" ]]; then uninstall; exit 0; fi

# ─── 1. Python venv ────────────────────────────────────────────────────
mkdir -p "${INSTALL_ROOT}" "${LOG_DIR}"
if [[ ! -d "${VENV}" ]]; then
  echo "→ creating venv at ${VENV}"
  python3 -m venv "${VENV}"
fi
echo "→ installing bleak + pyserial + pyobjc-framework-Quartz into venv"
"${VENV}/bin/pip" install --quiet --upgrade pip bleak pyserial pyobjc-framework-Quartz

# ─── 2. launchd plist ──────────────────────────────────────────────────
echo "→ writing launchd plist to ${PLIST_DST}"
mkdir -p "$(dirname "${PLIST_DST}")"
sed \
  -e "s|__VENV_PYTHON__|${VENV}/bin/python3|g" \
  -e "s|__BRIDGE_PY__|${HERE}/bridge.py|g" \
  -e "s|__LOG_DIR__|${LOG_DIR}|g" \
  -e "s|__SOCKET_PATH__|${SOCKET_PATH}|g" \
  "${HERE}/com.codex-bridge.plist.template" > "${PLIST_DST}"

echo "→ (re)loading launchd agent"
launchctl bootout "gui/$(id -u)/${PLIST_LABEL}" 2>/dev/null || true
sleep 1
if ! launchctl bootstrap "gui/$(id -u)" "${PLIST_DST}"; then
  echo "  ! bootstrap failed; trying kickstart."
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}" 2>/dev/null || \
    echo "  ! kickstart failed — bring it up manually:" \
         "launchctl bootstrap gui/\$(id -u) ${PLIST_DST}"
fi

# ─── 3. patch ~/.codex/hooks.json (nested Claude-Code schema) ──────────
require_jq
NODE_BIN="$(detect_node)"
echo "→ using node: ${NODE_BIN}"
mkdir -p "$(dirname "${HOOKS_JSON}")"
[[ -f "${HOOKS_JSON}" ]] || echo '{ "hooks": {} }' > "${HOOKS_JSON}"
chmod +x "${HERE}/${HOOK_BASENAME}" 2>/dev/null || true

backup_hooks_json
echo "→ stripping any prior codex-bridge hook entries"
strip_our_hooks

HOOK_CMD="\"${NODE_BIN}\" \"${HERE}/${HOOK_BASENAME}\""

# Append our group to an event if no existing group already references our cmd.
add_hook() {
  local ev="$1" cmd="$2" timeout="$3"
  local tmp; tmp="$(mktemp)"
  jq --arg ev "${ev}" --arg cmd "${cmd}" --arg t "${timeout}" '
    .hooks //= {}
    | .hooks[$ev] //= []
    | if (.hooks[$ev] | map(.hooks[]?.command // "") | any(. == $cmd))
      then .
      else .hooks[$ev] += [ { "hooks": [
             { "type": "command", "command": $cmd, "timeout": ($t | tonumber) }
           ] } ]
      end
  ' "${HOOKS_JSON}" > "${tmp}" && mv "${tmp}" "${HOOKS_JSON}"
}

for ev in "${HOOK_EVENTS_ASYNC[@]}"; do
  add_hook "${ev}" "${HOOK_CMD}" "${ASYNC_HOOK_TIMEOUT_S}"
done
echo "→ wired async hooks: ${HOOK_EVENTS_ASYNC[*]}"

cat <<EOF

✓ codex-bridge installed.
  • daemon:  launchctl print gui/$(id -u)/${PLIST_LABEL}
  • log:     tail -f ${LOG_DIR}/codex-bridge.log
  • socket:  ${SOCKET_PATH}  (pushes ext_sessions → cc-bridge)

Next:
  1. cc-bridge must be running (it owns the cardputer BLE link).
  2. Run codex once interactively and APPROVE the codex_hook.js trust prompt
     (Codex won't run an untrusted hook). Then fire any action.
  3. Open a Codex pane in cmux — the cardputer session list should show it
     with a green "cx" marker within ~15s (cmux cwd reconcile).
EOF
