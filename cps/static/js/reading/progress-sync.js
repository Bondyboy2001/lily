/* Lily reading-progress sync.
 *
 * Keeps a reader's position in localStorage (instant, works offline) and mirrors it to
 * the server so it follows the user across devices:
 *   GET  <url>  -> {"cfi": str, "percent": 0..1, "updated": iso-string | epoch}
 *   POST <url>  <- {"cfi": str, "percent": 0..1, "seconds": int, "pages": number?}   (X-CSRFToken header)
 * "seconds" is the active reading time since the last post: the tab visible and the reader
 * used (a page turn, scroll, key or tap) within IDLE_CAP, or with `background`, the audio
 * playing. The server needs enough of it before a book counts as finished (web.py).
 * "pages" is the book's length when the reader knows it and the position doesn't say (epub).
 * On open, whichever copy is newer wins. Every server error (offline, 401, 404, HTML
 * login page...) is swallowed and the local copy is used instead.
 *
 * Usage:
 *   var sync = LilyProgress.create({url: "/ajax/progress/12", storageKey: "12.epub", enabled: true});
 *   sync.load().then(function (pos) { if (pos) { ...jump to pos.cfi / pos.percent... } });
 *   sync.save(cfi, percent);   // queued POST, flushed on pagehide / tab hidden
 * Options: pages: function () -> number|null; background: true to count time with the tab hidden (audio).
 */
(function (window) {
    "use strict";

    var LOCAL_PREFIX = "lily.progress.";
    var POST_DELAY = 4000;
    var LOAD_TIMEOUT = 2500;
    var RETRY_MAX = 30000;
    var STATUS_CLEAR = 2500;
    // Longer than this without a page turn, scroll or key counts as away, not reading
    var IDLE_CAP = 120000;
    // One post claims at most this much reading (the server refuses more)
    var SECONDS_MAX = 3600;

    function csrfToken() {
        var input = document.querySelector("input[name='csrf_token']");
        if (input && input.value) {
            return input.value;
        }
        var meta = document.querySelector("meta[name='csrf-token']");
        return meta ? meta.getAttribute("content") || "" : "";
    }

    function toMillis(updated) {
        if (updated === null || updated === undefined || updated === "") {
            return 0;
        }
        if (typeof updated === "number") {
            // Seconds since the epoch look tiny next to milliseconds.
            return updated < 1e12 ? updated * 1000 : updated;
        }
        var text = String(updated);
        // Naive ISO timestamps from the server are UTC.
        if (/^\d{4}-\d\d-\d\d[ T]\d\d:\d\d(:\d\d(\.\d+)?)?$/.test(text)) {
            text = text.replace(" ", "T") + "Z";
        }
        var parsed = Date.parse(text);
        return isNaN(parsed) ? 0 : parsed;
    }

    function clampPercent(percent) {
        percent = parseFloat(percent);
        if (isNaN(percent)) {
            return null;
        }
        return Math.min(1, Math.max(0, percent));
    }

    function normalise(pos) {
        if (!pos || typeof pos !== "object") {
            return null;
        }
        var cfi = typeof pos.cfi === "string" ? pos.cfi : "";
        var percent = clampPercent(pos.percent);
        if (!cfi && percent === null) {
            return null;
        }
        return {cfi: cfi, percent: percent, updated: toMillis(pos.updated),
                format: typeof pos.format === "string" ? pos.format : null,
                pending: pos.pending === true};
    }

    function readLocal(key) {
        try {
            return normalise(JSON.parse(window.localStorage.getItem(LOCAL_PREFIX + key)));
        } catch (e) {
            return null;
        }
    }

    function writeLocal(key, pos) {
        try {
            window.localStorage.setItem(LOCAL_PREFIX + key, JSON.stringify(pos));
        } catch (e) {
            // Storage full or blocked: the server copy still works.
        }
    }

    function fetchServer(url) {
        if (!window.fetch) {
            return Promise.resolve(null);
        }
        var request = window.fetch(url, {
            credentials: "same-origin",
            headers: {"Accept": "application/json"}
        }).then(function (response) {
            var type = response.headers.get("Content-Type") || "";
            if (!response.ok || type.indexOf("json") === -1) {
                return null;
            }
            return response.json().then(normalise);
        }).catch(function () {
            return null;
        });
        var timeout = new Promise(function (resolve) {
            setTimeout(function () { resolve(null); }, LOAD_TIMEOUT);
        });
        return Promise.race([request, timeout]);
    }

    function create(options) {
        var url = options.url;
        var key = options.storageKey || url;
        var format = options.format || null;
        var statusEl = options.statusEl || null;
        var onStatus = typeof options.onStatus === "function" ? options.onStatus : null;
        var enabled = !!(options.enabled && url);
        var pending = null;
        var timer = null;
        var retryDelay = POST_DELAY;
        var inflight = false;
        var authFailed = false;
        var pages = typeof options.pages === "function" ? options.pages : null;
        var background = !!options.background;
        // Reading time not yet sent (ms), and when the reader was last used
        var active = 0;
        var lastUsed = null;
        // The newest position, which a post of reading time alone repeats
        var last = null;

        function used() {
            var now = Date.now();
            if (!background && document.visibilityState === "hidden") {
                lastUsed = null;
                return;
            }
            if (lastUsed !== null) {
                active += Math.min(now - lastUsed, IDLE_CAP);
            }
            lastUsed = now;
        }

        if (background || document.visibilityState !== "hidden") {
            lastUsed = Date.now();
        }
        ["keydown", "pointerdown", "wheel", "touchstart", "scroll"].forEach(function (name) {
            document.addEventListener(name, used, {capture: true, passive: true});
        });

        var stored = readLocal(key);
        if (stored && stored.pending) {
            pending = stored;
        }

        // The visible line stays empty while saves go through; it only appears once a
        // save has failed, and clears itself shortly after the retry succeeds.
        var shownPending = false;
        var clearTimer = null;

        function status(text) {
            if (onStatus) {
                try { onStatus(text); } catch (e) { /* status callback must not break the reader */ }
            }
            if (!statusEl) {
                return;
            }
            if (clearTimer) {
                clearTimeout(clearTimer);
                clearTimer = null;
            }
            if (text === "failed") {
                shownPending = true;
                statusEl.textContent = statusEl.getAttribute("data-pending-text") || "Saved on this device; waiting to sync";
            } else if (text === "synced" && shownPending) {
                shownPending = false;
                statusEl.textContent = statusEl.getAttribute("data-synced-text") || "Synced";
                clearTimer = setTimeout(function () { statusEl.textContent = ""; }, STATUS_CLEAR);
            } else if (text === "synced") {
                statusEl.textContent = "";
            }
        }

        function schedule(delay) {
            if (timer || !pending || authFailed) {
                return;
            }
            timer = setTimeout(function () {
                timer = null;
                if (document.visibilityState === "hidden") {
                    return;
                }
                post(false);
            }, delay);
        }

        function ackMatches(sent, acked) {
            return !!(acked && acked.cfi === sent.cfi && acked.percent === sent.percent
                      && (acked.format === undefined || acked.format === null || acked.format === format));
        }

        function post(keepalive) {
            if (!enabled || !pending || !window.fetch || inflight || authFailed) {
                return;
            }
            if (timer) {
                clearTimeout(timer);
                timer = null;
            }
            inflight = true;
            var sent = pending;
            var seconds = Math.min(SECONDS_MAX, Math.floor(active / 1000));
            active -= seconds * 1000;
            var payload = {cfi: sent.cfi, percent: sent.percent === null ? 0 : sent.percent,
                           format: format, seconds: seconds};
            var size = null;
            try { size = pages ? pages() : null; } catch (e) { size = null; }
            if (typeof size === "number" && isFinite(size) && size > 0) {
                payload.pages = size;
            }
            var body = JSON.stringify(payload);
            // A post that didn't go through hands its reading time to the next one
            function unsent() {
                active += seconds * 1000;
                seconds = 0;
            }
            try {
                window.fetch(url, {
                    method: "POST",
                    credentials: "same-origin",
                    keepalive: !!keepalive,
                    headers: {"Content-Type": "application/json", "X-CSRFToken": csrfToken()},
                    body: body
                }).then(function (response) {
                    var type = response.headers.get("Content-Type") || "";
                    var ok = response.ok && type.indexOf("json") !== -1;
                    return (ok ? response.json().then(function (d) { return d; },
                                                      function () { return undefined; }) :
                                Promise.resolve(null)).then(function (data) {
                        inflight = false;
                        if (response.status === 401) {
                            authFailed = true;
                            unsent();
                            return;
                        }
                        var acked = ok ? normalise(data) : null;
                        if (!ok || !ackMatches(sent, acked)) {
                            unsent();
                            status("failed");
                            retryDelay = Math.min(retryDelay * 2, RETRY_MAX);
                            schedule(retryDelay);
                            return;
                        }
                        retryDelay = POST_DELAY;
                        if (pending === sent) {
                            pending = null;
                            sent.updated = Math.max(sent.updated, acked.updated);
                            sent.pending = false;
                            writeLocal(key, sent);
                            status("synced");
                        } else {
                            schedule(0);
                        }
                    });
                }).catch(function () {
                    inflight = false;
                    unsent();
                    status("failed");
                    retryDelay = Math.min(retryDelay * 2, RETRY_MAX);
                    schedule(retryDelay);
                });
            } catch (e) {
                inflight = false;
                unsent();
                schedule(retryDelay);
            }
        }

        function flush() {
            // Leaving the tab ends a stretch of reading: count it, and send it even when the
            // position hasn't moved since the last post
            if (lastUsed !== null) {
                active += Math.min(Date.now() - lastUsed, IDLE_CAP);
                lastUsed = null;
            }
            if (!pending && last && active >= 1000) {
                pending = last;
            }
            post(true);
        }

        document.addEventListener("visibilitychange", function () {
            if (document.visibilityState === "hidden") {
                // Audio keeps playing, so its clock runs on; the position is sent all the same
                if (background) {
                    post(true);
                } else {
                    flush();
                }
            } else {
                if (!background) {
                    lastUsed = Date.now();
                }
                if (pending) {
                    schedule(POST_DELAY);
                }
            }
        });
        window.addEventListener("pagehide", flush);
        window.addEventListener("online", function () {
            authFailed = false;
            retryDelay = POST_DELAY;
            post(false);
        });

        return {
            /** Resolves to the newest known position ({cfi, percent, updated}) or null. */
            load: function () {
                var local = readLocal(key) || pending;
                if (!enabled) {
                    return Promise.resolve(local);
                }
                return fetchServer(url).then(function (server) {
                    if (server && (!local || server.updated >= local.updated)) {
                        if (pending) {
                            pending = null;
                        }
                        writeLocal(key, {cfi: server.cfi, percent: server.percent,
                                         updated: server.updated,
                                         format: server.format, pending: false});
                        last = server;
                        return server;
                    }
                    if (local && local.pending) {
                        schedule(POST_DELAY);
                    }
                    last = local || null;
                    return local;
                });
            },
            /** Records a position now and posts it to the server after a short pause. */
            save: function (cfi, percent) {
                var pos = {cfi: cfi || "", percent: clampPercent(percent),
                           updated: Date.now(), pending: enabled};
                used();
                last = pos;
                writeLocal(key, pos);
                if (enabled) {
                    pending = pos;
                    status("pending");
                    if (!inflight) {
                        schedule(POST_DELAY);
                    }
                }
            },
            flush: flush,
            readLocal: function () { return readLocal(key) || pending; }
        };
    }

    /** "page:12" -> 12, "time:93.5" -> 93.5; null when the cfi has another prefix. */
    function parseTagged(cfi, prefix) {
        if (typeof cfi !== "string" || cfi.indexOf(prefix + ":") !== 0) {
            return null;
        }
        var value = parseFloat(cfi.slice(prefix.length + 1));
        return isNaN(value) ? null : value;
    }

    window.LilyProgress = {create: create, parseTagged: parseTagged};
})(window);
