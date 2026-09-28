/* Lily reading-progress sync.
 *
 * Keeps a reader's position in localStorage (instant, works offline) and mirrors it to
 * the server so it follows the user across devices:
 *   GET  <url>  -> {"cfi": str, "percent": 0..1, "updated": iso-string | epoch}
 *   POST <url>  <- {"cfi": str, "percent": 0..1}   (X-CSRFToken header)
 * On open, whichever copy is newer wins. Every server error (offline, 401, 404, HTML
 * login page...) is swallowed and the local copy is used instead.
 *
 * Usage:
 *   var sync = LilyProgress.create({url: "/ajax/progress/12", storageKey: "12.epub", enabled: true});
 *   sync.load().then(function (pos) { if (pos) { ...jump to pos.cfi / pos.percent... } });
 *   sync.save(cfi, percent);   // debounced POST, flushed on pagehide / tab hidden
 */
(function (window) {
    "use strict";

    var LOCAL_PREFIX = "lily.progress.";
    var POST_DELAY = 4000;
    var LOAD_TIMEOUT = 2500;

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
        return {cfi: cfi, percent: percent, updated: toMillis(pos.updated)};
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
        var enabled = !!(options.enabled && url);
        var pending = null;
        var timer = null;
        var lastSent = "";

        function post(keepalive) {
            if (timer) {
                clearTimeout(timer);
                timer = null;
            }
            if (!enabled || !pending || !window.fetch) {
                return;
            }
            var body = JSON.stringify({cfi: pending.cfi, percent: pending.percent === null ? 0 : pending.percent});
            pending = null;
            if (body === lastSent) {
                return;
            }
            lastSent = body;
            try {
                window.fetch(url, {
                    method: "POST",
                    credentials: "same-origin",
                    keepalive: !!keepalive,
                    headers: {"Content-Type": "application/json", "X-CSRFToken": csrfToken()},
                    body: body
                }).then(function (response) {
                    if (!response.ok) {
                        lastSent = "";
                    }
                }).catch(function () {
                    lastSent = "";
                });
            } catch (e) {
                lastSent = "";
            }
        }

        function flush() {
            post(true);
        }

        document.addEventListener("visibilitychange", function () {
            if (document.visibilityState === "hidden") {
                flush();
            }
        });
        window.addEventListener("pagehide", flush);

        return {
            /** Resolves to the newest known position ({cfi, percent, updated}) or null. */
            load: function () {
                var local = readLocal(key);
                if (!enabled) {
                    return Promise.resolve(local);
                }
                return fetchServer(url).then(function (server) {
                    if (server && (!local || server.updated >= local.updated)) {
                        return server;
                    }
                    return local;
                });
            },
            /** Records a position now and posts it to the server after a short pause. */
            save: function (cfi, percent) {
                var pos = {cfi: cfi || "", percent: clampPercent(percent), updated: Date.now()};
                writeLocal(key, pos);
                pending = pos;
                if (enabled && !timer) {
                    timer = setTimeout(function () { post(false); }, POST_DELAY);
                }
            },
            flush: flush,
            readLocal: function () { return readLocal(key); }
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
