// Cardputer permission gating for OpenCode.
//
// Wires OpenCode's `permission.asked` event to the cc-bridge unix socket
// (the same `_handle_wait_permission` RPC cc-bridge and cursor-bridge both
// use). cc-bridge owns the only BLE connection to the cardputer, shows the
// approval prompt with an "oc" tag, blocks the socket until the user
// presses a button or the device's approval-safety timer fires, then
// replies with the decision. We translate the device decision into
// OpenCode's REST `/permission/{id}/reply` shape and POST it.
//
// Out of scope for this task: question.asked. Firmeware + python daemon
// are unchanged; this plugin only adds an OpenCode-side shim.

import { Socket } from "net";

const SOCKET = process.env.CC_BRIDGE_SOCKET || "/tmp/cc-bridge.sock";

// Socket helper adapted from ~/.config/opencode/plugins/vibe-island.js
// (the canonical "open a unix socket, write JSON, wait for one
// JSON line back on the same connection" helper). Only the SOCKET
// constant is overridden via CC_BRIDGE_SOCKET.
//
// One wire-protocol adaptation vs the verbatim copy: the cc-bridge
// daemon reads with `await reader.readuntil(b"\n")` (see
// tools/buddy_core/core.py:handle_client), so each request frame
// MUST be newline-terminated or readuntil blocks until the
// 30 s socket timeout. Other clients (cc-bridge/hook_permission.py,
// cursor-bridge/cursor_hook_permission.js) all append "\n"; we
// match that convention.
function sendAndWaitResponse(json, timeoutMs = 30000) {
  return new Promise((resolve) => {
    let settled = false;
    const settle = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    try {
      const sock = new Socket();
      let buf = "";
      sock.on("error", () => settle(null));
      sock.on("data", (data) => { buf += data.toString(); });
      sock.on("end", () => {
        try { settle(JSON.parse(buf)); } catch { settle(null); }
      });
      sock.on("connect", () => {
        try {
          sock.write(JSON.stringify(json) + "\n");
        } catch {
          sock.destroy();
          settle(null);
        }
      });
      sock.setTimeout(timeoutMs, () => { sock.destroy(); settle(null); });
      sock.connect({ path: SOCKET });
    } catch { settle(null); }
  });
}

// Decision table from the spec:
//
//   device decision | OpenCode reply | action
//   once            | once           | POST reply
//   always          | always         | POST reply
//   deny            | reject         | POST reply
//   ask / null / timeout | —        | do NOT POST (let OpenCode prompt)
function decisionToReply(decision) {
  if (decision === "once") return "once";
  if (decision === "always") return "always";
  if (decision === "deny") return "reject";
  return null;
}

function deriveHint(permission, patterns) {
  const list = Array.isArray(patterns) ? patterns : [];
  if (permission === "bash" && list.length > 0) {
    return list.join(" && ");
  }
  if ((permission === "edit" || permission === "write") && list.length > 0) {
    return list[0];
  }
  return "";
}

function truncate(str, max) {
  const s = (str == null ? "" : String(str));
  return s.length > max ? s.slice(0, max) : s;
}

export default async ({ client, serverUrl }) => {
  const serverOrigin = serverUrl?.origin || "http://127.0.0.1:4096";
  let lastSessionId = null;

  async function postReply(requestId, reply) {
    const path = `/permission/${encodeURIComponent(requestId)}/reply`;
    try {
      const response = await fetch(`${serverOrigin}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reply }),
      });
      if (response && response.ok) return { ok: true, via: "fetch" };
    } catch {}
    try {
      const rawClient = client?._client;
      if (!rawClient?.post) return { ok: false, via: null };
      await rawClient.post({
        url: path,
        headers: { "Content-Type": "application/json" },
        body: { reply },
      });
      return { ok: true, via: "client" };
    } catch {
      return { ok: false, via: null };
    }
  }

  async function handlePermissionAsked(props) {
    if (!props || !props.id) return;
    const tool = props.permission || "tool";
    const hint = truncate(deriveHint(props.permission, props.patterns), 120);
    const sid = props.sessionID || lastSessionId || "anon";
    const requestId = props.id;

    const payload = {
      action: "wait_permission",
      id: requestId,
      tool,
      hint,
      session_id: sid,
      agent: "opencode",
      timeout: 28,
    };

    const resp = await sendAndWaitResponse(payload, 30000);
    const decision = resp && typeof resp === "object" ? resp.decision : null;
    const reply = decisionToReply(decision);
    if (!reply) return; // ask / null / timeout → let OpenCode prompt
    await postReply(requestId, reply);
  }

  // Fire-and-forget: never block OpenCode's event tick.
  function asyncFire(promise) {
    promise.catch(() => {});
  }

  return {
    event: async ({ event }) => {
      const t = event?.type;
      if (t === "permission.asked") {
        asyncFire(handlePermissionAsked(event.properties || {}));
        return;
      }
      // Track lastSessionId so we can fill in for older OpenCode versions
      // that don't pass sessionID on permission.asked.
      const props = event?.properties || {};
      const sid = props.sessionID
        || props?.info?.sessionID
        || (props?.info?.id && props?.info?.id);
      if (sid && typeof sid === "string") lastSessionId = sid;
    },
  };
};
