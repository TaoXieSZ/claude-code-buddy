#!/usr/bin/env node
//
// Codex CLI hook → codex-bridge daemon shim.
//
// Codex fires this for each registered hook event. Unlike Cursor (whose hook
// schema needed real translation), Codex's hook payload is ALREADY
// Claude-Code-shaped: hook_event_name is one of SessionStart / UserPromptSubmit
// / PreToolUse / PostToolUse / Stop / PermissionRequest, and the fields
// (session_id, cwd, tool_name, tool_input, prompt, last_assistant_message) are
// exactly what bridge.py's apply_event() reads. So this is a near-identity
// forwarder — we whitelist the fields we care about and write one JSON line to
// the bridge's Unix socket, then exit immediately.
//
// Any failure (daemon down, socket missing, bad JSON, etc.) exits 0 silently —
// the hook MUST NOT block Codex on a side channel that may be offline.
//
// Permission echo (button-on-device allow/deny) lives in a sibling file,
// codex_hook_permission.js, wired to the PermissionRequest event. This script
// stays fire-and-forget for everything else.
//
// Wired up by tools/codex-bridge/install.sh into ~/.codex/hooks.json.

'use strict';

const fs  = require('fs');
const net = require('net');

const SOCKET_PATH = process.env.CODEX_BRIDGE_SOCKET || '/tmp/codex-bridge.sock';
const TIMEOUT_MS  = 500; // never slow Codex down for our side channel

const KNOWN = new Set([
    'SessionStart', 'SessionEnd', 'UserPromptSubmit', 'Stop',
    'PreToolUse', 'PostToolUse', 'PermissionRequest', 'PostCompact',
]);

// ─── Codex → Claude-Code-shaped event (near-identity) ──────────────────
function translate(ev) {
    const name = ev.hook_event_name || ev.event || '';
    if (!KNOWN.has(name)) return null;   // drop unknown events silently

    const sid = ev.session_id || ev.sessionId || ev.conversation_id || 'anon';
    const cwd = ev.cwd || ev.workspace || '';
    const out = { hook_event_name: name, session_id: sid };
    if (cwd) out.cwd = String(cwd);

    if (name === 'UserPromptSubmit') {
        const prompt = ev.prompt || ev.user_prompt || ev.text || '';
        if (prompt) out.prompt = String(prompt).slice(0, 200);
    } else if (name === 'Stop') {
        const txt = ev.last_assistant_message || ev.text || '';
        if (txt) out.last_assistant_message = String(txt).slice(0, 200);
    } else if (name === 'PreToolUse' || name === 'PostToolUse' || name === 'PermissionRequest') {
        out.tool_name = ev.tool_name || ev.tool || 'tool';
        const ti = ev.tool_input || {};
        // Keep only the small descriptive bits apply_event surfaces.
        const slim = {};
        if (ti.command)     slim.command     = String(ti.command).slice(0, 200);
        if (ti.description) slim.description = String(ti.description).slice(0, 120);
        if (ti.file_path)   slim.file_path   = String(ti.file_path).slice(0, 200);
        if (Object.keys(slim).length) out.tool_input = slim;
        if (ev.tool_use_id) out.tool_use_id = ev.tool_use_id;
    }
    return out;
}

// ─── main ──────────────────────────────────────────────────────────────
function main() {
    let raw;
    try {
        raw = fs.readFileSync(0, 'utf8'); // stdin
    } catch (_) {
        process.exit(0);
    }
    if (!raw) process.exit(0);

    let ev;
    try {
        ev = JSON.parse(raw);
    } catch (_) {
        process.exit(0);
    }

    if (process.env.CODEX_HOOK_DEBUG === '1') {
        try {
            fs.appendFileSync(
                '/tmp/codex-hook-debug.jsonl',
                JSON.stringify({ ts: Date.now(), ev }) + '\n'
            );
        } catch (_) {}
    }

    const translated = translate(ev);
    if (!translated) process.exit(0);

    const payload = JSON.stringify(translated) + '\n';

    const sock = net.createConnection(SOCKET_PATH);
    let done = false;
    const finish = () => {
        if (done) return;
        done = true;
        try { sock.end(); } catch (_) {}
        process.exit(0);
    };
    sock.setTimeout(TIMEOUT_MS, finish);
    sock.on('error',   finish);
    sock.on('connect', () => {
        try {
            sock.write(payload, finish);
        } catch (_) {
            finish();
        }
    });

    setTimeout(finish, TIMEOUT_MS + 100).unref();
}

main();
