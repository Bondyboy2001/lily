import { readFileSync } from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

const src = readFileSync(new URL("../../cps/static/js/reading/progress-sync.js", import.meta.url), "utf8");

function harness({ storage = {}, responses = [], failFetch = false, hidden = false } = {}) {
    let now = 1_700_000_000_000;
    const timers = [];
    const fetches = [];
    const listeners = {};
    const store = {
        getItem: (k) => (k in storage ? storage[k] : null),
        setItem: (k, v) => { storage[k] = String(v); },
        removeItem: (k) => { delete storage[k]; },
    };
    const sandbox = {
        Date: { now: () => now, parse: Date.parse },
        setTimeout: (fn, ms) => { const t = { at: now + ms, fn }; timers.push(t); return t; },
        clearTimeout: (t) => { const i = timers.indexOf(t); if (i > -1) timers.splice(i, 1); },
        Promise, JSON, Math, parseFloat, isNaN, Number,
        console,
    };
    sandbox.window = sandbox;
    sandbox.document = {
        querySelector: () => null,
        addEventListener: (ev, fn) => { (listeners["doc:" + ev] = listeners["doc:" + ev] || []).push(fn); },
        get visibilityState() { return hidden ? "hidden" : "visible"; },
    };
    sandbox.addEventListener = (ev, fn) => { (listeners["win:" + ev] = listeners["win:" + ev] || []).push(fn); };
    sandbox.localStorage = store;
    sandbox.fetch = (url, opts = {}) => {
        const entry = { url, opts };
        fetches.push(entry);
        return new Promise((resolve, reject) => { entry._resolve = resolve; entry._reject = reject; });
    };
    sandbox.setHidden = (v) => { hidden = v; };
    const nextResponse = () => responses.length ? responses.shift() : { ok: true, status: 200, headers: { get: () => "application/json" }, json: () => Promise.resolve({}) };
    sandbox.respondNext = (resp) => { responses.unshift(resp); };
    sandbox.pendingFetches = fetches;
    sandbox.answer = (i = 0, resp = null) => {
        const f = fetches[i];
        if (!resp) {
            const sent = f.opts && f.opts.body ? JSON.parse(f.opts.body) : {};
            resp = { ok: true, status: 200, headers: { get: () => "application/json" },
                     json: () => Promise.resolve({ cfi: sent.cfi, percent: sent.percent,
                                                   format: sent.format, updated: now + 10 }) };
        }
        f._resolve(resp);
    };
    sandbox.failNext = () => fetches[fetches.length - 1]._reject(new Error("offline"));
    sandbox.now = () => now;
    sandbox.tick = async (ms) => {
        now += ms;
        timers.sort((a, b) => a.at - b.at);
        while (timers.length && timers[0].at <= now) { timers.shift().fn(); await Promise.resolve(); }
        await new Promise((r) => setImmediate(r));
    };
    sandbox.fire = (target, ev) => { (listeners[target + ":" + ev] || []).forEach((f) => f()); };
    sandbox.storage = storage;
    vm.createContext(sandbox);
    vm.runInContext(src, sandbox);
    return sandbox;
}

const OPTS = { url: "/ajax/progress/7?format=epub", storageKey: "u1.lib.7.epub", format: "epub", enabled: true };
const PENDING_KEY = "lily.progress.u1.lib.7.epub";

{
    const h = harness();
    const sync = h.LilyProgress.create(OPTS);
    sync.save("epubcfi(/2)", 0.2);
    assert.equal(h.pendingFetches.length, 0);
    await h.tick(4500);
    assert.equal(h.pendingFetches.length, 1);
    const body = JSON.parse(h.pendingFetches[0].opts.body);
    assert.equal(body.format, "epub");
    assert.equal(body.cfi, "epubcfi(/2)");
    h.answer();
    await h.tick(1);
    assert.equal(JSON.parse(h.storage[PENDING_KEY]).pending, false);
}

{
    const h = harness();
    const sync = h.LilyProgress.create(OPTS);
    sync.save("epubcfi(/3)", 0.3);
    await h.tick(4500);
    h.failNext();
    await h.tick(1);
    const local = JSON.parse(h.storage[PENDING_KEY]);
    assert.equal(local.pending, true);
    assert.equal(local.cfi, "epubcfi(/3)");
    await h.tick(40000);
    assert.ok(h.pendingFetches.length >= 2);
}

{
    const h = harness();
    const sync = h.LilyProgress.create(OPTS);
    sync.save("epubcfi(/4)", 0.4);
    await h.tick(4500);
    sync.save("epubcfi(/9)", 0.9);
    h.answer(0, { ok: true, status: 200, headers: { get: () => "application/json" },
                  json: () => Promise.resolve({ cfi: "epubcfi(/4)", percent: 0.4,
                                                format: "epub", updated: h.now() + 5000 }) });
    await h.tick(1);
    const local = JSON.parse(h.storage[PENDING_KEY]);
    assert.equal(local.pending, true);
    assert.equal(local.cfi, "epubcfi(/9)");
    await h.tick(100);
    assert.equal(h.pendingFetches.length, 2);
}

{
    const h = harness();
    const sync = h.LilyProgress.create(OPTS);
    sync.save("epubcfi(/5)", 0.5);
    await h.tick(4500);
    h.answer(0, { ok: false, status: 401, headers: { get: () => "text/html" }, json: () => Promise.resolve({}) });
    await h.tick(60000);
    assert.equal(h.pendingFetches.length, 1);
    assert.equal(JSON.parse(h.storage[PENDING_KEY]).pending, true);
}

{
    const h = harness();
    const sync = h.LilyProgress.create(OPTS);
    sync.save("epubcfi(/6)", 0.6);
    await h.tick(4500);
    h.answer(0, { ok: true, status: 200, headers: { get: () => "text/html" }, json: () => Promise.resolve({}) });
    await h.tick(1);
    assert.equal(JSON.parse(h.storage[PENDING_KEY]).pending, true);
}

{
    for (const bad of [
        { ok: true, status: 200, headers: { get: () => "application/json" },
          json: () => Promise.resolve({}) },
        { ok: true, status: 200, headers: { get: () => "application/json" },
          json: () => Promise.resolve({ cfi: "epubcfi(/other)", percent: 0.5, format: "epub", updated: 5 }) },
        { ok: true, status: 200, headers: { get: () => "application/json" },
          json: () => Promise.resolve({ cfi: "epubcfi(/12)", percent: 0.5, format: "pdf", updated: 5 }) },
        { ok: true, status: 200, headers: { get: () => "application/json" },
          json: () => Promise.reject(new SyntaxError("bad json")) },
    ]) {
        const h = harness();
        const sync = h.LilyProgress.create(OPTS);
        sync.save("epubcfi(/12)", 0.5);
        await h.tick(4500);
        h.answer(0, bad);
        await h.tick(1);
        const local = JSON.parse(h.storage[PENDING_KEY]);
        assert.equal(local.pending, true, JSON.stringify(bad));
        assert.equal(local.cfi, "epubcfi(/12)");
    }
}

{
    const h = harness();
    const sync = h.LilyProgress.create(OPTS);
    sync.save("epubcfi(/13)", 0.5);
    await h.tick(4500);
    h.answer(0, { ok: false, status: 401, headers: { get: () => "application/json" },
                  json: () => Promise.resolve({}) });
    await h.tick(60000);
    assert.equal(h.pendingFetches.length, 1);
    h.fire("win", "online");
    assert.equal(h.pendingFetches.length, 2);
    h.answer(1);
    await h.tick(1);
    assert.equal(JSON.parse(h.storage[PENDING_KEY]).pending, false);
}

{
    const storage = {};
    const h = harness({ storage });
    const sync = h.LilyProgress.create(OPTS);
    sync.save("epubcfi(/old)", 0.2);
    const loading = sync.load();
    h.answer(0, { ok: true, status: 200, headers: { get: () => "application/json" },
                  json: () => Promise.resolve({ cfi: "epubcfi(/srv)", percent: 0.9,
                                                format: "epub", updated: Date.now() + 60000 }) });
    const pos = await loading;
    assert.equal(pos.cfi, "epubcfi(/srv)");
    assert.equal(JSON.parse(storage[PENDING_KEY]).pending, false);
    await h.tick(60000);
    assert.equal(h.pendingFetches.length, 1);
}

{
    const h = harness();
    const sync = h.LilyProgress.create(OPTS);
    sync.save("epubcfi(/local)", 0.7);
    const loading = sync.load();
    await h.tick(2600);
    const pos = await loading;
    assert.equal(pos.cfi, "epubcfi(/local)");
    assert.equal(JSON.parse(h.storage[PENDING_KEY]).cfi, "epubcfi(/local)");
}

{
    const h = harness();
    const statuses = [];
    const sync = h.LilyProgress.create({
        ...OPTS, enabled: false,
        statusEl: { getAttribute: () => null, set textContent(v) { statuses.push(v); } },
    });
    sync.save("epubcfi(/x)", 0.4);
    await h.tick(60000);
    assert.equal(h.pendingFetches.length, 0);
    assert.equal(JSON.parse(h.storage[PENDING_KEY]).pending, false);
    assert.deepEqual(statuses, []);
    h.fire("win", "pagehide");
    h.fire("win", "online");
    await h.tick(100);
    assert.equal(h.pendingFetches.length, 0);
}

{
    const storage = {};
    const h1 = harness({ storage });
    h1.LilyProgress.create(OPTS).save("epubcfi(/7)", 0.7);
    await h1.tick(1);
    const h2 = harness({ storage });
    const sync2 = h2.LilyProgress.create(OPTS);
    const loading = sync2.load();
    h2.answer(0, { ok: true, status: 200, headers: { get: () => "application/json" },
                   json: () => Promise.resolve({ cfi: "srv", percent: 0.1, updated: 1 }) });
    const pos = await loading;
    assert.equal(pos.cfi, "epubcfi(/7)");
    await h2.tick(4500);
    assert.equal(h2.pendingFetches.length, 2);
    assert.equal(h2.pendingFetches[1].opts.method, "POST");
}

{
    const h = harness();
    h.storage = null;
    h.localStorage = null;
    const sync = h.LilyProgress.create(OPTS);
    sync.save("epubcfi(/8)", 0.8);
    await h.tick(4500);
    h.answer();
    await h.tick(1);
}

{
    const h = harness();
    const sync = h.LilyProgress.create(OPTS);
    sync.save("epubcfi(/10)", 0.9);
    h.fire("win", "pagehide");
    assert.equal(h.pendingFetches.length, 1);
    assert.equal(h.pendingFetches[0].opts.keepalive, true);
}

{
    const h = harness({ hidden: true });
    const sync = h.LilyProgress.create(OPTS);
    sync.save("epubcfi(/11)", 0.1);
    await h.tick(60000);
    assert.equal(h.pendingFetches.length, 0);
    h.setHidden(false);
    h.fire("doc", "visibilitychange");
    await h.tick(4500);
    assert.equal(h.pendingFetches.length, 1);
}

console.log("progress-sync cases: all passed");
