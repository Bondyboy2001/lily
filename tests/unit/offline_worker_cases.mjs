// Runs templates/sw.js's keeping rules against in-memory caches: Save offline pins a book,
// the library's sync keeps the books in progress, and removing a book by hand keeps it off
// the device while it stays in progress. Exits non-zero on the first failure.
import { readFileSync } from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

const template = readFileSync(new URL("../../cps/templates/sw.js", import.meta.url), "utf8");
const src = template
  .replace("{{ version|tojson }}", '"test"')
  .replace("{{ scope|tojson }}", '"/"')
  .replace("{{ extras|tojson }}", "{}")
  .replace("{% raw %}", "").replace("{% endraw %}", "");

function worker() {
  const stores = new Map();
  const listeners = {};
  const cacheFor = (name) => {
    if (!stores.has(name)) { stores.set(name, new Map()); }
    const map = stores.get(name);
    const key = (req) => (typeof req === "string" ? req : req.url);
    return {
      match: async (req) => { const r = map.get(key(req)); return r ? r.clone() : undefined; },
      put: async (req, res) => { map.set(key(req), res); },
      delete: async (req) => map.delete(key(req)),
    };
  };
  const sandbox = {
    self: { location: { origin: "https://lily.test" }, skipWaiting: async () => {}, clients: { claim: async () => {} },
            addEventListener: (ev, fn) => { listeners[ev] = fn; } },
    caches: { open: async (name) => cacheFor(name), keys: async () => [...stores.keys()] },
    fetch: async (url) => new Response("<html><body>" + url + "</body></html>", { status: 200 }),
    Response, URL, Date, Promise, Set, Map, JSON, Number, Object, Array, String, Error, Math, console,
    setTimeout,
  };
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox);
  const send = (message) => new Promise((resolve, reject) => {
    const port = { postMessage: (reply) => (reply.ok ? resolve(reply.result) : reject(new Error(reply.error))) };
    listeners.message({ data: message, ports: [port], waitUntil: () => {} });
  });
  const saved = async (id) => (await send({ type: "status", ids: [id] }))[String(id)];
  const files = () => [...(stores.get("lily-books") || new Map()).keys()];
  return { send, saved, files };
}

const book = (id) => ({ id, title: "Book " + id, author: "", format: "epub",
                         reader: "/read/" + id + "/epub", page: "/book/" + id, cover: "/cover/" + id + "/md" });

const cases = {
  async "Save offline keeps a book that isn't being read"() {
    const w = worker();
    assert.equal(await w.saved(1), false);
    assert.equal(await w.send({ type: "keep", book: book(1) }), true);
    assert.equal(await w.saved(1), true);
    // The library's sync doesn't let go of a book saved by hand
    await w.send({ type: "sync", books: [] });
    assert.equal(await w.saved(1), true);
  },

  async "Remove offline copy takes it off the device"() {
    const w = worker();
    await w.send({ type: "keep", book: book(1) });
    assert.ok(w.files().some((u) => u.endsWith("/cover/1/md")));
    assert.equal(await w.send({ type: "drop", id: 1 }), false);
    assert.equal(await w.saved(1), false);
    assert.ok(!w.files().some((u) => u.endsWith("/cover/1/md")));
  },

  async "books in progress come and go on their own"() {
    const w = worker();
    await w.send({ type: "sync", books: [book(2)] });
    assert.equal(await w.saved(2), true);
    await w.send({ type: "sync", books: [] });
    assert.equal(await w.saved(2), false);
  },

  async "a book saved by hand stays after it leaves the Reading list"() {
    const w = worker();
    await w.send({ type: "sync", books: [book(3)] });
    await w.send({ type: "keep", book: book(3) });
    await w.send({ type: "sync", books: [] });
    assert.equal(await w.saved(3), true);
  },

  async "a book removed while in progress stays off until it leaves the list"() {
    const w = worker();
    await w.send({ type: "sync", books: [book(4)] });
    await w.send({ type: "drop", id: 4 });
    await w.send({ type: "sync", books: [book(4)] });
    assert.equal(await w.saved(4), false);
    // Once it leaves, the next time it is in progress it is kept again
    await w.send({ type: "sync", books: [] });
    await w.send({ type: "sync", books: [book(4)] });
    assert.equal(await w.saved(4), true);
    // And Save offline brings it back at once
    await w.send({ type: "drop", id: 4 });
    await w.send({ type: "keep", book: book(4) });
    assert.equal(await w.saved(4), true);
  },
};

for (const [name, run] of Object.entries(cases)) {
  try {
    await run();
    console.log("ok   " + name);
  } catch (error) {
    console.error("FAIL " + name + "\n" + (error && error.stack || error));
    process.exit(1);
  }
}
