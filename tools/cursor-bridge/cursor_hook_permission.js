#!/usr/bin/env node
//
// Cursor IDE pre-execution hook → cursor-bridge daemon (synchronous).
//
// Mirror of tools/cc-bridge/hook_permission.py, but speaks Cursor's hook
// response shape instead of Claude Code's hookSpecificOutput.
//
// Wired by tools/cursor-bridge/install.sh into ~/.cursor/hooks.json under
// the gateable pre-execution events (currently: beforeShellExecution,
// beforeMCPExecution). Non-gateable / async events stay on cursor_hook.js.
//
// Wire protocol (matches cc-bridge):
//   stdout JSON: {"action":"wait_permission","id":<rid>,"tool":<name>,
//                 "hint":<str>,"timeout":<sec>}  → /tmp/cursor-bridge.sock
//   stdin JSON:  {"decision":"once"|"always"|"deny"|"ask"}
//
// Cursor's pre-event response shape (per https://cursor.com/docs/hooks):
//   {"permission":"allow"|"deny"|"ask",
//    "user_message":"<shown in client>",
//    "agent_message":"<shown to agent>"}
//   - exit 0 + JSON above
//   - exit 2 = deny shortcut
//   - other exit = fail-open (action proceeds) unless `failClosed: true`
//
// Decision mapping:
//   stick "once"   → permission "allow"
//   stick "always" → permission "allow"  (no per-tool memory yet on stick side)
//   stick "deny"   → permission "deny"
//   stick "ask"    → no output (exit 0 with empty `{}`) → fail-open, Cursor
//                    falls back to its default permission flow.
//
// The hook MUST exit cleanly within `CURSOR_BRIDGE_PERMISSION_TIMEOUT_S +
// headroom` even if the daemon is down — never block Cursor on a side
// channel that may be temporarily offline.

'use strict';

const fs   = require('fs');
const net  = require('net');

const SOCKET_PATH = process.env.CURSOR_BRIDGE_SOCKET || '/tmp/cursor-bridge.sock';
const TIMEOUT_S   = Number(process.env.CURSOR_BRIDGE_PERMISSION_TIMEOUT_S || 8);
const ECHO_ENABLED = (process.env.CURSOR_BRIDGE_PERMISSION_ECHO || '1') !== '0';

// Hard cap on the whole hook process. Daemon's wait_permission timeout +
// socket round-trip + parse. Stays well under Cursor's per-script timeout
// in hooks.json (12s) so the script always exits before Cursor kills it.
const HOOK_CAP_MS = Math.round((TIMEOUT_S + 2) * 1000);

// ─── helpers ───────────────────────────────────────────────────────────

function emitNoop() {
    // No JSON body. Exit 0. Cursor treats no permission field as "no
    // decision from this hook", falls through to its default flow.
    process.exit(0);
}

function emitDecision(perm, reason) {
    const body = { permission: perm };
    if (reason) {
        body.user_message = reason;
        body.agent_message = reason;
    }
    process.stdout.write(JSON.stringify(body) + '\n');
    process.exit(0);
}

// Translate the Cursor pre-event payload into the (tool, hint) we surface
// on the stick. Returns null for events we don't gate.
function describe(ev) {
    const name = ev.hook_event_name || '';

    if (name === 'beforeShellExecution') {
        const cmd = String(ev.command || '').slice(0, 200);
        return { tool: 'shell', hint: cmd };
    }

    if (name === 'beforeMCPExecution') {
        // tool_input is documented as a JSON params string but in
        // practice can be either a string or an object. Defensive.
        const tool = String(ev.tool_name || 'mcp').slice(0, 40);
        let ti = ev.tool_input;
        if (typeof ti === 'string') {
            try { ti = JSON.parse(ti); } catch (_) { /* leave as string */ }
        }
        const hintParts = [];
        if (ev.command) hintParts.push(String(ev.command));
        if (ev.url)     hintParts.push(String(ev.url));
        if (ti && typeof ti === 'object') {
            // Pull a few common parameter names that summarize intent.
            for (const k of ['path', 'file_path', 'query', 'url', 'cmd',
                             'command', 'name', 'message']) {
                if (typeof ti[k] === 'string' && ti[k]) {
                    hintParts.push(`${k}=${ti[k]}`);
                    break;
                }
            }
        }
        const hint = hintParts.join(' ').slice(0, 200);
        return { tool: `mcp:${tool}`, hint };
    }

    return null;
}

// ─── main ──────────────────────────────────────────────────────────────

function main() {
    if (!ECHO_ENABLED) {
        emitNoop();
        return;
    }

    let raw;
    try {
        raw = fs.readFileSync(0, 'utf8');
    } catch (_) {
        emitNoop();
        return;
    }
    if (!raw) { emitNoop(); return; }

    let ev;
    try {
        ev = JSON.parse(raw);
    } catch (_) {
        emitNoop();
        return;
    }

    if (process.env.CURSOR_HOOK_DEBUG === '1') {
        try {
            fs.appendFileSync(
                '/tmp/cursor-hook-debug.jsonl',
                JSON.stringify({ ts: Date.now(), gate: true, ev }) + '\n'
            );
        } catch (_) {}
    }

    const desc = describe(ev);
    if (!desc) { emitNoop(); return; }

    const sid = String(ev.conversation_id || ev.session_id || 'anon').slice(0, 8);
    const rid = `cursor_${sid}_${Date.now()}`;

    const req = {
        action: 'wait_permission',
        id:      rid,
        tool:    desc.tool,
        hint:    desc.hint,
        timeout: TIMEOUT_S,
    };

    // Hard process-level cap so we can never hang Cursor.
    const hardStop = setTimeout(() => emitNoop(), HOOK_CAP_MS).unref();

    const sock = net.createConnection(SOCKET_PATH);
    let buf = '';
    let done = false;

    const finish = (decision) => {
        if (done) return;
        done = true;
        clearTimeout(hardStop);
        try { sock.end(); } catch (_) {}
        // Map stick decision → Cursor permission shape.
        if (decision === 'once' || decision === 'always') {
            emitDecision('allow', `buddy stick: ${decision}`);
        } else if (decision === 'deny') {
            emitDecision('deny', 'buddy stick: deny');
        } else {
            // "ask" / unknown / null → no opinion, let Cursor handle.
            emitNoop();
        }
    };

    // Socket-level deadline (slightly more than the daemon's wait_for).
    sock.setTimeout(HOOK_CAP_MS - 200, () => finish(null));
    sock.on('error',   () => finish(null));

    sock.on('connect', () => {
        try {
            sock.write(JSON.stringify(req) + '\n');
        } catch (_) {
            finish(null);
        }
    });

    sock.on('data', (chunk) => {
        buf += chunk.toString('utf8');
        const nl = buf.indexOf('\n');
        if (nl === -1) return;
        const line = buf.slice(0, nl).trim();
        try {
            const obj = JSON.parse(line);
            finish(obj.decision || null);
        } catch (_) {
            finish(null);
        }
    });

    sock.on('close', () => {
        if (done) return;
        // Server closed without a complete line — fall through.
        finish(null);
    });
}

main();
