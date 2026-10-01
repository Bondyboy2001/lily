/* Registers the reader's offline service worker (reader-sw.js) from a reader page.
 * <script src=".../reader-offline.js" data-sw="<root>/reader-sw.js" data-scope="<root>/read/">
 * Service workers need a secure context (https or localhost); over plain http this does nothing. */
(function () {
    "use strict";

    var script = document.currentScript;
    if (!script || !("serviceWorker" in navigator) || !window.isSecureContext) {
        return;
    }
    var url = script.getAttribute("data-sw");
    var scope = script.getAttribute("data-scope");
    if (!url || !scope) {
        return;
    }
    // This page's own files: the page first, then its scripts, styles and data-cache extras
    // (the book file, the page theme stylesheet).
    function pageFiles() {
        var urls = [window.location.href.split("#")[0]];
        document.querySelectorAll("script[src], link[rel='stylesheet'][href]").forEach(function (element) {
            urls.push(element.src || element.href);
        });
        (script.getAttribute("data-cache") || "").split(" ").forEach(function (extra) {
            if (extra) {
                urls.push(new URL(extra, window.location.href).href);
            }
        });
        return urls;
    }

    window.addEventListener("load", function () {
        var controlled = !!navigator.serviceWorker.controller;
        navigator.serviceWorker.register(url, {scope: scope}).then(function () {
            return controlled ? null : navigator.serviceWorker.ready;
        }).then(function (registration) {
            // Opened before the worker was running: hand it the files it did not see.
            if (registration && registration.active) {
                registration.active.postMessage({type: "lily-reader-warm", urls: pageFiles()});
            }
        }).catch(function () {
            // Blocked or unsupported: the reader works online as before.
        });
    });
})();
