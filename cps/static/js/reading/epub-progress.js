/* global reader, calibre */

/* Reading position and percentage for the epub reader.
 *
 * Start: epub.js already rendered this device's saved position (a CFI needs no locations).
 * Here the server copy is fetched (progress-sync.js, at most 2.5 s); when it is newer, from
 * another device, the reader jumps there.
 *
 * Percent: epub.js "locations" map a CFI to a fraction of the book. Generating them parses
 * every chapter, so it runs in the background after the first page is on screen and the
 * result is cached in localStorage per book (keyed by the book's file stamp), which makes
 * every later open instant. Until then the last known percentage is shown and saved.
 *
 * Every page turn saves {cfi, percent} through progress-sync.js and fires a
 * "lily:reader-progress" event ({fraction, atEnd}) for the end-of-book card. */
(function () {
    "use strict";

    var sync = window.lilyProgressSync;
    if (!window.reader || !reader.book || !reader.rendition || !sync) {
        return;
    }
    var book = reader.book;
    var rendition = reader.rendition;
    var progressDiv = document.getElementById("progress");

    var CHARS_PER_LOCATION = 1024;
    var CACHE_PREFIX = "lily.locations.";
    var CACHE_INDEX = "lily.locations.index";
    var MAX_CACHED_BOOKS = 20;
    var cacheKey = CACHE_PREFIX + (calibre.progressKey || calibre.bookUrl);
    var cacheStamp = String(calibre.bookStamp || "") + ":" + CHARS_PER_LOCATION;

    // Nothing is saved until the starting position is settled, otherwise the first render
    // could overwrite a newer position from another device.
    var restored = false;
    var locationsReady = false;
    var lastFraction = null;
    var pendingPercent = null;  // a position known only as a percentage, waiting for locations

    // ------------------------------------------------------------------ locations cache
    function readIndex() {
        try {
            var index = JSON.parse(window.localStorage.getItem(CACHE_INDEX));
            return Array.isArray(index) ? index : [];
        } catch (e) {
            return [];
        }
    }

    function writeIndex(index) {
        try {
            window.localStorage.setItem(CACHE_INDEX, JSON.stringify(index));
        } catch (e) {}
    }

    function readCachedLocations() {
        try {
            var entry = JSON.parse(window.localStorage.getItem(cacheKey));
            if (entry && entry.stamp === cacheStamp && typeof entry.locations === "string") {
                return entry.locations;
            }
        } catch (e) {}
        return null;
    }

    function cacheLocations(json) {
        var entry = JSON.stringify({stamp: cacheStamp, locations: json});
        var index = readIndex().filter(function (key) { return key !== cacheKey; });
        while (index.length >= MAX_CACHED_BOOKS) {
            try { window.localStorage.removeItem(index.shift()); } catch (e) {}
        }
        // Storage full: drop the oldest cached book and try again.
        for (;;) {
            try {
                window.localStorage.setItem(cacheKey, entry);
                index.push(cacheKey);
                writeIndex(index);
                return;
            } catch (e) {
                if (!index.length) {
                    writeIndex(index);
                    return;
                }
                try { window.localStorage.removeItem(index.shift()); } catch (ignored) {}
            }
        }
    }

    // ------------------------------------------------------------------ position
    function currentLocation() {
        var location = rendition.location;
        return location && location.start && location.start.cfi ? location : null;
    }

    /** Fraction (0..1) read at the current page, or the last known one before locations exist. */
    function currentFraction(location) {
        if (location.atEnd) {
            return 1;
        }
        if (locationsReady && location.end && location.end.cfi) {
            var fraction = book.locations.percentageFromCfi(location.end.cfi);
            if (typeof fraction === "number" && !isNaN(fraction)) {
                return fraction;
            }
        }
        return lastFraction;
    }

    function update() {
        var location = currentLocation();
        if (!location) {
            return;
        }
        var fraction = currentFraction(location);
        lastFraction = fraction;
        if (progressDiv) {
            progressDiv.textContent = fraction === null ? "" : Math.round(fraction * 100) + "%";
        }
        if (restored) {
            sync.save(location.start.cfi, fraction);
        }
        window.dispatchEvent(new CustomEvent("lily:reader-progress", {
            detail: {fraction: fraction, atEnd: !!location.atEnd}
        }));
    }

    function displayPercent(percent) {
        var cfi = book.locations.cfiFromPercentage(percent);
        if (cfi) {
            rendition.display(cfi).catch(function () {});
        }
    }

    function restore(saved) {
        if (saved && typeof saved.percent === "number") {
            lastFraction = saved.percent;
        }
        if (saved && saved.cfi && saved.cfi.indexOf("epubcfi(") === 0) {
            if (saved.cfi !== reader.lilyStartCfi) {
                return reader.lilyShow(saved.cfi).catch(function () {});
            }
            return null;
        }
        // No CFI (an old percentage only): it needs locations, so it waits for them.
        var percent = saved && saved.percent > 0 ? saved.percent : null;
        if (percent !== null) {
            if (locationsReady) {
                displayPercent(percent);
            } else {
                pendingPercent = percent;
            }
        }
        return null;
    }

    // ------------------------------------------------------------------ start
    rendition.on("relocated", update);

    sync.load().then(restore).catch(function () {}).then(function () {
        restored = true;
        update();
    });

    function locationsLoaded() {
        locationsReady = true;
        if (pendingPercent !== null) {
            displayPercent(pendingPercent);
            pendingPercent = null;
        }
        update();
    }

    function generateLocations() {
        book.locations.generate(CHARS_PER_LOCATION).then(function () {
            cacheLocations(book.locations.save());
            locationsLoaded();
        }).catch(function () {});
    }

    book.ready.then(function () {
        var cached = readCachedLocations();
        if (cached) {
            try {
                book.locations.load(cached);
                locationsLoaded();
                return;
            } catch (e) {}
        }
        // Let the first page render before parsing the whole book.
        var started = false;
        var start = function () {
            if (!started) {
                started = true;
                setTimeout(generateLocations, 500);
            }
        };
        rendition.once("rendered", start);
        setTimeout(start, 3000);
    }).catch(function () {});
})();
