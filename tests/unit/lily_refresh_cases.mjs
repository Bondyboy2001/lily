import { readFileSync } from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

const src = readFileSync(new URL("../../cps/static/js/lily.js", import.meta.url), "utf8");

function harness({ storage = {}, hidden = false, readyState = "complete", userId = "u7" } = {}) {
    let now = 1_000_000;
    const timers = [];
    const fetches = [];
    const listeners = {};
    const store = {
        getItem: (k) => (k in storage ? storage[k] : null),
        setItem: (k, v) => { storage[k] = String(v); },
        removeItem: (k) => { delete storage[k]; },
    };
    const para = { textContent: "", children: [], appendChild(c) { this.children.push(c); } };
    const box = {
        hidden: true,
        dataset: {},
        classes: new Set(),
        classList: {
            add(...cs) { cs.forEach((c) => box.classes.add(c)); },
            remove(...cs) { cs.forEach((c) => box.classes.delete(c)); },
            contains(c) { return box.classes.has(c); },
            toggle(c, v) { (v === undefined ? !box.classes.has(c) : v) ? box.classes.add(c) : box.classes.delete(c); },
        },
        querySelector: () => ({ className: "" }),
        addEventListener() {},
        getAttribute(name) { return name === "data-error-message" ? "Library refresh failed." : null; },
    };
    const btn = {
        classes: new Set(),
        classList: {
            toggle(c, v) { (v === undefined ? !btn.classes.has(c) : v) ? btn.classes.add(c) : btn.classes.delete(c); },
            contains(c) { return btn.classes.has(c); },
        },
        setAttribute() {},
    };
    const sandbox = {
        Date, Promise, JSON, Math, console, localStorage: store,
        setTimeout: (fn, ms) => { const t = { at: now + ms, fn }; timers.push(t); return t; },
        clearTimeout: (t) => { const i = timers.indexOf(t); if (i > -1) timers.splice(i, 1); },
        document: {
            body: { getAttribute: () => userId },
            readyState,
            get hidden() { return hidden; },
            addEventListener: (ev, fn) => { (listeners[ev] = listeners[ev] || []).push(fn); },
            getElementById: (id) =>
                id === "message_library_refresh" ? box
                    : id === "library_refresh_message" ? para
                        : id === "refresh-library" ? btn : null,
            querySelector: () => null,
            createElement: () => ({ href: "", textContent: "" }),
            createTextNode: (t) => ({ text: String(t) }),
            documentElement: {},
        },
        fetch: (url, opts = {}) => {
            const entry = { url, opts };
            fetches.push(entry);
            return new Promise((resolve, reject) => { entry._resolve = resolve; entry._reject = reject; });
        },
    };
    sandbox.window = sandbox;
    sandbox.globalThis = sandbox;
    sandbox.addEventListener = (ev, fn) => { (listeners["win:" + ev] = listeners["win:" + ev] || []).push(fn); };
    sandbox.respond = (i, resp) => fetches[i]._resolve(resp);
    sandbox.rejectLast = (e) => fetches[fetches.length - 1]._reject(e || new Error("net"));
    sandbox.json = (data, status = 200) => ({
        ok: status >= 200 && status < 300, status,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve(data),
    });
    sandbox.setHidden = (v) => { hidden = v; };
    sandbox.fire = (ev) => { (listeners[ev] || []).forEach((f) => f()); };
    sandbox.tick = async (ms) => {
        now += ms;
        timers.sort((a, b) => a.at - b.at);
        while (timers.length && timers[0].at <= now) { timers.shift().fn(); await Promise.resolve(); }
        await new Promise((r) => setImmediate(r));
    };
    sandbox.fetches = fetches;
    sandbox.para = para;
    sandbox.box = box;
    sandbox.flush = () => new Promise((r) => setImmediate(r));
    vm.createContext(sandbox);
    vm.runInContext(src, sandbox);
    return sandbox;
}

const KEY = "lily.refreshJob.u7";

{
    const storage = { [KEY]: "/cwa-library-refresh/jobs/" + "a".repeat(32) };
    const h = harness({ storage });
    await h.tick(1);
    assert.equal(h.fetches.length, 1);
    assert.equal(h.fetches[0].url, storage[KEY]);
    h.respond(0, h.json({ state: "running", message: "checking" }));
    await h.flush();
    await h.tick(2100);
    assert.equal(h.fetches.length, 2);
}

{
    const storage = { [KEY]: "/status/x" };
    const h = harness({ storage });
    await h.tick(1);
    assert.equal(h.fetches.length, 1);
    await h.tick(60000);
    assert.equal(h.fetches.length, 1);
}

{
    const storage = { [KEY]: "/status/x" };
    const h = harness({ storage, hidden: true });
    await h.tick(60000);
    assert.equal(h.fetches.length, 0);
    h.setHidden(false);
    h.fire("visibilitychange");
    await h.tick(1);
    assert.equal(h.fetches.length, 1);
}

{
    const storage = { [KEY]: "/status/x" };
    const h = harness({ storage });
    await h.tick(1);
    for (let i = 0; i < 160; i++) {
        h.respond(h.fetches.length - 1, h.json({ state: "running", message: "still going" }));
        await h.flush();
        await h.tick(2100);
    }
    assert.ok(h.fetches.length > 160);
}

{
    const storage = { [KEY]: "/status/x" };
    const h = harness({ storage });
    await h.tick(1);
    h.respond(0, { ok: false, status: 500, headers: { get: () => "application/json" } });
    await h.flush();
    await h.tick(31000);
    assert.ok(h.fetches.length >= 2);
    assert.ok(h.box.classes.has("is-busy"));
    assert.equal(storage[KEY], "/status/x");
    h.respond(h.fetches.length - 1, h.json({ state: "succeeded", message: "done" }));
    await h.tick(1);
    await h.flush();
    assert.equal(storage[KEY], undefined);
    assert.ok(h.box.classes.has("is-done"));
}

{
    const storage = { [KEY]: "/status/x" };
    const h = harness({ storage });
    h.box.dataset.logsUrl = "/logs";
    h.box.dataset.logsLabel = "Logs";
    await h.tick(1);
    h.respond(0, h.json({ state: "failed", message: "2 import(s) failed; see the logs" }));
    await h.flush();
    await h.tick(60000);
    assert.equal(h.box.hidden, false);
    assert.ok(h.box.classes.has("is-error"));
    const hrefs = h.para.children.filter((c) => c.href).map((c) => c.href);
    assert.deepEqual(hrefs, ["/logs"]);
    await h.tick(1000);
    assert.equal(h.fetches.length, 1);
}

console.log("lily refresh cases: all passed");
