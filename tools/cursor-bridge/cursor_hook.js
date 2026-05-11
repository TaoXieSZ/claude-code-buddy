#!/usr/bin/env node
//
// Cursor IDE hook → cursor-bridge daemon shim.
//
// Cursor fires this for each registered hook event. We read the JSON
// payload off stdin, translate it into a Claude-Code-shaped event that
// bridge.py's apply_event() already understands, forward it to the
// bridge daemon over a Unix socket, and exit immediately.
//
// Any failure (daemon down, socket missing, bad JSON, etc.) exits 0
// silently — the hook MUST NOT block Cursor on a side channel that may
// be temporarily offline.
//
// Wired up by tools/cursor-bridge/install.sh into ~/.cursor/hooks.json
// under the relevant Cursor hook events (sessionStart,
// beforeSubmitPrompt, beforeShellExecution, ...).
//
// Permission echo (button-on-stick approval) is NOT implemented in v1.
// Cursor's permission protocol differs from Claude Code's hookSpecificOutput
// shape; we'll wire it up in a follow-up.

'use strict';

const fs   = require('fs');
const net  = require('net');
const path = require('path');

const SOCKET_PATH = process.env.CURSOR_BRIDGE_SOCKET || '/tmp/cursor-bridge.sock';
const TIMEOUT_MS  = 500; // never slow Cursor down for our side channel

// ─── Cursor → Claude Code event translation ────────────────────────────
//
// bridge.py's apply_event() switches on `hook_event_name` matching the
// Claude Code names (SessionStart, UserPromptSubmit, PreToolUse, ...).
// We map Cursor's hook event names onto those, attaching the same
// fields apply_event() reads (tool_name, tool_input, prompt, ...).
//
// For unknown events we exit 0 silently — better to drop than to
// generate confusing state mutations.

function translate(ev) {
    const cursorName = ev.hook_event_name || ev.event || '';
    const sid =
        ev.session_id || ev.sessionId || ev.conversation_id || 'anon';
    const base = { session_id: sid };

    switch (cursorName) {
        case 'sessionStart':
            return { ...base, hook_event_name: 'SessionStart' };

        case 'sessionEnd':
            return { ...base, hook_event_name: 'SessionEnd' };

        case 'beforeSubmitPrompt': {
            const prompt =
                ev.prompt || ev.user_prompt || ev.userPrompt || ev.text || '';
            return {
                ...base,
                hook_event_name: 'UserPromptSubmit',
                prompt: String(prompt),
            };
        }

        case 'afterAgentResponse': {
            // afterAgentResponse carries token usage + the assistant reply
            // text. Plumb both through so bridge.py can accumulate `tokens`
            // and surface the latest reply snippet in `entries`.
            const out = { ...base, hook_event_name: 'Stop' };
            if (typeof ev.output_tokens === 'number') out.output_tokens = ev.output_tokens;
            if (typeof ev.input_tokens  === 'number') out.input_tokens  = ev.input_tokens;
            const txt = ev.text || ev.response || '';
            if (txt) out.text = String(txt).slice(0, 200);
            return out;
        }
        case 'afterAgentThought':
        case 'stop':
            return { ...base, hook_event_name: 'Stop' };

        case 'beforeShellExecution': {
            const ti = ev.tool_input || {};
            const cmd = ev.command || ti.command || ev.shell_command || '';
            return {
                ...base,
                hook_event_name: 'PreToolUse',
                tool_name: 'shell',
                tool_input: { command: String(cmd).slice(0, 200) },
            };
        }
        case 'afterShellExecution':
            return {
                ...base,
                hook_event_name: 'PostToolUse',
                tool_name: 'shell',
            };

        case 'beforeMCPExecution': {
            const tool = ev.tool || ev.tool_name || ev.method || 'mcp';
            const desc = ev.description || ev.summary || '';
            return {
                ...base,
                hook_event_name: 'PreToolUse',
                tool_name: `mcp:${String(tool).slice(0, 40)}`,
                tool_input: { description: String(desc).slice(0, 120) },
            };
        }
        case 'afterMCPExecution':
            return {
                ...base,
                hook_event_name: 'PostToolUse',
                tool_name: 'mcp',
            };

        case 'beforeReadFile': {
            const fp = ev.file_path || ev.path || ev.filePath || '';
            return {
                ...base,
                hook_event_name: 'PreToolUse',
                tool_name: 'read',
                tool_input: { file_path: String(fp).slice(0, 200) },
            };
        }

        case 'afterFileEdit': {
            const fp = ev.file_path || ev.path || ev.filePath || '';
            return {
                ...base,
                hook_event_name: 'PostToolUse',
                tool_name: 'edit',
                tool_input: { file_path: String(fp).slice(0, 200) },
            };
        }

        case 'preToolUse':
            // Generic Cursor tool gate (for non-shell/non-MCP tools).
            return {
                ...base,
                hook_event_name: 'PreToolUse',
                tool_name: ev.tool_name || ev.tool || 'tool',
            };
        case 'postToolUse':
            return {
                ...base,
                hook_event_name: 'PostToolUse',
                tool_name: ev.tool_name || ev.tool || 'tool',
            };
        case 'postToolUseFailure':
            // Same shape as postToolUse but flagged. bridge.py surfaces
            // this as `msg = "failed: <tool>"` and pushes a `!failed:` line
            // into entries so the user can see when something went sideways
            // — otherwise a failed Edit / shell looks identical to a
            // successful one on the stick.
            return {
                ...base,
                hook_event_name: 'PostToolUse',
                tool_name: ev.tool_name || ev.tool || 'tool',
                failure: true,
                error: String(ev.error || ev.message || ev.reason || '').slice(0, 120),
            };

        case 'subagentStart':
            // Multitask Mode fires subagents constantly. Surface them so
            // the buddy reflects background workers in flight. We treat
            // each subagent as a "subagent" tool start; bridge.py will
            // count it toward `running` via the standard PreToolUse path.
            return {
                ...base,
                hook_event_name: 'PreToolUse',
                tool_name: `sub:${String(ev.subagent_type || ev.type || 'task').slice(0, 24)}`,
                tool_input: {
                    description: String(
                        ev.description || ev.task || ev.prompt || '',
                    ).slice(0, 120),
                },
            };
        case 'subagentStop':
            return {
                ...base,
                hook_event_name: 'PostToolUse',
                tool_name: `sub:${String(ev.subagent_type || ev.type || 'task').slice(0, 24)}`,
            };

        default:
            return null;
    }
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

    if (process.env.CURSOR_HOOK_DEBUG === '1') {
        try {
            fs.appendFileSync(
                '/tmp/cursor-hook-debug.jsonl',
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

    // Hard cap in case the socket layer hangs.
    setTimeout(finish, TIMEOUT_MS + 100).unref();
}

main();
