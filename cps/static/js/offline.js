/*
 * Offline reading, the page side (the worker is templates/sw.js, served at /sw.js).
 * - Registers the worker, where the browser allows one (HTTPS or localhost).
 * - Library: hands the worker the books in progress (#lily-offline-auto) to keep.
 * Where there is no worker, nothing happens.
 */
(function () {
  "use strict";

  var meta = document.querySelector('meta[name="lily-sw"]');
  if (!meta || !("serviceWorker" in navigator) || !window.isSecureContext) { return; }

  navigator.serviceWorker.register(meta.content, { scope: meta.getAttribute("data-scope") })
    .catch(function () { /* offline reading just stays off */ });

  function ask(message) {
    return navigator.serviceWorker.ready.then(function (reg) {
      return new Promise(function (resolve, reject) {
        var channel = new MessageChannel();
        channel.port1.onmessage = function (event) {
          if (event.data && event.data.ok) { resolve(event.data.result); } else { reject(new Error(event.data && event.data.error)); }
        };
        (navigator.serviceWorker.controller || reg.active).postMessage(message, [channel.port2]);
      });
    });
  }

  // ------------------------------------------------------------ library
  function syncContinueReading() {
    var data = document.getElementById("lily-offline-auto");
    if (!data || !navigator.onLine) { return; }
    var books;
    try { books = JSON.parse(data.textContent); } catch (e) { return; }
    ask({ type: "sync", books: books }).catch(function () {});
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", syncContinueReading);
  } else {
    syncContinueReading();
  }
})();
