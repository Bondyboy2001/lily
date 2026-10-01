/* Lily reader service worker: keep the open book readable through a network drop.
 *
 * Served at <root>/reader-sw.js and registered by the reader pages with scope <root>/read/,
 * so it controls reader pages only; the library, login and settings pages never go
 * through it. What it caches, all network-first with a cached fallback:
 *   - reader pages (<root>/read/<id>/<format>), only when the server answered 200 and
 *     did not redirect (a redirect means signed out: the cached copies are dropped);
 *   - static files (<root>/static/...) the reader loads;
 *   - EPUB files (<root>/show/<id>/epub|kepub/...), the newest few only.
 * Everything else (progress and bookmark calls, other formats, POSTs) is left alone.
 * Bump VERSION to drop every cache on the next visit. */
"use strict";

var VERSION = "lily-reader-v1";
var SHELL = VERSION + "-shell";
var BOOKS = VERSION + "-books";
var MAX_BOOKS = 3;
// A Tailscale drop can leave a request hanging rather than failing; give up on the
// network after this long and use the cached copy.
var NETWORK_TIMEOUT = 6000;
// After one request has fallen back to the cache, skip the network for a while so the
// page does not wait NETWORK_TIMEOUT once per file.
var OFFLINE_GRACE = 30000;

var root = new URL("../", self.registration.scope).pathname;
var offlineUntil = 0;

self.addEventListener("install", function () {
    self.skipWaiting();
});

self.addEventListener("activate", function (event) {
    event.waitUntil(caches.keys().then(function (names) {
        return Promise.all(names.filter(function (name) {
            return name.indexOf("lily-reader-") === 0 && name !== SHELL && name !== BOOKS;
        }).map(function (name) {
            return caches.delete(name);
        }));
    }).then(function () {
        return self.clients.claim();
    }));
});

function kindOfUrl(href, navigate) {
    var url = new URL(href, self.location.href);
    if (url.origin !== self.location.origin || url.pathname.indexOf(root) !== 0) {
        return null;
    }
    var path = url.pathname.slice(root.length);
    if (/^read\/\d+\/[\w.]+$/.test(path)) {
        return navigate ? "page" : null;
    }
    if (navigate) {
        return null;
    }
    if (path.indexOf("static/") === 0) {
        return "static";
    }
    if (/^show\/\d+\/k?epub(\/|$)/i.test(path)) {
        return "book";
    }
    return null;
}

function kindOf(request) {
    if (request.method !== "GET" || request.headers.has("Range")) {
        return null;
    }
    return kindOfUrl(request.url, request.mode === "navigate");
}

/** Reader pages are cached once per book, whatever the query string (?from=search ...). */
function cacheKey(kind, href) {
    var url = new URL(href);
    url.hash = "";
    if (kind === "page") {
        url.search = "";
    }
    return url.href;
}

function trimBooks(cache) {
    return cache.keys().then(function (keys) {
        // Cache keys keep insertion order; a re-put moves the book to the end.
        return Promise.all(keys.slice(0, Math.max(0, keys.length - MAX_BOOKS)).map(function (key) {
            return cache.delete(key);
        }));
    });
}

function store(kind, href, response) {
    var name = kind === "book" ? BOOKS : SHELL;
    var key = cacheKey(kind, href);
    return caches.open(name).then(function (cache) {
        return cache.delete(key).then(function () {
            return cache.put(key, response);
        }).then(function () {
            return kind === "book" ? trimBooks(cache) : null;
        });
    }).catch(function () {
        // Quota or a body error: the network copy is still served.
    });
}

function cacheable(kind, response) {
    if (!response || response.status !== 200 || response.type !== "basic" || response.redirected) {
        return false;
    }
    var type = response.headers.get("Content-Type") || "";
    if (kind === "page") {
        return type.indexOf("text/html") === 0;
    }
    if (kind === "book") {
        // "File not in Database" and error pages come back as text/html.
        return type.indexOf("text/") !== 0;
    }
    return true;
}

function signedOut(response) {
    return response.type === "opaqueredirect" || response.redirected ||
        response.status === 401 || response.status === 403;
}

function keepAlive(event, promise) {
    try {
        event.waitUntil(promise);
    } catch (e) {
        // The response already went out from the cache; the promise still runs.
    }
}

function fromNetwork(kind, event) {
    return fetch(event.request).then(function (response) {
        offlineUntil = 0;
        if (kind === "page" && signedOut(response)) {
            keepAlive(event, caches.delete(SHELL).then(function () { return caches.delete(BOOKS); }));
        } else if (cacheable(kind, response)) {
            keepAlive(event, store(kind, event.request.url, response.clone()));
        }
        return response;
    });
}

function fromCache(kind, request) {
    return caches.match(cacheKey(kind, request.url), {ignoreVary: true});
}

function handle(kind, event) {
    if (Date.now() < offlineUntil) {
        return fromCache(kind, event.request).then(function (cached) {
            return cached || fromNetwork(kind, event);
        });
    }
    var network = fromNetwork(kind, event);
    network.catch(function () {});
    var timer;
    var timedOut = new Promise(function (resolve, reject) {
        timer = setTimeout(function () { reject(new Error("timeout")); }, NETWORK_TIMEOUT);
    });
    return Promise.race([network, timedOut]).then(function (response) {
        clearTimeout(timer);
        return response;
    }, function () {
        clearTimeout(timer);
        offlineUntil = Date.now() + OFFLINE_GRACE;
        // Nothing cached: keep waiting on the network, which fails as the browser would.
        return fromCache(kind, event.request).then(function (cached) {
            return cached || network;
        });
    });
}

self.addEventListener("fetch", function (event) {
    var kind = kindOf(event.request);
    if (kind) {
        event.respondWith(handle(kind, event));
    }
});

// The first reader page opened before the worker existed loaded its files past it; the page
// sends their URLs (reader-offline.js) so they are cached now rather than on the next visit.
self.addEventListener("message", function (event) {
    var data = event.data || {};
    if (data.type !== "lily-reader-warm" || !Array.isArray(data.urls)) {
        return;
    }
    event.waitUntil(Promise.all(data.urls.slice(0, 60).map(function (href, position) {
        var kind = kindOfUrl(href, position === 0);
        if (!kind) {
            return null;
        }
        return caches.match(cacheKey(kind, href), {ignoreVary: true}).then(function (cached) {
            if (cached) {
                return null;
            }
            return fetch(href, {credentials: "same-origin"}).then(function (response) {
                return cacheable(kind, response) ? store(kind, href, response) : null;
            });
        }).catch(function () {});
    })));
});
