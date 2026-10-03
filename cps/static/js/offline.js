/*
 * Offline reading, the page side (the worker is templates/sw.js, served at /sw.js).
 * - Registers the worker, where the browser allows one (HTTPS or localhost).
 * - Library: hands the worker the books in progress (#lily-offline-auto) to keep.
 * - Book page: the Save offline button (#offline-btn) keeps the book on this device, or
 *   removes it; it is on (.is-on) while the book is saved here.
 * Where there is no worker, nothing happens and the button stays hidden.
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

  // ------------------------------------------------------------ book page
  function offlineButton() {
    var btn = document.getElementById("offline-btn");
    if (!btn) { return; }
    var book;
    try { book = JSON.parse(btn.getAttribute("data-book")); } catch (e) { return; }
    var glyph = btn.querySelector(".glyphicon");
    var label = btn.querySelector(".book-action-label");

    function show(saved, busy) {
      var text = busy ? btn.getAttribute("data-saving")
        : btn.getAttribute(saved ? "data-remove" : "data-save");
      btn.classList.toggle("is-on", !!saved);
      btn.classList.toggle("is-busy", !!busy);
      btn.setAttribute("aria-busy", busy ? "true" : "false");
      btn.title = text;
      label.textContent = text;
      glyph.className = "glyphicon " + (busy ? "glyphicon-refresh glyphicon-spin"
        : saved ? "glyphicon-cloud-check" : "glyphicon-cloud-download");
      btn.setAttribute("data-saved", saved ? "true" : "false");
    }

    ask({ type: "status", ids: [book.id] }).then(function (status) {
      show(status && status[book.id], false);
      btn.hidden = false;
    }).catch(function () { /* no worker yet: the button stays hidden */ });

    btn.addEventListener("click", function () {
      if (btn.classList.contains("is-busy")) { return; }
      var saved = btn.getAttribute("data-saved") === "true";
      if (!saved && !navigator.onLine) {
        if (window.lilyFlash) { window.lilyFlash(btn.getAttribute("data-failed"), "error"); }
        return;
      }
      show(saved, !saved);
      ask(saved ? { type: "drop", id: book.id } : { type: "keep", book: book }).then(function (now) {
        show(!!now, false);
      }).catch(function () {
        show(saved, false);
        if (window.lilyFlash) { window.lilyFlash(btn.getAttribute("data-failed"), "error"); }
      });
    });
  }

  function start() {
    syncContinueReading();
    offlineButton();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
