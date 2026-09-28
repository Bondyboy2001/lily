/*
 * Settings frame (templates/settings_layout.html): the search at the head of
 * the section rail, the section heading on the Lily settings tabs, and landing
 * on one setting when a search result is picked.
 *
 * The search index is built from the settings pages themselves: every page the
 * rail links to is fetched once, and each row's label becomes an entry. What a
 * person can search is therefore exactly what the rail lets them open.
 */
(function () {
  "use strict";

  var field = document.getElementById("lp-search");
  var suggest = document.getElementById("lp-suggest");
  var heading = document.getElementById("lp-heading");
  var rail = document.querySelector(".lp-rail-list");
  if (!field || !suggest || !rail) { return; }

  var CACHE_KEY = "lily-settings-index-v1";
  var index = null;
  var loading = null;
  var active = -1;

  function text(el) { return el ? el.textContent.replace(/\s+/g, " ").trim() : ""; }
  function pathOf(href) { var a = document.createElement("a"); a.href = href; return a.pathname; }

  /* ---------------- Rail: pages and the Lily tabs ---------------- */
  var railLinks = Array.prototype.slice.call(rail.querySelectorAll("a.lp-rail-item"));
  var pages = [];
  railLinks.forEach(function (a) {
    var path = pathOf(a.href);
    if (!pages.some(function (p) { return p.path === path; })) { pages.push({ path: path, label: text(a) }); }
  });

  function tabLabel(name) {
    for (var i = 0; i < railLinks.length; i++) {
      if (railLinks[i].hash === "#" + name) { return text(railLinks[i]); }
    }
    return "";
  }

  function pageLabel(path) {
    for (var i = 0; i < pages.length; i++) { if (pages[i].path === path) { return pages[i].label; } }
    return "";
  }

  /* ---------------- Index ---------------- */
  function rowsOf(doc, path) {
    var out = [];
    var page = pageLabel(path);
    doc.querySelectorAll(".lp-pane .lp-row, .lp-pane .lp-check").forEach(function (row) {
      var labelEl = row.matches(".lp-check") ? row : row.querySelector(".lp-text > label, .lp-text > .lp-title");
      var name = text(labelEl);
      if (!name) { return; }
      var target = null;
      var forId = labelEl && labelEl.getAttribute("for");
      if (forId) { target = forId; }
      else if (row.id) { target = row.id; }
      else {
        var control = row.querySelector("input[id]:not([type=hidden]), select[id], textarea[id], button[id], a[id]");
        if (control) { target = control.id; }
      }
      var pane = row.closest("[data-lily-pane]");
      var section = pane ? tabLabel(pane.getAttribute("data-lily-pane")) : page;
      var group = row.closest(".lp-group");
      var groupLabel = group ? text(group.querySelector(".lp-label")) : "";
      if (!target && group) { target = group.id; }
      var where = section;
      if (groupLabel && groupLabel.toLowerCase() !== section.toLowerCase()) { where += " › " + groupLabel; }
      var help = text(row.querySelector(".lp-help"));
      out.push({ name: name, where: where, help: help, href: path + (target ? "#" + target : "") });
    });
    return out;
  }

  function sectionEntries() {
    return railLinks.map(function (a) {
      return { name: text(a), where: "", help: "", href: pathOf(a.href) + a.hash, section: true };
    });
  }

  function loadIndex() {
    if (index) { return Promise.resolve(index); }
    if (loading) { return loading; }
    try {
      var cached = JSON.parse(sessionStorage.getItem(CACHE_KEY) || "null");
      if (cached && cached.pages === pages.map(function (p) { return p.path; }).join(",")) {
        index = cached.entries;
        return Promise.resolve(index);
      }
    } catch (e) { /* storage unavailable: build it fresh */ }

    var here = window.location.pathname;
    loading = Promise.all(pages.map(function (p) {
      if (p.path === here) { return Promise.resolve(rowsOf(document, p.path)); }
      return fetch(p.path, { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.text() : ""; })
        .then(function (html) { return html ? rowsOf(new DOMParser().parseFromString(html, "text/html"), p.path) : []; })
        .catch(function () { return []; });
    })).then(function (lists) {
      var seen = {};
      index = sectionEntries().concat([].concat.apply([], lists)).filter(function (e) {
        var key = e.name + "|" + e.href;
        if (seen[key]) { return false; }
        seen[key] = true;
        return true;
      });
      try {
        sessionStorage.setItem(CACHE_KEY, JSON.stringify({ pages: pages.map(function (p) { return p.path; }).join(","), entries: index }));
      } catch (e) { /* ignore */ }
      return index;
    });
    return loading;
  }

  /* ---------------- Suggestions ---------------- */
  function score(entry, words) {
    var name = entry.name.toLowerCase();
    var hay = (name + " " + entry.where + " " + entry.help).toLowerCase();
    var total = 0;
    for (var i = 0; i < words.length; i++) {
      var w = words[i];
      if (hay.indexOf(w) === -1) { return -1; }
      if (name.indexOf(w) === 0) { total += 6; }
      else if (name.indexOf(" " + w) !== -1) { total += 4; }
      else if (name.indexOf(w) !== -1) { total += 3; }
      else { total += 1; }
    }
    if (entry.section) { total += 2; }
    return total;
  }

  function close() {
    suggest.hidden = true;
    field.setAttribute("aria-expanded", "false");
    active = -1;
  }

  function highlight(i) {
    var items = suggest.querySelectorAll(".lp-suggest-row");
    if (!items.length) { active = -1; return; }
    active = (i + items.length) % items.length;
    items.forEach(function (el, n) { el.setAttribute("aria-selected", n === active ? "true" : "false"); });
    items[active].scrollIntoView({ block: "nearest" });
  }

  function render() {
    var q = field.value.trim().toLowerCase();
    if (!q) { close(); return; }
    loadIndex().then(function (entries) {
      if (field.value.trim().toLowerCase() !== q) { return; }
      var words = q.split(/\s+/);
      var hits = entries.map(function (e) { return { e: e, s: score(e, words) }; })
        .filter(function (h) { return h.s >= 0; })
        .sort(function (a, b) { return b.s - a.s || a.e.name.length - b.e.name.length; })
        .slice(0, 12);
      suggest.textContent = "";
      if (!hits.length) {
        var empty = document.createElement("div");
        empty.className = "lp-suggest-empty";
        empty.textContent = suggest.getAttribute("data-empty");
        suggest.appendChild(empty);
      }
      hits.forEach(function (h) {
        var a = document.createElement("a");
        a.className = "lp-suggest-row";
        a.href = h.e.href;
        a.setAttribute("role", "option");
        var name = document.createElement("span");
        name.className = "lp-suggest-name";
        name.textContent = h.e.name;
        a.appendChild(name);
        if (h.e.where) {
          var where = document.createElement("span");
          where.className = "lp-suggest-where";
          where.textContent = h.e.where;
          a.appendChild(where);
        }
        a.addEventListener("mousedown", function (ev) { ev.preventDefault(); });
        a.addEventListener("click", function (ev) { go(ev, h.e.href); });
        suggest.appendChild(a);
      });
      suggest.hidden = false;
      field.setAttribute("aria-expanded", "true");
      highlight(0);
    });
  }

  function go(ev, href) {
    var url = new URL(href, window.location.href);
    close();
    field.blur();
    if (url.pathname === window.location.pathname) {
      if (ev) { ev.preventDefault(); }
      if (window.history && window.history.replaceState) { window.history.replaceState(null, "", url.hash || window.location.pathname); }
      land(url.hash.replace(/^#/, ""));
    } else if (!ev) {
      window.location.href = url.href;
    }
  }

  field.addEventListener("focus", function () { loadIndex(); if (field.value.trim()) { render(); } });
  field.addEventListener("input", render);
  field.addEventListener("blur", function () { setTimeout(close, 120); });
  field.addEventListener("keydown", function (e) {
    if (e.key === "ArrowDown") { e.preventDefault(); if (suggest.hidden) { render(); } else { highlight(active + 1); } }
    else if (e.key === "ArrowUp") { e.preventDefault(); highlight(active - 1); }
    else if (e.key === "Enter") {
      var items = suggest.querySelectorAll(".lp-suggest-row");
      if (!suggest.hidden && items[active]) { e.preventDefault(); go(null, items[active].getAttribute("href")); }
    } else if (e.key === "Escape") { if (!suggest.hidden) { e.preventDefault(); close(); } else { field.value = ""; field.blur(); } }
  });

  /* "/" focuses the search, as in Tulip, unless you are typing somewhere. */
  document.addEventListener("keydown", function (e) {
    if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) { return; }
    var t = e.target;
    if (t && (t.isContentEditable || /^(input|textarea|select)$/i.test(t.tagName))) { return; }
    e.preventDefault();
    field.focus();
  });

  /* ---------------- Landing on one setting ---------------- */
  function land(id) {
    if (!id) { return; }
    var el = document.getElementById(id);
    if (!el || !el.closest(".lp-pane") || el.matches("[data-lily-pane]")) { return; }
    var pane = el.closest("[data-lily-pane]");
    if (pane && !pane.classList.contains("active")) {
      var tab = rail.querySelector('[data-lily-tab="' + pane.getAttribute("data-lily-pane") + '"]');
      if (tab) { tab.click(); }
    }
    var row = el.closest(".lp-row, .lp-check, .lp-group") || el;
    setTimeout(function () {
      row.scrollIntoView({ block: "center" });
      row.classList.remove("lp-flash");
      void row.offsetWidth;
      row.classList.add("lp-flash");
      if (el.focus && el.matches("input, select, textarea, button, a")) { el.focus({ preventScroll: true }); }
    }, 30);
  }

  /* ---------------- Heading follows the Lily settings tab ---------------- */
  /* A group titled the same as the page says nothing twice. */
  function hideEchoes() {
    if (!heading) { return; }
    var h = text(heading).toLowerCase();
    document.querySelectorAll(".lp-pane .lp-group-head").forEach(function (head) {
      var label = head.querySelector(".lp-label");
      var echo = label && text(label).toLowerCase() === h;
      if (label) { label.style.visibility = echo ? "hidden" : ""; }
      head.classList.toggle("is-echo", !!echo && !head.querySelector(".btn"));
    });
  }

  function syncHeading() {
    if (!heading) { return; }
    var on = rail.querySelector("li.active > [data-lily-tab]");
    if (on) { heading.textContent = text(on); }
    hideEchoes();
  }

  if (rail.querySelector("[data-lily-tab]")) {
    rail.addEventListener("click", function (e) {
      if (e.target.closest("[data-lily-tab]")) { setTimeout(syncHeading, 0); }
    });
    window.addEventListener("hashchange", function () { setTimeout(syncHeading, 0); });
  }

  function ready() {
    syncHeading();
    land(window.location.hash.replace(/^#/, ""));
  }
  if (document.readyState === "complete") { setTimeout(ready, 0); }
  else { window.addEventListener("load", ready); }
})();
