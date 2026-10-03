/*
 * Lily's service worker: offline reading (cps/offline.py renders this file at the app root).
 *
 * Caches
 *   lily-pages            kept books' reader and book pages
 *   lily-books            kept books' files and covers
 *   lily-static           the scripts, styles and fonts those pages use
 *   lily-offline-meta     the index of kept books (one JSON entry)
 *
 * Requests
 *   page loads            network first (4 s, then the saved copy if there is one)
 *   /static/…             a versioned URL (?q=) from the cache when kept, else the network;
 *                         unversioned ones (pdf.js' own fetches) network first
 *   book files, covers    network first, the saved copy offline (byte ranges sliced for pdf.js)
 *   everything else       untouched (API calls, uploads, POSTs)
 *
 * offline.js on the library page sends: sync {books}.
 */
const VERSION = {{ version|tojson }};
const SCOPE = {{ scope|tojson }};
const EXTRAS = {{ extras|tojson }};
{% raw %}
const PAGES_CACHE = "lily-pages";
const BOOKS_CACHE = "lily-books";
const STATIC_CACHE = "lily-static";
const META_CACHE = "lily-offline-meta";
const INDEX_URL = new URL(SCOPE + "__lily_offline_index__", self.location.origin).href;
const NAV_TIMEOUT_MS = 4000;
const REFRESH_AFTER_MS = 10 * 60 * 1000;
const FETCH_CONCURRENCY = 4;

// ---------------------------------------------------------------- helpers

function abs(url) {
  return new URL(url, self.location.origin).href;
}

function appPath(url) {
  const u = new URL(url, self.location.origin);
  if (u.origin !== self.location.origin || !u.pathname.startsWith(SCOPE)) { return null; }
  return u.pathname.slice(SCOPE.length);
}

function isStatic(url) { const p = appPath(url); return p !== null && p.startsWith("static/") && !p.endsWith("/"); }
function isBookFile(url) { const p = appPath(url); return p !== null && (p.startsWith("show/") || p.startsWith("cover/")); }

// Same-origin static files, book files and covers a page or stylesheet refers to. baseUrl must be
// absolute: new URL() throws on a relative base.
function referencedUrls(text, baseUrl, cssOnly) {
  const found = new Set();
  const add = (raw) => {
    if (!raw || raw.startsWith("data:") || raw.startsWith("#")) { return; }
    let url;
    try { url = new URL(raw.trim(), baseUrl).href; } catch (e) { return; }
    if (isStatic(url) || isBookFile(url)) { found.add(url); }
  };
  let m;
  const css = /url\(\s*["']?([^"')]+)["']?\s*\)/g;
  while ((m = css.exec(text))) { add(m[1]); }
  if (cssOnly) { return found; }
  const srcset = /srcset\s*=\s*["']([^"']+)["']/g;
  while ((m = srcset.exec(text))) { m[1].split(",").forEach((part) => add(part.trim().split(/\s+/)[0])); }
  // Attributes and string literals in inline scripts (workerSrc, bookUrl, …).
  const quoted = /["']((?:\/|https?:)[^"'\s<>]*)["']/g;
  while ((m = quoted.exec(text))) { add(m[1]); }
  return found;
}

// A response worth keeping: a real 200 for the URL asked for, not a bounce to the login page.
async function fetchOk(url) {
  const res = await fetch(url, { credentials: "same-origin" });
  if (!res.ok || res.status !== 200 || res.type === "opaqueredirect") { throw new Error("HTTP " + res.status + " for " + url); }
  if (res.redirected && appPath(res.url) !== appPath(url)) { throw new Error("Redirected away from " + url); }
  return res;
}

async function inParallel(items, worker) {
  const queue = items.slice();
  const runners = Array.from({ length: Math.min(FETCH_CONCURRENCY, queue.length) }, async () => {
    while (queue.length) { await worker(queue.shift()); }
  });
  await Promise.all(runners);
}

// Index of kept books: {id: {id, title, author, format, reader, page, cover, auto,
// savedAt, bytes, pages:[], files:[], statics:[]}}
async function readIndex() {
  const res = await (await caches.open(META_CACHE)).match(INDEX_URL);
  return res ? res.json() : {};
}

async function writeIndex(index) {
  await (await caches.open(META_CACHE)).put(INDEX_URL, new Response(JSON.stringify(index), {
    headers: { "Content-Type": "application/json" }
  }));
}

// One change to the caches at a time.
let chain = Promise.resolve();
function serial(fn) {
  const run = chain.then(fn, fn);
  chain = run.catch(() => {});
  return run;
}

// ---------------------------------------------------------------- keeping books

// Cache a page and everything it needs; returns {pages, files, statics} it touched.
async function savePage(url, touched) {
  const res = await fetchOk(url);
  const html = await res.clone().text();
  await (await caches.open(PAGES_CACHE)).put(url, res);
  touched.pages.add(abs(url));
  referencedUrls(html, abs(url), false).forEach((u) => (isStatic(u) ? touched.statics : touched.files).add(u));
}

async function saveAll(touched, refreshFiles) {
  const staticCache = await caches.open(STATIC_CACHE);
  const bookCache = await caches.open(BOOKS_CACHE);
  // Stylesheets name fonts and images of their own.
  const styles = [...touched.statics].filter((u) => /\.css(\?|$)/.test(u));
  await inParallel(styles, async (u) => {
    try {
      const res = (await staticCache.match(u)) || (await fetchOk(u));
      const text = await res.clone().text();
      await staticCache.put(u, res);
      referencedUrls(text, u, true).forEach((v) => touched.statics.add(v));
    } catch (e) { /* a missing stylesheet only costs looks */ }
  });
  await inParallel([...touched.statics], async (u) => {
    if (await staticCache.match(u)) { return; }
    try { await staticCache.put(u, await fetchOk(u)); } catch (e) { /* optional asset */ }
  });
  let bytes = 0;
  await inParallel([...touched.files], async (u) => {
    let hit = refreshFiles ? null : await bookCache.match(u);
    if (!hit) {
      try {
        await bookCache.put(u, await fetchOk(u));
        hit = await bookCache.match(u);
      } catch (e) {
        // The book file itself must be there; an extra cover size may not be.
        if (/\/show\//.test(u)) { throw e; }
        touched.files.delete(u);
        return;
      }
    }
    bytes += Number(hit.headers.get("Content-Length")) || (await hit.clone().blob()).size;
  });
  return bytes;
}

async function keepBook(book, how) {
  const index = await readIndex();
  const old = index[book.id] || {};
  const touched = { pages: new Set(), files: new Set(), statics: new Set() };
  await savePage(book.reader, touched);
  try { await savePage(book.page, touched); } catch (e) { /* the reader alone is enough */ }
  if (book.cover) { touched.files.add(abs(book.cover)); }
  (EXTRAS[book.format] || []).concat(EXTRAS.all || []).forEach((u) => touched.statics.add(abs(u)));
  const bytes = await saveAll(touched, how.refreshFiles);
  const entry = {
    id: book.id, title: book.title, author: book.author || "", format: book.format,
    reader: abs(book.reader), page: abs(book.page), cover: book.cover ? abs(book.cover) : "",
    auto: how.auto === undefined ? !!old.auto : how.auto,
    excluded: false, savedAt: Date.now(), bytes: bytes,
    pages: [...touched.pages], files: [...touched.files], statics: [...touched.statics]
  };
  // Drop what an earlier copy used and this one doesn't (e.g. an asset version from before a deploy).
  const fresh = await readIndex();
  fresh[book.id] = entry;
  await removeUnused(old, fresh);
  await writeIndex(fresh);
  return summary(entry);
}

async function removeUnused(old, index) {
  const live = { pages: new Set(), files: new Set(), statics: new Set() };
  Object.values(index).forEach((e) => {
    if (e.excluded) { return; }
    e.pages.forEach((u) => live.pages.add(u));
    e.files.forEach((u) => live.files.add(u));
    e.statics.forEach((u) => live.statics.add(u));
  });
  const pairs = [[PAGES_CACHE, old.pages || [], live.pages], [BOOKS_CACHE, old.files || [], live.files],
                 [STATIC_CACHE, old.statics || [], live.statics]];
  for (const [name, urls, keep] of pairs) {
    const cache = await caches.open(name);
    await Promise.all(urls.filter((u) => !keep.has(u)).map((u) => cache.delete(u)));
  }
}

async function dropBook(id) {
  const index = await readIndex();
  const old = index[id];
  if (!old) { return null; }
  delete index[id];
  await removeUnused(old, index);
  await writeIndex(index);
  return null;
}

async function patchEntry(id, fields) {
  const index = await readIndex();
  if (!index[id]) { return; }
  Object.assign(index[id], fields);
  await writeIndex(index);
}

// The books in progress, as the library page hands them over: keep those books and let go of the ones that left.
// An "excluded" entry is a mark left by the removed Offline page's trash button; it is kept again.
async function syncAuto(books) {
  const wanted = new Map(books.map((b) => [String(b.id), b]));
  for (const [id, entry] of Object.entries(await readIndex())) {
    if (wanted.has(id)) { continue; }
    if (entry.auto) {
      await dropBook(id);
    }
  }
  for (const book of books) {
    const entry = (await readIndex())[book.id];
    if (!entry || entry.excluded || Date.now() - entry.savedAt > REFRESH_AFTER_MS) {
      try { await keepBook(book, { auto: true }); } catch (e) { /* tried again on the next visit */ }
    } else if (!entry.auto) {
      await patchEntry(book.id, { auto: true });
    }
  }
  return null;
}

// A kept page loaded online: save the new copy (and any new asset versions) in the background.
async function refreshKept(url) {
  const index = await readIndex();
  const entry = Object.values(index).find((e) => !e.excluded && (e.reader === url || e.page === url));
  if (!entry || Date.now() - entry.savedAt < REFRESH_AFTER_MS) { return; }
  await keepBook(entry, {});
}

// ---------------------------------------------------------------- lifecycle

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    // The Offline page's own cache (lily-shell-*) is gone with the page.
    await Promise.all(names.filter((n) => n.startsWith("lily-shell-")).map((n) => caches.delete(n)));
    await self.clients.claim();
  })());
});

self.addEventListener("message", (event) => {
  const port = event.ports && event.ports[0];
  const msg = event.data || {};
  const work = (async () => {
    switch (msg.type) {
      case "sync": return serial(() => syncAuto(msg.books || []));
      default: throw new Error("Unknown message " + msg.type);
    }
  })();
  event.waitUntil(work.then((result) => port && port.postMessage({ ok: true, result: result }),
                            (error) => port && port.postMessage({ ok: false, error: String(error && error.message || error) })));
});

// ---------------------------------------------------------------- requests

async function fromCaches(url, names, ignoreSearch) {
  for (const name of names) {
    const hit = await (await caches.open(name)).match(url, { ignoreSearch: !!ignoreSearch });
    if (hit) { return hit; }
  }
  return null;
}

async function navigate(event) {
  const request = event.request;
  const network = fetch(request);
  const timeout = new Promise((resolve) => setTimeout(() => resolve("timeout"), NAV_TIMEOUT_MS));
  try {
    const first = await Promise.race([network, timeout]);
    if (first !== "timeout") {
      if (first.ok && !first.redirected) {
        event.waitUntil(serial(() => refreshKept(abs(request.url))).catch(() => {}));
      }
      return first;
    }
    const saved = await fromCaches(request.url, [PAGES_CACHE], false);
    return saved || (await network);
  } catch (error) {
    const saved = await fromCaches(request.url, [PAGES_CACHE], false);
    return saved || Response.error();
  }
}

async function staticFile(request) {
  const versioned = /[?&]q=/.test(request.url);
  if (versioned) {
    const hit = await fromCaches(request.url, [STATIC_CACHE], false);
    if (hit) { return hit; }
  }
  try {
    return await fetch(request);
  } catch (error) {
    const hit = await fromCaches(request.url, [STATIC_CACHE], true);
    if (hit) { return hit; }
    throw error;
  }
}

// pdf.js asks for byte ranges; answer them from the saved whole file.
async function sliced(response, range) {
  const m = /bytes=(\d*)-(\d*)/.exec(range || "");
  if (!m) { return response; }
  const blob = await response.blob();
  const size = blob.size;
  let start = m[1] === "" ? Math.max(0, size - Number(m[2])) : Number(m[1]);
  let end = m[1] !== "" && m[2] !== "" ? Math.min(Number(m[2]), size - 1) : size - 1;
  if (start >= size || start > end) {
    return new Response(null, { status: 416, headers: { "Content-Range": "bytes */" + size } });
  }
  return new Response(blob.slice(start, end + 1), {
    status: 206,
    headers: {
      "Content-Type": response.headers.get("Content-Type") || "application/octet-stream",
      "Content-Range": "bytes " + start + "-" + end + "/" + size,
      "Content-Length": String(end - start + 1),
      "Accept-Ranges": "bytes"
    }
  });
}

async function bookFile(request) {
  try {
    return await fetch(request);
  } catch (error) {
    const hit = await fromCaches(request.url, [BOOKS_CACHE], false) ||
                await fromCaches(request.url, [BOOKS_CACHE], true);
    if (!hit) { throw error; }
    return request.headers.get("Range") ? sliced(hit, request.headers.get("Range")) : hit;
  }
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") { return; }
  const path = appPath(request.url);
  if (path === null || path === "sw.js") { return; }
  if (request.mode === "navigate") {
    event.respondWith(navigate(event));
  } else if (isStatic(request.url)) {
    event.respondWith(staticFile(request));
  } else if (isBookFile(request.url)) {
    event.respondWith(bookFile(request));
  }
});
{% endraw %}
