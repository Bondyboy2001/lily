/*
 * Lily settings page (cps/templates/cwa_settings.html)
 *
 * - Tabbed layout with the active tab remembered in the URL hash
 * - Sticky save bar with an "unsaved changes" hint
 * - Schedule selector toggles (archived cleanup / Hardcover auto-fetch)
 * - Metadata provider hierarchy + enable toggles
 * - Duplicate format priority ranking
 *
 * Plain DOM APIs only, so it does not depend on the jQuery version.
 */
(function () {
  "use strict";

  function parseJsonAttr(el, attr, fallback) {
    var raw = el.getAttribute(attr);
    if (raw === null || raw === "") { return fallback; }
    try {
      return JSON.parse(raw);
    } catch (e) {
      console.error("Error parsing " + attr + ":", e);
      return fallback;
    }
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  document.addEventListener("DOMContentLoaded", function () {
    var form = document.getElementById("cwa_settings_form");
    if (!form) { return; }

    var i18n = {
      priority: form.getAttribute("data-i18n-priority") || "Priority",
      noFormatData: form.getAttribute("data-i18n-no-format-data") || "No format data available. Please save settings to initialize.",
      formatError: form.getAttribute("data-i18n-format-error") || "Error loading format priority:",
      unsaved: form.getAttribute("data-i18n-unsaved") || "You have unsaved changes"
    };

    /* ------------------------------------------------------------------ */
    /* Unsaved-changes tracking                                            */
    /* ------------------------------------------------------------------ */
    var statusEl = document.getElementById("lily_savebar_status");
    var dirty = false;
    var submitting = false;

    function markDirty() {
      if (dirty) { return; }
      dirty = true;
      if (statusEl) { statusEl.textContent = i18n.unsaved; }
    }

    form.addEventListener("input", markDirty);
    form.addEventListener("change", markDirty);

    window.addEventListener("beforeunload", function (e) {
      if (dirty && !submitting) {
        e.preventDefault();
        e.returnValue = "";
      }
    });

    /* ------------------------------------------------------------------ */
    /* Tabs                                                                */
    /* ------------------------------------------------------------------ */
    var tabLinks = Array.prototype.slice.call(document.querySelectorAll("[data-lily-tab]"));
    var panes = Array.prototype.slice.call(document.querySelectorAll("[data-lily-pane]"));
    var baseAction = form.getAttribute("action") || "";
    var currentTab = null;

    function tabExists(name) {
      return tabLinks.some(function (link) { return link.getAttribute("data-lily-tab") === name; });
    }

    function activateTab(name, updateHash) {
      if (!tabExists(name)) { return; }
      currentTab = name;
      tabLinks.forEach(function (link) {
        var on = link.getAttribute("data-lily-tab") === name;
        link.parentNode.classList.toggle("active", on);
        link.setAttribute("aria-selected", on ? "true" : "false");
        link.setAttribute("tabindex", on ? "0" : "-1");
        if (on) {
          // Keep the active tab visible in the horizontally scrolling strip (mobile)
          var strip = link.parentNode.parentNode;
          var li = link.parentNode;
          if (li.offsetLeft < strip.scrollLeft) {
            strip.scrollLeft = li.offsetLeft;
          } else if (li.offsetLeft + li.offsetWidth > strip.scrollLeft + strip.clientWidth) {
            strip.scrollLeft = li.offsetLeft + li.offsetWidth - strip.clientWidth;
          }
        }
      });
      panes.forEach(function (pane) {
        var on = pane.getAttribute("data-lily-pane") === name;
        pane.classList.toggle("active", on);
        pane.classList.toggle("in", on);
      });
      if (updateHash && window.history && window.history.replaceState) {
        // replaceState avoids the scroll jump that assigning location.hash causes
        window.history.replaceState(null, "", "#" + name);
      }
    }

    function tabFromHash() {
      var hash = (window.location.hash || "").replace(/^#/, "");
      return tabExists(hash) ? hash : null;
    }

    tabLinks.forEach(function (link, index) {
      link.addEventListener("click", function (e) {
        e.preventDefault();
        activateTab(link.getAttribute("data-lily-tab"), true);
      });
      link.addEventListener("keydown", function (e) {
        var target = null;
        if (e.key === "ArrowRight") { target = tabLinks[(index + 1) % tabLinks.length]; }
        else if (e.key === "ArrowLeft") { target = tabLinks[(index - 1 + tabLinks.length) % tabLinks.length]; }
        else if (e.key === "Home") { target = tabLinks[0]; }
        else if (e.key === "End") { target = tabLinks[tabLinks.length - 1]; }
        if (target) {
          e.preventDefault();
          activateTab(target.getAttribute("data-lily-tab"), true);
          target.focus();
        }
      });
    });

    window.addEventListener("hashchange", function () {
      var name = tabFromHash();
      if (name && name !== currentTab) { activateTab(name, false); }
    });

    activateTab(tabFromHash() || (tabLinks[0] && tabLinks[0].getAttribute("data-lily-tab")), false);

    // If the browser blocks submission because a field in a hidden tab is
    // invalid, switch to that tab so the validation bubble can be shown.
    var invalidHandled = false;
    form.addEventListener("invalid", function (e) {
      if (invalidHandled) { return; }
      invalidHandled = true;
      setTimeout(function () { invalidHandled = false; }, 0);
      var pane = e.target.closest ? e.target.closest("[data-lily-pane]") : null;
      if (pane && !pane.classList.contains("active")) {
        activateTab(pane.getAttribute("data-lily-pane"), true);
      }
    }, true);

    form.addEventListener("submit", function () {
      submitting = true;
      // Return to the same tab after the POST re-renders the page
      if (currentTab) { form.setAttribute("action", baseAction + "#" + currentTab); }
    });

    /* ------------------------------------------------------------------ */
    /* Schedule selectors (daily / weekly / monthly)                       */
    /* ------------------------------------------------------------------ */
    // The weekly <select> and the monthly <input type=number> share the same
    // name; the one that does not apply is disabled so only the relevant value
    // is submitted (weekly stays enabled for non-weekly/monthly schedules to
    // keep the previously stored default).
    Array.prototype.slice.call(document.querySelectorAll("select[data-lily-schedule]")).forEach(function (select) {
      var day = document.getElementById(select.getAttribute("data-day-selector"));
      var monthday = document.getElementById(select.getAttribute("data-monthday-selector"));
      var hour = document.getElementById(select.getAttribute("data-hour-selector"));

      function setControlsDisabled(container, disabled) {
        if (!container) { return; }
        Array.prototype.slice.call(container.querySelectorAll("input, select")).forEach(function (el) {
          el.disabled = disabled;
        });
      }

      function update() {
        var type = select.value;
        var lockedByServer = select.disabled; // e.g. no Hardcover token
        // "" hands display back to the stylesheet (the rows are grids).
        if (day) { day.style.display = type === "weekly" ? "" : "none"; }
        if (monthday) { monthday.style.display = type === "monthly" ? "" : "none"; }
        if (hour) { hour.style.display = (type === "daily" || type === "weekly" || type === "monthly") ? "" : "none"; }
        if (!lockedByServer) {
          setControlsDisabled(day, type === "monthly");
          setControlsDisabled(monthday, type !== "monthly");
        }
      }

      select.addEventListener("change", update);
      update();
    });

    /* ------------------------------------------------------------------ */
    /* Automatic duplicate resolution needs Title among the criteria       */
    /* ------------------------------------------------------------------ */
    // The server refuses it too (duplicate_rules.auto_resolve_block_reason); this
    // just keeps the switch from looking on when it can't be.
    var autoResolve = document.querySelector("input[data-needs-title]");
    var titleCriterion = document.getElementById("duplicate_detection_title");
    if (autoResolve && titleCriterion) {
      var lockedUntilPreview = autoResolve.disabled;
      var syncAutoResolve = function () {
        if (!titleCriterion.checked) { autoResolve.checked = false; }
        autoResolve.disabled = lockedUntilPreview || !titleCriterion.checked;
      };
      titleCriterion.addEventListener("change", syncAutoResolve);
      syncAutoResolve();
    }

    /* ------------------------------------------------------------------ */
    /* Metadata provider hierarchy + global enable toggles                 */
    /* ------------------------------------------------------------------ */
    var providerList = document.getElementById("metadata_provider_list");
    var hierarchyInput = document.getElementById("metadata_provider_hierarchy_hidden");
    var enabledList = document.getElementById("metadata_provider_enabled_list");
    var enabledInput = document.getElementById("metadata_providers_enabled_hidden");

    var providerHierarchy = parseJsonAttr(form, "data-provider-hierarchy", ["google", "openlibrary", "hardcover", "googlescholar"]);
    try {
      if (typeof providerHierarchy === "string") { providerHierarchy = JSON.parse(providerHierarchy); }
    } catch (e) {
      console.error("Error parsing hierarchy:", e);
      providerHierarchy = ["google", "openlibrary", "hardcover", "googlescholar"];
    }
    if (!Array.isArray(providerHierarchy)) { providerHierarchy = ["google", "openlibrary", "hardcover", "googlescholar"]; }

    var globalEnabledMap = parseJsonAttr(form, "data-providers-enabled", {});
    try {
      if (typeof globalEnabledMap === "string") {
        var s = globalEnabledMap.charAt(0) === "'" && globalEnabledMap.charAt(globalEnabledMap.length - 1) === "'"
          ? globalEnabledMap.slice(1, -1) : globalEnabledMap;
        globalEnabledMap = JSON.parse(s || "{}");
      }
    } catch (e) {
      console.error("Error parsing enabled map:", e);
      globalEnabledMap = {};
    }
    if (!globalEnabledMap || typeof globalEnabledMap !== "object" || Array.isArray(globalEnabledMap)) {
      globalEnabledMap = {};
    }

    function updateHierarchyInput() {
      if (!providerList || !hierarchyInput) { return; }
      var items = providerList.querySelectorAll(".metadata-provider-item");
      hierarchyInput.value = JSON.stringify(Array.prototype.map.call(items, function (item) {
        return item.dataset.providerId;
      }));
    }

    function addProviderItem(provider) {
      var item = document.createElement("div");
      item.className = "metadata-provider-item";
      item.draggable = false; // native drag disabled, mouse handlers below
      item.dataset.providerId = provider.id;
      item.innerHTML =
        '<span class="metadata-provider-drag-handle" aria-hidden="true">⋮⋮</span>' +
        '<span class="metadata-provider-name">' + escapeHtml(provider.name) + "</span>";
      providerList.appendChild(item);
    }

    function populateProviderList(providers) {
      if (!providerList) { return; }
      providerList.innerHTML = "";
      var providerMap = {};
      providers.forEach(function (p) { providerMap[p.id] = p; });
      // Saved order first, then any providers not yet in the hierarchy
      providerHierarchy.forEach(function (id) {
        if (providerMap[id]) {
          addProviderItem(providerMap[id]);
          delete providerMap[id];
        }
      });
      Object.keys(providerMap).forEach(function (id) { addProviderItem(providerMap[id]); });
      updateHierarchyInput();
    }

    function updateEnabledInput() {
      if (!enabledList || !enabledInput) { return; }
      var map = {};
      Array.prototype.forEach.call(
        enabledList.querySelectorAll('input[type="checkbox"][data-provider-id]'),
        function (t) { map[t.dataset.providerId] = !!t.checked; }
      );
      enabledInput.value = JSON.stringify(map);
    }

    function populateEnabledToggles(providers) {
      if (!enabledList) { return; }
      enabledList.innerHTML = "";
      providers.forEach(function (p) {
        var wrapper = document.createElement("div");
        wrapper.className = "provider_enable_item";
        var id = "global-provider-" + p.id;
        var checked = Object.prototype.hasOwnProperty.call(globalEnabledMap, p.id) ? !!globalEnabledMap[p.id] : p.globally_enabled !== false;
        wrapper.innerHTML =
          '<input type="checkbox" id="' + escapeHtml(id) + '" data-provider-id="' + escapeHtml(p.id) + '"' + (checked ? " checked" : "") + ">" +
          '<label for="' + escapeHtml(id) + '">' + escapeHtml(p.name) + "</label>";
        enabledList.appendChild(wrapper);
      });
      updateEnabledInput();
    }

    if (enabledList) {
      enabledList.addEventListener("change", updateEnabledInput);
      // Ensure the hidden input is current on submit
      form.addEventListener("submit", updateEnabledInput);
    }

    function getAfterElement(container, selector, y, exclude) {
      var candidates = Array.prototype.filter.call(container.querySelectorAll(selector), function (el) {
        return el !== exclude;
      });
      return candidates.reduce(function (closest, child) {
        var box = child.getBoundingClientRect();
        var offset = y - box.top - box.height / 2;
        if (offset < 0 && offset > closest.offset) {
          return { offset: offset, element: child };
        }
        return closest;
      }, { offset: Number.NEGATIVE_INFINITY }).element;
    }

    // Mouse-driven reordering shared by the provider list and the format list
    function makeSortable(container, itemSelector, onDragStart, onDrop) {
      var dragged = null;
      var moved = false;

      container.addEventListener("mousedown", function (e) {
        var item = e.target.closest(itemSelector);
        if (!item || !container.contains(item)) { return; }
        dragged = item;
        moved = false;
        onDragStart(item, true);
        document.body.style.userSelect = "none";
        e.preventDefault();
      });

      document.addEventListener("mousemove", function (e) {
        if (!dragged) { return; }
        var after = getAfterElement(container, itemSelector, e.clientY, dragged);
        if (after == null) {
          if (container.lastElementChild !== dragged) { container.appendChild(dragged); moved = true; }
        } else if (after.previousElementSibling !== dragged) {
          container.insertBefore(dragged, after);
          moved = true;
        }
      });

      document.addEventListener("mouseup", function () {
        if (!dragged) { return; }
        onDragStart(dragged, false);
        dragged = null;
        document.body.style.userSelect = "";
        onDrop();
        if (moved) { markDirty(); }
      });
    }

    function initProviders(providers) {
      populateProviderList(providers);
      populateEnabledToggles(providers);
      if (providerList) {
        makeSortable(providerList, ".metadata-provider-item", function (item, on) {
          item.classList.toggle("dragging", on);
        }, updateHierarchyInput);
      }
    }

    var providerUrl = form.getAttribute("data-provider-url");
    if (providerList && providerUrl) {
      fetch(providerUrl, { credentials: "same-origin" })
        .then(function (response) { return response.json(); })
        .then(initProviders)
        .catch(function (error) {
          console.error("Error fetching providers:", error);
          initProviders([
            { id: "google", name: "Google", active: true },
            { id: "openlibrary", name: "Open Library", active: true },
            { id: "hardcover", name: "Hardcover", active: true },
            { id: "googlescholar", name: "Scholar", active: true }
          ]);
        });
    }
    updateHierarchyInput();

    /* ------------------------------------------------------------------ */
    /* Duplicate format priority ranking                                   */
    /* ------------------------------------------------------------------ */
    var priorityInput = document.getElementById("duplicate_format_priority");
    var priorityList = document.getElementById("format_priority_list");

    function renderPriorityError(message) {
      priorityList.innerHTML = "";
      var li = document.createElement("li");
      li.className = "lily-format-priority-error";
      li.textContent = message;
      priorityList.appendChild(li);
    }

    function updatePriorityInput() {
      var items = priorityList.querySelectorAll(".format-priority-item");
      var total = items.length;
      var priorities = {};
      Array.prototype.forEach.call(items, function (item, index) {
        // Top of the list gets the highest priority
        priorities[item.dataset.format] = 100 - (index * Math.floor(100 / total));
      });
      priorityInput.value = JSON.stringify(priorities);
      Array.prototype.forEach.call(items, function (item) {
        var badge = item.querySelector(".lily-format-priority-badge");
        if (badge) { badge.textContent = i18n.priority + ": " + priorities[item.dataset.format]; }
      });
    }

    if (priorityInput && priorityList) {
      try {
        var rawValue = priorityInput.value || "{}";
        var decodedValue = rawValue.replace(/&quot;/g, '"').replace(/&#34;/g, '"');
        var formatPriority = JSON.parse(decodedValue);
        var formatArray = Object.keys(formatPriority).map(function (format) {
          return { format: format, priority: formatPriority[format] };
        });
        formatArray.sort(function (a, b) { return b.priority - a.priority; });

        if (formatArray.length === 0) {
          console.error("No format priority data found");
          renderPriorityError(i18n.noFormatData);
        } else {
          priorityList.innerHTML = "";
          formatArray.forEach(function (item) {
            var li = document.createElement("li");
            li.className = "format-priority-item";
            li.dataset.format = item.format;
            li.dataset.priority = item.priority;
            li.innerHTML =
              '<div class="lily-format-priority-row">' +
                '<span class="lily-format-priority-handle" aria-hidden="true">☰</span>' +
                '<span class="lily-format-priority-name">' + escapeHtml(item.format) + "</span>" +
                '<span class="lily-format-priority-badge">' + escapeHtml(i18n.priority + ": " + item.priority) + "</span>" +
              "</div>";
            priorityList.appendChild(li);
          });
          makeSortable(priorityList, ".format-priority-item", function (item, on) {
            item.style.opacity = on ? "0.5" : "1";
          }, updatePriorityInput);
        }
      } catch (e) {
        console.error("Error initializing format priority list:", e);
        console.error("Hidden input value:", priorityInput.value);
        renderPriorityError(i18n.formatError + " " + e.message);
      }
    }
  });
})();
