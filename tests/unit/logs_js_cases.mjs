import { readFileSync } from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

const src = readFileSync(new URL("../../cps/static/js/logs.js", import.meta.url), "utf8");

function makeEl(id) {
    const el = {
        id,
        classes: {},
        scrollTop: 0,
        scrollHeight: 0,
        clientHeight: 100,
        textContent: "",
        appended: 0,
        _text: "",
        text(v) { if (v === undefined) return el._text; el._text = v; return el; },
        attr(n, v) {
            el._attrs = el._attrs || {};
            if (v === undefined) return el._attrs[n];
            el._attrs[n] = v; return el;
        },
        toggleClass(name, on) { el.classes[name] = on; },
        appendChild(node) {
            el.textContent += node.data;
            el.appended += 1;
            el.scrollHeight += 1000;
            return node;
        },
    };
    Object.defineProperty(el, "length", { get() { return 1; } });
    return el;
}

export function loadLogs(fetchImpl, { visibilityState = "visible" } = {}) {
    const els = {
        ".lily-logs": makeEl(".lily-logs"),
        "#logs_status": makeEl("#logs_status"),
    };
    els[".lily-logs"].attr("data-logs-url", "/logs/data");
    els[".lily-logs"].attr("data-empty-message", "No logs captured yet.");
    const output = makeEl("log_output");
    const timers = new Map();
    let nextTimer = 1;
    const listeners = {};
    const document = {
        ready(fn) { fn(); },
        getElementById() { return output; },
        createTextNode(data) { return { data }; },
        addEventListener(ev, fn) { (listeners[ev] = listeners[ev] || []).push(fn); },
        visibilityState,
    };
    const sandbox = {
        document,
        setTimeout(cb, ms) { const id = nextTimer++; timers.set(id, { cb, ms }); return id; },
        clearTimeout(id) { timers.delete(id); },
        fetch: fetchImpl,
        console,
    };
    const $ = (sel) => {
        if (sel === document) return { ready: (fn) => fn() };
        return els[sel] || makeEl(sel);
    };
    sandbox.$ = $;
    sandbox.window = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(src, sandbox);
    return {
        els,
        output,
        timers,
        // Fire every pending timer, as if its delay had passed.
        tick() {
            const due = [...timers.entries()];
            timers.clear();
            due.forEach(([, t]) => t.cb());
        },
        setVisibility(state) {
            document.visibilityState = state;
            (listeners.visibilitychange || []).forEach((fn) => fn());
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
            return typeof payload === "function" ? payload() : respond(payload);
        },
    };
}

async function flush() {
    for (let i = 0; i < 10; i++) {
        await new Promise((r) => setImmediate(r));
    }
}

const results = [];
function test(name, fn) {
    results.push([name, fn]);
}

test("loads on open and html stays text", async () => {
    const fx = scripted([{ success: true, version: "v1", truncated: false, text: "a\n<img src=x onerror=alert(1)>" }]);
    const { output } = loadLogs(fx.fetch);
    await flush();
    assert.equal(fx.calls.length, 1);
    assert.equal(fx.calls[0], "/logs/data");
    assert.equal(output.textContent, "a\n<img src=x onerror=alert(1)>");
});

test("empty payload shows the empty message", async () => {
    const fx = scripted([{ success: true, version: "v1", truncated: false, text: "" }]);
    const { output } = loadLogs(fx.fetch);
    await flush();
    assert.equal(output.textContent, "No logs captured yet.");
});

test("polls every 2 seconds with the last version", async () => {
    const fx = scripted([
        { success: true, version: "v1", truncated: false, text: "one\n" },
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
        { success: true, version: "v1", truncated: false, text: "one\n" },
        { success: true, version: "v1", unchanged: true },
    ]);
    const { output, tick } = loadLogs(fx.fetch);
    await flush();
    output.textContent = "sentinel";
    tick();
    await flush();
    assert.equal(output.textContent, "sentinel");
});

test("grown log appends only the new lines and stays pinned to the bottom", async () => {
    const fx = scripted([
        { success: true, version: "v1", truncated: false, text: "one\n" },
        { success: true, version: "v2", truncated: false, text: "one\ntwo\n" },
    ]);
    const { output, tick } = loadLogs(fx.fetch);
    await flush();
    output.scrollHeight = 100;
    output.scrollTop = 0;
    tick();
    await flush();
    assert.equal(output.textContent, "one\ntwo\n");
    assert.equal(output.appended, 1);
    assert.equal(output.scrollTop, 1100);
});

test("rewritten log replaces the output", async () => {
    const fx = scripted([
        { success: true, version: "v1", truncated: false, text: "one\n" },
        { success: true, version: "v2", truncated: true, text: "rotated\n" },
    ]);
    const { els, output, tick } = loadLogs(fx.fetch);
    await flush();
    tick();
    await flush();
    assert.equal(output.textContent, "rotated\n");
    assert.equal(output.appended, 0);
    assert.equal(els["#logs_status"]._text, "Showing the most recent entries only.");
});

test("scrolled-up reader is not dragged to the bottom", async () => {
    const fx = scripted([
        { success: true, version: "v1", truncated: false, text: "one\n" },
        { success: true, version: "v2", truncated: false, text: "one\ntwo\n" },
    ]);
    const { output, tick } = loadLogs(fx.fetch);
    await flush();
    output.scrollHeight = 2000;
    output.scrollTop = 0;
    tick();
    await flush();
    assert.equal(output.textContent, "one\ntwo\n");
    assert.equal(output.scrollTop, 0);
});

test("no polling while the tab is hidden; resumes when shown", async () => {
    const fx = scripted([{ success: true, version: "v1", truncated: false, text: "x" }]);
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
    const fx = scripted([{ success: true, version: "v1", truncated: false, text: "x" }]);
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
        { success: true, version: "v1", truncated: false, text: "good" },
        () => Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) }),
        { success: true, version: "v1", unchanged: true },
    ]);
    const { els, output, timers, tick } = loadLogs(fx.fetch);
    await flush();
    tick();
    await flush();
    assert.equal(output.textContent, "good");
    assert.match(els["#logs_status"]._text, /Couldn't load new lines \(HTTP 500\)/);
    assert.equal(els["#logs_status"].classes["is-error"], true);
    assert.deepEqual([...timers.values()].map((t) => t.ms), [10000]);
    tick();
    await flush();
    assert.equal(els["#logs_status"]._text, "");
    assert.equal(els["#logs_status"].classes["is-error"], false);
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
