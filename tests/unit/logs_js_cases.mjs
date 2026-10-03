import { readFileSync } from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

const src = readFileSync(new URL("../../cps/static/js/logs.js", import.meta.url), "utf8");

// A small DOM: enough elements, classes, attributes and text for logs.js.
class Node {
    constructor(tag, doc) {
        this.tagName = tag.toUpperCase();
        this.nodeType = 1;
        this.ownerDocument = doc;
        this.children = [];
        this.childNodes = [];
        this.parentNode = null;
        this.attrs = {};
        this.style = { props: {}, setProperty(k, v) { this.props[k] = v; } };
        this.hidden = false;
        this.disabled = false;
        this.tabIndex = 0;
        this.listeners = {};
        this.scrolledIntoView = 0;
        const el = this;
        this.classList = {
            set: new Set(),
            add(c) { this.set.add(c); },
            remove(c) { this.set.delete(c); },
            contains(c) { return this.set.has(c); },
            toggle(c, on) { if (on) this.set.add(c); else this.set.delete(c); },
        };
        Object.defineProperty(this, "className", {
            get() { return [...el.classList.set].join(" "); },
            set(v) { el.classList.set = new Set(String(v).split(/\s+/).filter(Boolean)); },
        });
    }
    get firstChild() { return this.childNodes[0] || null; }
    get firstElementChild() { return this.children[0] || null; }
    get nextElementSibling() {
        const sibs = this.parentNode ? this.parentNode.children : [];
        return sibs[sibs.indexOf(this) + 1] || null;
    }
    appendChild(node) {
        if (node.isFragment) {
            [...node.childNodes].forEach((n) => this.appendChild(n));
            node.childNodes = [];
            node.children = [];
            return node;
        }
        if (node.parentNode) node.parentNode.removeChild(node);
        node.parentNode = this;
        this.childNodes.push(node);
        if (node.nodeType === 1) this.children.push(node);
        return node;
    }
    removeChild(node) {
        this.childNodes = this.childNodes.filter((n) => n !== node);
        this.children = this.children.filter((n) => n !== node);
        node.parentNode = null;
        return node;
    }
    get textContent() { return this.childNodes.map((n) => n.textContent).join(""); }
    set textContent(v) {
        this.childNodes.forEach((n) => { n.parentNode = null; });
        this.childNodes = [];
        this.children = [];
        if (v) this.appendChild(this.ownerDocument.createTextNode(v));
    }
    // Only what the lookups endpoint sends: <p class="…" data-id="…" data-day="…">text</p>
    set innerHTML(html) {
        this.textContent = "";
        for (const m of html.matchAll(/<p ([^>]*)>([^<]*)<\/p>/g)) {
            const p = this.ownerDocument.createElement("p");
            for (const a of m[1].matchAll(/([\w-]+)="([^"]*)"/g)) {
                if (a[1] === "class") p.className = a[2]; else p.setAttribute(a[1], a[2]);
            }
            p.textContent = m[2];
            this.appendChild(p);
        }
    }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
    addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); }
    dispatch(ev, extra = {}) { (this.listeners[ev] || []).forEach((fn) => fn({ preventDefault() {}, ...extra })); }
    contains(node) {
        for (let n = node; n; n = n.parentNode) if (n === this) return true;
        return false;
    }
    focus() { this.ownerDocument.activeElement = this; }
    scrollIntoView() { this.scrolledIntoView += 1; }
    get offsetHeight() { return 60; }
    descendants() { return this.children.flatMap((c) => [c, ...c.descendants()]); }
    matches(sel) { return sel.startsWith(".") && this.classList.contains(sel.slice(1)); }
    querySelectorAll(sel) { return this.descendants().filter((n) => n.matches(sel)); }
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

function makeDocument() {
    const doc = {
        visibilityState: "visible",
        listeners: {},
        documentElement: { lang: "en" },
        createElement(tag) { return new Node(tag, doc); },
        createTextNode(data) { return { nodeType: 3, textContent: String(data), parentNode: null }; },
        createDocumentFragment() { const f = new Node("#fragment", doc); f.isFragment = true; return f; },
        addEventListener(ev, fn) { (doc.listeners[ev] = doc.listeners[ev] || []).push(fn); },
        ready(fn) { fn(); },
    };
    const el = (tag, id, cls, attrs = {}) => {
        const n = doc.createElement(tag);
        if (id) n.id = id;
        if (cls) n.className = cls;
        Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, v));
        return n;
    };
    const page = el("div", "", "lily-logs", {
        "data-logs-url": "/logs/data", "data-lookups-url": "/logs/lookups",
        "data-empty-message": "No logs captured yet.", "data-warning": "Warning", "data-error": "Error",
    });
    const ids = {};
    const add = (parent, n) => { parent.appendChild(n); if (n.id) ids[n.id] = n; return n; };
    const bar = add(page, el("div", "", "logs-bar"));
    add(bar, el("button", "logs_tab_app", "btn lily-chip active"));
    add(bar, el("button", "logs_tab_metadata", "btn lily-chip"));
    add(bar, el("span", "logs_live", "logs-live"));
    const copy = add(bar, el("button", "log_copy", "icon-btn logs-copy"));
    copy.appendChild(el("span", "", "glyphicon glyphicon-copy"));
    const label = el("span", "", "sr-only logs-copy-label", { "data-done": "Copied", "data-failed": "Couldn’t copy." });
    label.textContent = "Copy logs";
    copy.appendChild(label);
    add(page, el("p", "logs_status", "logs-status"));
    const app = add(page, el("div", "logs_app", "logs-panel logs-app"));
    add(app, el("div", "log_output", "logs-output"));
    const md = add(page, el("div", "logs_metadata", "logs-panel logs-lookups"));
    md.hidden = true;
    const lookups = add(md, el("div", "lookup_output", "logs-output"));
    lookups.innerHTML = '<p class="logs-source" data-day="2026-10-03">3 October</p>' +
        '<p class="logs-line lookup is-matched" data-id="7" data-day="2026-10-03">20:28 Mesomolecules matched</p>';
    add(md, el("p", "lookup_empty", "logs-empty")).hidden = true;
    const topbar = el("div", "", "lily-topbar");
    doc.querySelector = (sel) => (sel === ".lily-logs" ? page : sel === ".lily-topbar" ? topbar : null);
    doc.getElementById = (id) => ids[id] || null;
    doc.page = page;
    return doc;
}

export function loadLogs(fetchImpl, { visibilityState = "visible" } = {}) {
    const document = makeDocument();
    document.visibilityState = visibilityState;
    const timers = new Map();
    let nextTimer = 1;
    const observers = [];
    let selection = { isCollapsed: true, anchorNode: null };
    const sandbox = {
        document,
        setTimeout(cb, ms) { const id = nextTimer++; timers.set(id, { cb, ms }); return id; },
        clearTimeout(id) { timers.delete(id); },
        fetch: fetchImpl,
        console,
        navigator: {},
        isSecureContext: false,
        addEventListener() {},
        getSelection: () => selection,
        IntersectionObserver: class {
            constructor(cb) { this.cb = cb; observers.push(this); }
            observe() {}
        },
        Promise,
    };
    sandbox.window = sandbox;
    sandbox.$ = (sel) => ({ ready: (fn) => fn() });
    vm.createContext(sandbox);
    vm.runInContext(src, sandbox);
    const $id = (id) => document.getElementById(id);
    return {
        document,
        timers,
        LilyLogs: sandbox.LilyLogs,
        output: $id("log_output"),
        lookups: $id("lookup_output"),
        status: $id("logs_status"),
        live: $id("logs_live"),
        end: document.page.querySelector(".logs-end"),
        // The reader scrolls away from (false) or back to (true) the newest line.
        atBottom(on) { observers.forEach((o) => o.cb([{ isIntersecting: on }])); },
        select(node) { selection = { isCollapsed: false, anchorNode: node }; },
        unselect() { selection = { isCollapsed: true, anchorNode: null }; },
        tick() {
            const due = [...timers.entries()];
            timers.clear();
            due.forEach(([, t]) => t.cb());
        },
        setVisibility(state) {
            document.visibilityState = state;
            (document.listeners.visibilitychange || []).forEach((fn) => fn());
        },
    };
}

function respond(payload) {
    return Promise.resolve({ ok: true, json: () => Promise.resolve(payload) });
}

function scripted(payloads) {
    const calls = [];
    return {
        calls,
        fetch(url) {
            calls.push(url);
            const payload = payloads[Math.min(calls.length, payloads.length) - 1];
            return typeof payload === "function" ? payload(url) : respond(payload);
        },
    };
}

async function flush() {
    for (let i = 0; i < 10; i++) {
        await new Promise((r) => setImmediate(r));
    }
}

const lines = (output) => output.querySelectorAll(".logs-line");
const texts = (output) => lines(output).map((p) => p.children[1].textContent);

const SAMPLE = [
    "===== Ingest service (current) =====",
    "2026-10-03 20:18:30.400153342  ========== STARTING CWA-INGEST SERVICE ==========",
    "2026-10-03 20:18:30.401660197  [cwa-ingest-service] Watching folder: /cwa-book-ingest",
    "",
    "===== Lily web app @400000006ac15d9519eea825.u =====",
    "2026-10-03 19:57:28.479144014  [2026-10-03 19:57:28,212]  WARN {cps.pdf_cover:315} Could not make the cover of book 315",
    "===== Lily web app (current) =====",
    "2026-10-03 20:18:29.312064974  [2026-10-03 20:18:29,311]  INFO {cps:160} Starting Calibre Web...",
    "2026-10-03 20:18:30.000000001  [2026-10-03 20:18:30,000]  ERROR {cps.web:1} Boom",
    "2026-10-03 20:18:30.000000002    ",
    "[2026-10-03 20:19:00,000]  INFO {cps.server:1} from the log file, no s6 stamp",
].join("\n");

const results = [];
function test(name, fn) {
    results.push([name, fn]);
}

test("parse cleans each line and merges one service's sources", async () => {
    const { LilyLogs } = loadLogs(scripted([{ success: true, version: "v1", text: "" }]).fetch);
    // Copied out of the sandbox so deepEqual compares plain arrays
    const groups = JSON.parse(JSON.stringify(LilyLogs.parse(SAMPLE)));
    assert.deepEqual(groups.map((g) => g.name), ["Ingest service", "Lily web app"]);
    const [ingest, web] = groups;
    // The start-up banner and the service tag are gone; the time is short
    assert.deepEqual(ingest.lines.map((l) => [l.time, l.level, l.text]),
        [["20:18:30", "", "Watching folder: /cwa-book-ingest"]]);
    assert.deepEqual(web.lines.map((l) => [l.time, l.level, l.text]), [
        ["19:57:28", "warning", "Could not make the cover of book 315"],
        ["20:18:29", "", "Starting Calibre Web..."],
        ["20:18:30", "error", "Boom"],
        ["20:19:00", "", "from the log file, no s6 stamp"],
    ]);
    assert.equal(web.date, "2026-10-03");
    assert.equal(web.lines[0].key, SAMPLE.split("\n")[5]);
});

test("loads on open and html stays text", async () => {
    const fx = scripted([{ success: true, version: "v1", text: "2026-10-03 20:00:00.1  <img src=x onerror=alert(1)>" }]);
    const { output } = loadLogs(fx.fetch);
    await flush();
    assert.equal(fx.calls[0], "/logs/data");
    assert.deepEqual(texts(output), ["<img src=x onerror=alert(1)>"]);
    assert.equal(output.querySelectorAll(".logs-line")[0].children.length, 2);
});

test("warnings and errors carry a word, nothing else", async () => {
    const fx = scripted([{ success: true, version: "v1", text: SAMPLE }]);
    const { output } = loadLogs(fx.fetch);
    await flush();
    const words = output.querySelectorAll(".logs-level").map((b) => b.textContent);
    assert.deepEqual(words, ["Warning", "Error"]);
    assert.equal(lines(output).filter((p) => p.classList.contains("is-error")).length, 1);
    assert.deepEqual(output.querySelectorAll(".logs-source").map((h) => h.textContent.split(",")[0]),
        ["Ingest service", "Lily web app"]);
});

test("empty payload shows the empty message", async () => {
    const fx = scripted([{ success: true, version: "v1", text: "" }]);
    const { output } = loadLogs(fx.fetch);
    await flush();
    assert.equal(output.textContent, "No logs captured yet.");
});

test("polls every 2 seconds with the last version", async () => {
    const fx = scripted([
        { success: true, version: "v1", text: "2026-10-03 20:00:00.1  one" },
        { success: true, version: "v1", unchanged: true },
    ]);
    const { timers, tick } = loadLogs(fx.fetch);
    await flush();
    assert.deepEqual([...timers.values()].map((t) => t.ms), [2000]);
    tick();
    await flush();
    tick();
    await flush();
    assert.equal(fx.calls.length, 3);
    assert.equal(fx.calls[1], "/logs/data?since=v1");
});

test("unchanged reply leaves the output alone", async () => {
    const fx = scripted([
        { success: true, version: "v1", text: "2026-10-03 20:00:00.1  one" },
        { success: true, version: "v1", unchanged: true },
    ]);
    const { output, tick } = loadLogs(fx.fetch);
    await flush();
    const before = lines(output)[0];
    tick();
    await flush();
    assert.equal(lines(output)[0], before);
});

test("only lines that weren't there before fade in", async () => {
    const one = "2026-10-03 20:00:00.1  one";
    const fx = scripted([
        { success: true, version: "v1", text: one },
        { success: true, version: "v2", text: one + "\n2026-10-03 20:00:01.1  two" },
    ]);
    const { output, tick } = loadLogs(fx.fetch);
    await flush();
    assert.equal(lines(output).filter((p) => p.classList.contains("is-new")).length, 0);
    tick();
    await flush();
    assert.deepEqual(lines(output).map((p) => p.classList.contains("is-new")), [false, true]);
});

test("follows new lines at the bottom, not when scrolled up", async () => {
    const fx = scripted([
        { success: true, version: "v1", text: "2026-10-03 20:00:00.1  one" },
        { success: true, version: "v2", text: "2026-10-03 20:00:00.1  one\n2026-10-03 20:00:01.1  two" },
        { success: true, version: "v3", text: "2026-10-03 20:00:00.1  one\n2026-10-03 20:00:02.1  three" },
    ]);
    const { end, tick, atBottom } = loadLogs(fx.fetch);
    await flush();
    const opened = end.scrolledIntoView;
    assert.ok(opened >= 1, "opens at the newest line");
    tick();
    await flush();
    assert.equal(end.scrolledIntoView, opened + 1);
    atBottom(false);
    tick();
    await flush();
    assert.equal(end.scrolledIntoView, opened + 1);
});

test("a selection in the log holds the redraw until it ends", async () => {
    const fx = scripted([
        { success: true, version: "v1", text: "2026-10-03 20:00:00.1  one" },
        { success: true, version: "v2", text: "2026-10-03 20:00:00.1  one\n2026-10-03 20:00:01.1  two" },
    ]);
    const { output, document, tick, select, unselect } = loadLogs(fx.fetch);
    await flush();
    select(lines(output)[0]);
    tick();
    await flush();
    assert.equal(lines(output).length, 1);
    unselect();
    (document.listeners.selectionchange || []).forEach((fn) => fn());
    assert.equal(lines(output).length, 2);
});

test("no polling while the tab is hidden; resumes when shown", async () => {
    const fx = scripted([{ success: true, version: "v1", text: "x" }]);
    const { timers, setVisibility } = loadLogs(fx.fetch);
    await flush();
    assert.equal(timers.size, 1);
    setVisibility("hidden");
    assert.equal(timers.size, 0);
    setVisibility("visible");
    await flush();
    assert.equal(fx.calls.length, 2);
    assert.equal(timers.size, 1);
});

test("hidden on open schedules nothing", async () => {
    const fx = scripted([{ success: true, version: "v1", text: "x" }]);
    const { timers } = loadLogs(fx.fetch, { visibilityState: "hidden" });
    await flush();
    assert.equal(fx.calls.length, 1);
    assert.equal(timers.size, 0);
});

test("no second request while one is in flight", async () => {
    let release;
    const gate = new Promise((r) => { release = r; });
    const fx = scripted([
        () => gate.then(() => ({ ok: true, json: () => Promise.resolve({ success: true, version: "v1", text: "x" }) })),
    ]);
    const { timers, setVisibility } = loadLogs(fx.fetch);
    assert.equal(timers.size, 0);
    setVisibility("hidden");
    setVisibility("visible");
    assert.equal(fx.calls.length, 1);
    release();
    await flush();
    assert.equal(timers.size, 1);
});

test("failure keeps the output, flags the status and backs off", async () => {
    const fx = scripted([
        { success: true, version: "v1", text: "2026-10-03 20:00:00.1  good" },
        () => Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) }),
        { success: true, version: "v1", unchanged: true },
    ]);
    const { output, status, live, timers, tick } = loadLogs(fx.fetch);
    await flush();
    tick();
    await flush();
    assert.deepEqual(texts(output), ["good"]);
    assert.match(status.textContent, /Couldn't load new lines \(HTTP 500\)/);
    assert.equal(status.classList.contains("is-error"), true);
    assert.equal(live.classList.contains("is-error"), true);
    assert.deepEqual([...timers.values()].map((t) => t.ms), [10000]);
    tick();
    await flush();
    assert.equal(status.textContent, "");
    assert.equal(live.classList.contains("is-error"), false);
});

test("the Metadata pill shows lookups and asks only for newer ones", async () => {
    const fx = scripted([
        { success: true, version: "v1", text: "2026-10-03 20:00:00.1  one" },
        { success: true, html: '<p class="logs-line lookup is-failed" data-id="8" data-day="2026-10-03">20:29 Some exercises failed</p>' },
        { success: true, html: "" },
    ]);
    const { document, lookups, tick } = loadLogs(fx.fetch);
    await flush();
    const tab = document.getElementById("logs_tab_metadata");
    tab.dispatch("click");
    await flush();
    assert.equal(document.getElementById("logs_metadata").hidden, false);
    assert.equal(document.getElementById("logs_app").hidden, true);
    assert.equal(tab.getAttribute("aria-selected"), "true");
    assert.equal(tab.classList.contains("active"), true);
    assert.equal(fx.calls[1], "/logs/lookups?after=7&day=2026-10-03");
    const rows = lines(lookups);
    assert.deepEqual(rows.map((p) => p.getAttribute("data-id")), ["7", "8"]);
    assert.equal(rows[1].classList.contains("is-new"), true);
    tick();
    await flush();
    // The app log isn't polled while its view is away
    assert.equal(fx.calls[2], "/logs/lookups?after=8&day=2026-10-03");
});

let failures = 0;
for (const [name, fn] of results) {
    try {
        await fn();
        console.log("ok -", name);
    } catch (err) {
        failures += 1;
        console.log("FAIL -", name, "-", err.message);
    }
}
process.exit(failures ? 1 : 0);
