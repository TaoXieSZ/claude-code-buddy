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
// Client-side matcher (CURSOR_BRIDGE_PERMISSION_MATCHER, default on):
//   For `beforeShellExecution`, low-risk read-only commands (ls / cat /
//   head / pwd / git status / etc.) auto-allow without bothering the
//   stick OR the daemon — keeps the approval queue focused on commands
//   that actually mutate state or have side effects. See SAFE_SHELL_*
//   sets below for the exact safelist; any shell metacharacter (pipe,
//   redirect, substitution, chain) hard-rejects the match so the command
//   falls through to the stick.
//
// Sentinel-file kill switch (no Cursor/daemon restart needed):
//   `touch /tmp/cursor-bridge-echo-off` — silence stick prompts entirely
//                                         (matcher still runs, but
//                                         non-matching commands no-op
//                                         instead of going to the stick).
//   `rm    /tmp/cursor-bridge-echo-off` — re-enable.
//   Useful when Cursor's own bypass-permission toggle is on and you don't
//   want a duplicate gate on the stick. Cursor doesn't expose its bypass
//   state to hooks (verified May 2026, cursor 3.3.30), so this is the
//   manual escape hatch.
//
// The hook MUST exit cleanly within `CURSOR_BRIDGE_PERMISSION_TIMEOUT_S +
// headroom` even if the daemon is down — never block Cursor on a side
// channel that may be temporarily offline.

'use strict';

const fs   = require('fs');
const net  = require('net');

const SOCKET_PATH = process.env.CURSOR_BRIDGE_SOCKET || '/tmp/cursor-bridge.sock';
const TIMEOUT_S   = Number(process.env.CURSOR_BRIDGE_PERMISSION_TIMEOUT_S || 8);

// Sentinel file lets us silence stick prompts WITHOUT restarting Cursor or
// the daemon — useful when the user has Cursor's bypass-permission toggled
// on and doesn't want a duplicate gate on the stick. `touch` to silence,
// `rm` to re-enable. Hook checks per-invocation.
const ECHO_OFF_SENTINEL = '/tmp/cursor-bridge-echo-off';
const ECHO_ENABLED =
    (process.env.CURSOR_BRIDGE_PERMISSION_ECHO || '1') !== '0' &&
    !fs.existsSync(ECHO_OFF_SENTINEL);
const MATCHER_ENABLED = (process.env.CURSOR_BRIDGE_PERMISSION_MATCHER || '1') !== '0';

// Hard cap on the whole hook process. Daemon's wait_permission timeout +
// socket round-trip + parse. Stays well under Cursor's per-script timeout
// in hooks.json (12s) so the script always exits before Cursor kills it.
const HOOK_CAP_MS = Math.round((TIMEOUT_S + 2) * 1000);

// ─── client-side matcher ───────────────────────────────────────────────
// Commands that read state without mutating anything visible from the
// shell. Adding to this list trades stick-prompt fatigue for slightly
// reduced visibility — only add commands that are unambiguously
// read-only AND can't be turned into a write via a single flag.

const SAFE_SHELL_COMMANDS = new Set([
    // listing / pathing
    'ls', 'll', 'la', 'pwd', 'realpath', 'readlink', 'basename', 'dirname',
    // reading / inspecting files
    'cat', 'bat', 'head', 'tail', 'wc', 'file', 'stat', 'od', 'xxd',
    // searching (grep -r / rg are read-only by design; no in-place flag)
    'grep', 'egrep', 'fgrep', 'rg', 'ag',
    // diffing (read-only; no in-place flag exists)
    'diff', 'cmp', 'comm',
    // text echoing (printf with no -v shell-write side effect)
    'echo', 'printf',
    // identity / system info
    'whoami', 'id', 'groups', 'tty', 'hostname', 'arch', 'uname', 'date',
    // command lookup
    'which', 'whereis', 'type', 'command',
    // hashing
    'cksum', 'md5', 'md5sum', 'sha1sum', 'sha256sum', 'shasum',
    // structured parsers (no -i / in-place flag exists)
    'jq', 'yq',
    // no-op
    'true', 'false', ':',
]);

// `git` is special: many subcommands are pure read, many others mutate.
// Only safelist the read-only ones. Anything not in this set falls
// through to the stick.
const SAFE_GIT_SUBCOMMANDS = new Set([
    'status', 'log', 'diff', 'show', 'blame',
    'ls-files', 'ls-tree', 'ls-remote',
    'rev-parse', 'rev-list', 'describe',
]);

// Reject anything with shell metacharacters that enable chaining,
// redirect, or substitution. Even a safelisted `cat` can become unsafe
// with `cat foo | xargs rm`.
const SHELL_METACHAR_RE = /[;&|<>`$\n\r]|\$\(|\$\{/;

// Reject leading env-var assignments (`FOO=bar cmd`). The assignment
// itself is benign but obscures audit and is rarely seen in agent
// output, so easier to gate.
const LEADING_ENV_ASSIGN_RE = /^\s*[A-Za-z_][A-Za-z0-9_]*=/;

function isLowRiskShell(command) {
    if (!command || typeof command !== 'string') return null;
    if (SHELL_METACHAR_RE.test(command)) return null;
    if (LEADING_ENV_ASSIGN_RE.test(command)) return null;

    const tokens = command.trim().split(/\s+/);
    if (tokens.length === 0) return null;
    const head = tokens[0];

    if (SAFE_SHELL_COMMANDS.has(head)) {
        return `safelisted: ${head}`;
    }
    if (head === 'git' && tokens.length >= 2 && SAFE_GIT_SUBCOMMANDS.has(tokens[1])) {
        return `safelisted: git ${tokens[1]}`;
    }
    return null;
}

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

    // Client-side matcher: short-circuit allow for low-risk shell reads
    // before we even open the socket. Saves the round-trip AND keeps the
    // stick's approval queue focused on commands the agent actually
    // needs to think about.
    if (MATCHER_ENABLED && ev.hook_event_name === 'beforeShellExecution') {
        const tag = isLowRiskShell(ev.command);
        if (tag) {
            if (process.env.CURSOR_HOOK_DEBUG === '1') {
                try {
                    fs.appendFileSync(
                        '/tmp/cursor-hook-debug.jsonl',
                        JSON.stringify({ ts: Date.now(), matcher: tag,
                                         cmd: String(ev.command).slice(0, 200) }) + '\n'
                    );
                } catch (_) {}
            }
            emitDecision('allow', `buddy matcher: ${tag}`);
            return;
        }
    }

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
