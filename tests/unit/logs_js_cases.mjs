import { readFileSync } from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

const src = readFileSync(new URL("../../cps/static/js/logs.js", import.meta.url), "utf8");

function makeEl(id) {
    const el = {
        id,
        _val: "",
        checked: false,
        handlers: {},
        options: [],
        classes: {},
        scrollTop: 0,
        scrollHeight: 0,
        clientHeight: 100,
        textContent: "",
        _text: "",
        text(v) { if (v === undefined) return el._text; el._text = v; return el; },
        on(ev, fn) { (el.handlers[ev] = el.handlers[ev] || []).push(fn); return el; },
        trigger(ev) { (el.handlers[ev] || []).forEach((fn) => fn.call(el)); return el; },
        val(v) { if (v === undefined) return el._val; el._val = v; return el; },
        is(sel) { return sel === ":checked" ? el.checked : false; },
        find(sel) {
            const eq = sel.match(/option\[value='(.+)'\]/);
            const neq = sel.match(/option\[value!='(.+)'\]/);
            if (eq) {
                const found = el.options.filter((o) => o.value === eq[1]);
                return { length: found.length };
            }
            if (neq) {
                return { remove() { el.options = el.options.filter((o) => o.value === neq[1]); } };
            }
            return { length: 0 };
        },
        append(o) { el.options.push(o); return el; },
        attr(n, v) {
            el._attrs = el._attrs || {};
            if (v === undefined) return el._attrs[n];
            el._attrs[n] = v; return el;
        },
        data(n, v) {
            el._data = el._data || {};
            if (v === undefined) return el._data[n];
            el._data[n] = v; return el;
        },
        toggleClass(name, on) { el.classes[name] = on; },
        remove() {},
    };
    Object.defineProperty(el, "length", { get() { return el._len === undefined ? 1 : el._len; } });
    return el;
}

export function loadLogs(fetchImpl, { visibilityState = "visible" } = {}) {
    const els = {
        ".lily-logs": makeEl(".lily-logs"),
        "#log_source": makeEl("#log_source"),
        "#log_search": makeEl("#log_search"),
        "#log_errors": makeEl("#log_errors"),
        "#log_autorefresh": makeEl("#log_autorefresh"),
        "#log_refresh": makeEl("#log_refresh"),
        "#logs_status": makeEl("#logs_status"),
    };
    els[".lily-logs"].attr("data-logs-url", "/logs/data");
    els[".lily-logs"].attr("data-empty-message", "No logs captured yet.");
    els["#log_source"]._val = "all";
    const output = makeEl("log_output");
    let intervalCb = null;
    const sandbox = {
        document: {
            ready(fn) { fn(); },
            getElementById(id) { return output; },
            createElement() { return { value: "", textContent: "" }; },
            visibilityState,
        },
        setInterval(cb) { intervalCb = cb; },
        fetch: fetchImpl,
        console,
    };
    const $ = (sel) => {
        if (sel === sandbox.document) return { ready: (fn) => fn() };
        return els[sel] || makeEl(sel);
    };
    $.ready = (fn) => fn();
    sandbox.$ = $;
    sandbox.window = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(src, sandbox);
    return { els, output, tick: () => intervalCb && intervalCb() };
}

function resolvedFetch(payloads) {
    const calls = [];
    return {
        calls,
        fetch(url) {
            calls.push(url);
            const payload = typeof payloads === "function" ? payloads(url, calls.length) : payloads;
            return Promise.resolve({
                ok: true,
                json: () => Promise.resolve(payload),
            });
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

test("errors filter matches lowercase and html stays text", async () => {
    const padding = Array.from({ length: 10 }, (_, i) => "pad " + i).join("\n");
    const fx = resolvedFetch({
        success: true, truncated: false, sources: [],
        text: "info line\n" + padding + "\nerror lowercase happened\ncontext a\ncontext b\n<img src=x onerror=alert(1)>",
    });
    const { els, output } = loadLogs(fx.fetch);
    await flush();
    els["#log_errors"].checked = true;
    els["#log_errors"].trigger("change");
    assert.match(output.textContent, /error lowercase/);
    assert.match(output.textContent, /<img src=x onerror=alert\(1\)>/);
    assert.doesNotMatch(output.textContent, /info line/);
    assert.ok(fx.calls.length === 1);
});

test("search filter is case-insensitive", async () => {
    const fx = resolvedFetch({ success: true, truncated: false, sources: [], text: "Alpha\nbeta\nGAMMA" });
    const { els, output } = loadLogs(fx.fetch);
    await flush();
    els["#log_search"].val("gamma");
    els["#log_search"].trigger("input");
    assert.equal(output.textContent, "GAMMA");
});

test("empty payload shows the empty message", async () => {
    const fx = resolvedFetch({ success: true, truncated: false, sources: [], text: "" });
    const { output } = loadLogs(fx.fetch);
    await flush();
    assert.equal(output.textContent, "No logs captured yet.");
});

test("poll skipped while tab hidden", async () => {
    const fx = resolvedFetch({ success: true, truncated: false, sources: [], text: "x" });
    const { els, tick } = loadLogs(fx.fetch, { visibilityState: "hidden" });
    await flush();
    els["#log_autorefresh"].checked = true;
    tick();
    await flush();
    assert.equal(fx.calls.length, 1);
});

test("poll runs when visible and enabled", async () => {
    const fx = resolvedFetch({ success: true, truncated: false, sources: [], text: "x" });
    const { els, tick } = loadLogs(fx.fetch);
    await flush();
    els["#log_autorefresh"].checked = true;
    tick();
    await flush();
    assert.equal(fx.calls.length, 2);
});

test("overlapping refresh coalesces into one pending fetch", async () => {
    let resolveFirst;
    const calls = [];
    const gate = new Promise((r) => { resolveFirst = r; });
    const fetch = (url) => {
        calls.push(url);
        return calls.length === 1
            ? gate.then(() => ({ ok: true, json: () => Promise.resolve({ success: true, sources: [], text: "one", truncated: false }) }))
            : Promise.resolve({ ok: true, json: () => Promise.resolve({ success: true, sources: [], text: "two", truncated: false }) });
    };
    const { els } = loadLogs(fetch);
    els["#log_refresh"].trigger("click");
    els["#log_refresh"].trigger("click");
    assert.equal(calls.length, 1);
    resolveFirst();
    await flush();
    assert.equal(calls.length, 2);
});

test("source change during flight ignores stale response and refetches", async () => {
    let resolveFirst;
    const calls = [];
    const gate = new Promise((r) => { resolveFirst = r; });
    const fetch = (url) => {
        calls.push(url);
        return calls.length === 1
            ? gate.then(() => ({ ok: true, json: () => Promise.resolve({ success: true, sources: [], text: "STALE-ALL", truncated: false }) }))
            : Promise.resolve({ ok: true, json: () => Promise.resolve({ success: true, sources: [], text: "fresh-archive", truncated: false }) });
    };
    const { els, output } = loadLogs(fetch);
    els["#log_source"].val("src-archive");
    els["#log_source"].trigger("change");
    resolveFirst();
    await flush();
    assert.equal(calls.length, 2);
    assert.match(calls[0], /source=all/);
    assert.match(calls[1], /source=src-archive/);
    assert.equal(output.textContent, "fresh-archive");
});

test("refresh failure keeps output and flags status", async () => {
    let n = 0;
    const calls = [];
    const fetch = (url) => {
        calls.push(url);
        n += 1;
        if (n === 1) {
            return Promise.resolve({ ok: true, json: () => Promise.resolve({ success: true, sources: [], text: "good", truncated: false }) });
        }
        return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) });
    };
    const { els, output } = loadLogs(fetch);
    await flush();
    assert.equal(output.textContent, "good");
    els["#log_refresh"].trigger("click");
    await flush();
    assert.equal(output.textContent, "good");
    assert.equal(els["#logs_status"]._text.startsWith("Refresh failed"), true);
    assert.equal(els["#logs_status"].classes["is-error"], true);
});

test("scrolled-up reader is not dragged to bottom", async () => {
    const fx = resolvedFetch({ success: true, truncated: false, sources: [], text: "line1\nline2" });
    const { els, output } = loadLogs(fx.fetch);
    await flush();
    output.scrollHeight = 2000;
    output.scrollTop = 0;
    els["#log_search"].val("line1");
    els["#log_search"].trigger("input");
    assert.equal(output.scrollTop, 0);
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
