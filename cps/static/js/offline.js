/*
 * Offline reading, the page side (the worker is templates/sw.js, served at /sw.js).
 * - Registers the worker, where the browser allows one (HTTPS or localhost).
 * - Book page: the "Keep offline" button (#keep-offline-btn) keeps or lets go of the book.
 * - Library: hands the worker the Continue Reading books (#lily-offline-auto) to keep.
 * - Offline page: lists the books kept on this device.
 * Where there is no worker, the button stays hidden and nothing else happens.
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
  window.LilyOffline = { ask: ask };

  function formatSize(bytes) {
    if (!bytes) { return ""; }
    var units = ["B", "KB", "MB", "GB"], i = 0;
    while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i += 1; }
    return (i ? bytes.toFixed(1) : String(bytes)) + " " + units[i];
  }

  function flash(message) {
    if (window.lilyFlash) { window.lilyFlash(message, "danger"); }
  }

  // ------------------------------------------------------------ book page
  function bookButton() {
    var btn = document.getElementById("keep-offline-btn");
    if (!btn) { return; }
    var book = JSON.parse(btn.getAttribute("data-book"));
    var label = btn.querySelector(".book-action-label");
    function show(kept) {
      btn.setAttribute("aria-pressed", kept ? "true" : "false");
      var text = btn.getAttribute(kept ? "data-label-kept" : "data-label-keep");
      btn.title = text;
      if (label) { label.textContent = text; }
    }
    ask({ type: "status", id: book.id }).then(function (entry) {
      show(!!entry);
      btn.hidden = false;
    }).catch(function () {});
    btn.addEventListener("click", function () {
      if (btn.classList.contains("is-busy")) { return; }
      var keep = btn.getAttribute("aria-pressed") !== "true";
      btn.classList.add("is-busy");
      btn.setAttribute("aria-busy", "true");
      if (keep && navigator.storage && navigator.storage.persist) {
        navigator.storage.persist().catch(function () {});
      }
      ask(keep ? { type: "keep", book: book } : { type: "drop", id: book.id }).then(function () {
        show(keep);
      }).catch(function () {
        flash(btn.getAttribute(keep ? "data-keep-failed" : "data-drop-failed"));
      }).then(function () {
        btn.classList.remove("is-busy");
        btn.removeAttribute("aria-busy");
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

  // ------------------------------------------------------------ offline page
  function offlinePage() {
    var main = document.querySelector(".lily-offline");
    if (!main) { return; }
    var listEl = main.querySelector(".offline-books");
    var empty = main.querySelector(".offline-empty");
    var statusEl = main.querySelector(".offline-status");
    function setStatus() {
      statusEl.textContent = statusEl.getAttribute(navigator.onLine ? "data-online" : "data-offline");
    }
    setStatus();
    window.addEventListener("online", setStatus);
    window.addEventListener("offline", setStatus);
    function render(books) {
      listEl.textContent = "";
      empty.hidden = books.length > 0;
      books.forEach(function (book) {
        var li = document.createElement("li");
        li.className = "offline-book";
        var cover = document.createElement("img");
        cover.className = "offline-book-cover";
        cover.alt = "";
        if (book.cover) { cover.src = book.cover; }
        var text = document.createElement("div");
        text.className = "offline-book-text";
        var title = document.createElement("a");
        title.className = "offline-book-title";
        title.href = book.reader;
        title.textContent = book.title;
        text.appendChild(title);
        var info = document.createElement("p");
        info.className = "offline-book-meta";
        var size = formatSize(book.bytes);
        info.textContent = [book.author, book.format ? book.format.toUpperCase() : "",
                            size ? main.getAttribute("data-size-label").replace("{size}", size) : ""]
          .filter(Boolean).join(" · ");
        text.appendChild(info);
        var read = document.createElement("a");
        read.className = "btn btn-default";
        read.href = book.reader;
        read.textContent = main.getAttribute("data-read-label");
        var remove = document.createElement("button");
        remove.type = "button";
        remove.className = "icon-btn is-danger";
        remove.setAttribute("aria-label", main.getAttribute("data-remove-label").replace("{name}", book.title));
        remove.title = remove.getAttribute("aria-label");
        remove.innerHTML = '<span class="glyphicon glyphicon-trash" aria-hidden="true"></span>';
        remove.addEventListener("click", function () {
          remove.classList.add("is-busy");
          ask({ type: "drop", id: book.id }).then(load, function () { remove.classList.remove("is-busy"); });
        });
        li.appendChild(cover);
        li.appendChild(text);
        li.appendChild(read);
        li.appendChild(remove);
        listEl.appendChild(li);
      });
    }
    function load() { return ask({ type: "list" }).then(render, function () { render([]); }); }
    load();
  }

  function start() {
    bookButton();
    syncContinueReading();
    offlinePage();
  }
  if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", start); } else { start(); }
})();
