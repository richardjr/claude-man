#!/usr/bin/env node
// claude-man relay helper — written into /workspace/.relay/bin/relay by the HOST on every start
// (docs/RELAY.md). It only reads/writes this project's own mailbox: requests go to ../out/, the host
// relay answers in ../in/. Nothing here is trusted by the host: the relay re-validates every request
// and identifies the sender by WHOSE workspace the file appeared in, never by its contents.
"use strict";
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const RELAY = path.resolve(__dirname, "..");
const OUT = path.join(RELAY, "out");
const IN = path.join(RELAY, "in");
const DIRECTORY = path.join(RELAY, "directory.json");
const TERMINAL = new Set(["done", "failed", "rejected", "cancelled"]);
const VALUE_RE = /^(?!-)[A-Za-z0-9._\/@:+=,%-]{1,200}$/;
const ID_RE = /^[a-z0-9][a-z0-9-]{7,63}$/;

function die(msg, code = 3) {
  process.stderr.write(`relay: ${msg}\n`);
  process.exit(code);
}

function usage() {
  process.stdout.write(`usage:
  relay list                                   who you can ask, and their tasks
  relay ask <project> <task> [name=value ...] [--note TEXT | --note-file PATH | --note -] [--wait] [--timeout SECS]
                                               send a request; prints its id
  relay wait <id> [--timeout SECS]             wait for the reply (default 540s; exit 2 = still open)
  relay status [<id>]                          your requests and their state
`);
}

function loadDirectory() {
  try {
    return JSON.parse(fs.readFileSync(DIRECTORY, "utf8"));
  } catch (e) {
    die("no relay directory here — this project can't ask any other project for help", 1);
  }
}

function cmdList() {
  const d = loadDirectory();
  if (!d.targets || d.targets.length === 0) {
    process.stdout.write("No project currently accepts requests from this one.\n");
    return 0;
  }
  for (const t of d.targets) {
    process.stdout.write(`${t.project}  (agent: ${t.agent}${t.locked ? ", locked" : ""})\n`);
    for (const task of t.tasks) {
      const params = task.params.map((p) => `${p.name}=<${p.description || p.name}>`).join(" ");
      const how = task.free_form ? ' --note "<your request>"' : "";
      process.stdout.write(`  ${task.name}${params ? " " + params : ""}${how}\n`);
      if (task.summary) process.stdout.write(`      ${task.summary}\n`);
      process.stdout.write(`      approval: ${task.approval}, timeout: ${task.timeout}s\n`);
    }
  }
  return 0;
}

function newId() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  const stamp = `${p(d.getUTCFullYear() % 100)}${p(d.getUTCMonth() + 1)}${p(d.getUTCDate())}` +
    `${p(d.getUTCHours())}${p(d.getUTCMinutes())}${p(d.getUTCSeconds())}`;
  return `${stamp}-${crypto.randomBytes(4).toString("hex")}`;
}

function parseFlags(argv) {
  const pos = [];
  const flags = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--wait") flags.wait = true;
    else if (a === "--note" || a === "--note-file" || a === "--timeout") {
      if (i + 1 >= argv.length) die(`${a} needs a value`);
      flags[a.slice(2)] = argv[++i];
    } else if (a.startsWith("--")) die(`unknown option ${a}`);
    else pos.push(a);
  }
  return { pos, flags };
}

function readNote(flags) {
  if (flags["note-file"] !== undefined) return fs.readFileSync(flags["note-file"], "utf8");
  if (flags.note === "-") return fs.readFileSync(0, "utf8");
  return flags.note || "";
}

function cmdAsk(argv) {
  const { pos, flags } = parseFlags(argv);
  if (pos.length < 2) die("ask needs <project> <task>");
  const [to, taskName, ...pairs] = pos;
  const params = {};
  for (const kv of pairs) {
    const eq = kv.indexOf("=");
    if (eq < 1) die(`param ${JSON.stringify(kv)} must be name=value`);
    const k = kv.slice(0, eq);
    const v = kv.slice(eq + 1);
    if (!VALUE_RE.test(v)) {
      die(`param ${k}=${JSON.stringify(v)} must be identifier-shaped (letters, digits, . _ / @ : + = , % -; no leading -) — put prose in --note`);
    }
    params[k] = v;
  }
  const note = readNote(flags);
  // Friendly local checks against the directory; the host relay re-validates everything.
  const d = loadDirectory();
  const target = (d.targets || []).find((t) => t.project === to);
  if (!target) die(`${to} does not accept requests from this project (run: relay list)`, 1);
  const task = target.tasks.find((t) => t.name === taskName);
  if (!task) die(`${to} has no task ${taskName} (offered: ${target.tasks.map((t) => t.name).join(", ")})`, 1);
  for (const p of task.params) if (!(p.name in params)) die(`task ${taskName} needs ${p.name}=<${p.description || p.name}>`, 1);
  for (const k of Object.keys(params)) if (!task.params.some((p) => p.name === k)) die(`task ${taskName} takes no param ${k}`, 1);
  if (task.free_form && !note.trim()) die(`task ${taskName} is free-form — pass the request with --note`, 1);

  const id = newId();
  const body = JSON.stringify({ v: 1, id, to, task: taskName, params, note }) + "\n";
  fs.mkdirSync(OUT, { recursive: true });
  const tmp = path.join(OUT, `.${id}.tmp`);
  fs.writeFileSync(tmp, body, { mode: 0o644 });
  fs.renameSync(tmp, path.join(OUT, `${id}.json`));
  process.stdout.write(`${id}\n`);
  process.stderr.write(`relay: sent ${taskName} to ${to} (approval: ${task.approval}). ` +
    (flags.wait ? "Waiting for the reply…\n" : `Wait for the reply with: relay wait ${id}\n`));
  if (flags.wait) return waitFor(id, flags.timeout);
  return 0;
}

function readStatus(id) {
  try {
    return JSON.parse(fs.readFileSync(path.join(IN, `${id}.json`), "utf8"));
  } catch (e) {
    return null;
  }
}

function sleep(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

function waitFor(id, timeoutArg) {
  if (!ID_RE.test(id)) die(`invalid request id ${JSON.stringify(id)}`);
  const timeout = Number(timeoutArg === undefined ? 540 : timeoutArg);
  if (!Number.isFinite(timeout) || timeout < 0) die("--timeout must be a number of seconds");
  const deadline = Date.now() + timeout * 1000;
  const started = Date.now();
  let last = "";
  let hinted = false;
  for (;;) {
    const s = readStatus(id);
    if (s) {
      const line = `${s.status}${s.detail ? ": " + s.detail : ""}`;
      if (line !== last) {
        process.stderr.write(`relay: ${id} ${line}\n`);
        last = line;
      }
      if (TERMINAL.has(s.status)) {
        if (s.reply) process.stdout.write(s.reply);
        return s.status === "done" ? 0 : 1;
      }
    } else if (!hinted && Date.now() - started > 30000) {
      process.stderr.write("relay: not picked up yet — the operator's claude-man relay may not be running (TUI open, or `claudemanctl relay run`)\n");
      hinted = true;
    }
    if (Date.now() >= deadline) {
      process.stderr.write(`relay: still ${s ? s.status : "unsent"} after ${timeout}s — run \`relay wait ${id}\` again to keep waiting\n`);
      return 2;
    }
    sleep(2000);
  }
}

function cmdStatus(argv) {
  if (argv[0]) {
    const s = readStatus(argv[0]);
    if (!s) die(`no status for ${argv[0]} yet`, 1);
    process.stdout.write(`${s.id}  ${s.to}/${s.task}  ${s.status}${s.detail ? "  " + s.detail : ""}\n`);
    if (s.reply) process.stdout.write(s.reply);
    return 0;
  }
  let names = [];
  try {
    names = fs.readdirSync(IN).filter((n) => n.endsWith(".json")).sort();
  } catch (e) { /* no inbox yet */ }
  let pending = [];
  try {
    pending = fs.readdirSync(OUT).filter((n) => n.endsWith(".json"));
  } catch (e) { /* none */ }
  if (names.length === 0 && pending.length === 0) {
    process.stdout.write("No requests yet.\n");
    return 0;
  }
  for (const n of pending) process.stdout.write(`${n.slice(0, -5)}  (sent — not picked up by the relay yet)\n`);
  for (const n of names) {
    const s = readStatus(n.slice(0, -5));
    if (s) process.stdout.write(`${s.id}  ${s.to}/${s.task}  ${s.status}${s.detail ? "  " + s.detail : ""}\n`);
  }
  return 0;
}

function main(argv) {
  const [cmd, ...rest] = argv;
  switch (cmd) {
    case "list": return cmdList();
    case "ask": return cmdAsk(rest);
    case "wait":
      if (!rest[0]) die("wait needs <id>");
      return waitFor(rest[0], parseFlags(rest.slice(1)).flags.timeout);
    case "status": return cmdStatus(rest);
    case undefined: case "-h": case "--help": case "help": usage(); return 0;
    default: usage(); return 3;
  }
}

process.exit(main(process.argv.slice(2)));
