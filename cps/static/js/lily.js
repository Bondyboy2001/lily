/*
 * Lily shell behaviour (templates/layout.html, css/lily-shell.css).
 * Phone widths: the sidebar is a drawer opened by .lily-drawer-toggle and closed by the scrim or Escape.
 * Cmd/Ctrl+B toggles the sidebar: the drawer on phones, a remembered collapse on wider screens.
 * Plain DOM APIs only, so it does not depend on the jQuery version.
 */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var app = document.querySelector(".lily-app");
    var toggle = document.querySelector(".lily-drawer-toggle");
    var scrim = document.querySelector(".lily-scrim");
    if (!app || !toggle) { return; }

    function setOpen(open) {
      app.classList.toggle("drawer-open", open);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    }

    toggle.addEventListener("click", function () {
      setOpen(!app.classList.contains("drawer-open"));
    });

    if (scrim) {
      scrim.addEventListener("click", function () { setOpen(false); });
    }

    var phone = window.matchMedia("(max-width: 767px)");

    document.addEventListener("keydown", function (e) {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && (e.key === "b" || e.key === "B")) {
        if (e.target && e.target.isContentEditable) { return; }
        e.preventDefault();
        if (phone.matches) {
          setOpen(!app.classList.contains("drawer-open"));
        } else {
          var collapsed = app.classList.toggle("sidebar-collapsed");
          try { localStorage.setItem("lily-sidebar-collapsed", collapsed ? "1" : "0"); } catch (err) {}
        }
        return;
      }
      if (e.key === "Escape" && app.classList.contains("drawer-open")) {
        setOpen(false);
        toggle.focus();
      }
    });
  });
})();

/*
 * Page behaviours that used to live in caliBlur.js, without its DOM rearranging.
 * jQuery and Bootstrap 3 are loaded before this file (layout.html).
 */
(function ($) {
  "use strict";
  if (!$) { return; }

  var root = window.scriptRoot || "";

  // Quiet motion: no Bootstrap fade/slide transitions (docs/design.md §1).
  $.support.transition = false;

  function csrfToken() {
    return $("input[name='csrf_token']").first().val() || "";
  }

  function flash(message, type) {
    var tone = type === "error" ? "danger" : type;
    $("#flash_danger, #flash_success").closest(".row-fluid").remove();
    var $row = $("<div class='row-fluid'></div>");
    var $alert = $("<div></div>").attr({ id: "flash_" + tone, "class": "alert alert-" + tone }).text(message).appendTo($row);
    // #messageContainer (layout.html) is the page's polite live region; errors also interrupt.
    if (tone === "danger") { $alert.attr("role", "alert"); }
    var $region = $("#messageContainer");
    if ($region.length) { $region.append($row); } else { $(".navbar").first().after($row); }
  }

  // Exposed so other scripts (e.g. table.js) can report failures the same way.
  window.lilyFlash = flash;

  // Flash messages step aside by themselves: a short while for news, a little longer for
  // errors. Notices with their own close button (update, setup) stay until dismissed.
  var FLASH_MS = 3000, FLASH_ERROR_MS = 5000;
  function autoHide(el) {
    if (el.dataset.lilyAutoHide || $(el).is(".alert-cwa") || $(el).find(".close").length) { return; }
    el.dataset.lilyAutoHide = "1";
    setTimeout(function () {
      var $row = $(el).closest(".row-fluid");
      ($row.length ? $row : $(el)).remove();
    }, $(el).is(".alert-danger") ? FLASH_ERROR_MS : FLASH_MS);
  }
  function hideFlashes() {
    $("[id^='flash_'].alert").each(function () { autoHide(this); });
  }
  $(hideFlashes);
  if (window.MutationObserver) {
    $(function () {
      new MutationObserver(hideFlashes).observe(document.body, { childList: true, subtree: true });
    });
  }

  function setLabel($btn, label) {
    $btn.attr({ title: label, "aria-label": label });
  }

  $(function () {
    // No JS tooltips: the design calls for none. The controls keep their title/aria-label
    // attributes, so the browser's own tooltip is the only popup and screen readers still
    // announce the control. (An earlier Bootstrap tooltip was also the cause of a hover
    // flicker on the book page's action icons, since the tooltip was appended to <body>
    // and could land on top of the button that opened it.)

    // Links to other sites open in a new tab.
    $("a[href]").filter(function () {
      return this.hostname && this.hostname !== location.hostname;
    }).attr({ target: "_blank", rel: "noopener" }).addClass("external");

    // A dropdown that would run off the right edge opens leftwards instead.
    $(document).on("shown.bs.dropdown", function (e) {
      var $menu = $(e.target).find(".dropdown-menu").first();
      if (!$menu.length) { return; }
      $menu.removeClass("dropdown-menu-right");
      var rect = $menu[0].getBoundingClientRect();
      if (rect.right > document.documentElement.clientWidth - 8) {
        $menu.addClass("dropdown-menu-right");
      }
    });

    // Menus opened by hand (display toggled rather than Bootstrap's .open) close on click-off.
    $(document).on("mouseup", function (e) {
      $(".dropdown-menu:visible").each(function () {
        var $menu = $(this);
        if ($menu.is(".datepicker, .dropdown-context") || $menu.closest(".open, .dropdown, .btn-group, .bootstrap-select").length) { return; }
        if (!$menu.is(e.target) && $menu.has(e.target).length === 0) { $menu.hide(); }
      });
    });

    // Quick actions under grid covers (image.html cover_actions).
    $(document).on("click", ".lily-cover-actions .lily-read-now", function () {
      var url = this.getAttribute("data-reader-url");
      if (url) {
        window.open(url, "_blank", "noopener");
      }
    });

    $(document).on("click", ".lily-cover-actions .lily-toggle-read", function () {
      var $btn = $(this);
      var $book = $btn.closest(".lily-book");
      var bookId = $btn.closest(".lily-cover-actions").data("book-id");
      $btn.addClass("is-busy");
      $.ajax({
        url: root + "/ajax/toggleread/" + bookId,
        type: "POST",
        data: { csrf_token: csrfToken() },
        headers: { "X-Requested-With": "XMLHttpRequest" }
      }).done(function () {
        var nowRead = $btn.attr("aria-pressed") !== "true";
        $btn.attr("aria-pressed", nowRead ? "true" : "false");
        setLabel($btn, nowRead ? $btn.data("label-read") : $btn.data("label-unread"));
        $book.toggleClass("is-read", nowRead);
        var $img = $book.find(".cover .img");
        $img.find(".badge.read").remove();
        if (nowRead) {
          $("<span class='badge read is-new glyphicon glyphicon-eye-open'></span>").attr("title", $btn.data("label-read")).appendTo($img);
        }
      }).fail(function (xhr) {
        flash((xhr.responseJSON && xhr.responseJSON.message) || "Could not change the read status. Try again.", "danger");
      }).always(function () {
        $btn.removeClass("is-busy");
      });
    });
  });
})(window.jQuery);

/*
 * Library refresh button (#refresh-library) and its status toast (#message_library_refresh).
 * Globals because layout.html wires them with onclick attributes.
 */
(function () {
  "use strict";

  var root = window.scriptRoot || "";
  var POLL_MS = 2000;
  var timer = null;
  var inFlight = false;
  var pollDelay = POLL_MS;
  var activeStatusUrl = null;

  var jobStorageKey = "lily.refreshJob." +
    ((document.body && document.body.getAttribute("data-user-id")) || "anonymous");

  function rememberJob(statusUrl) {
    try { window.localStorage.setItem(jobStorageKey, statusUrl); } catch (e) { /* storage optional */ }
  }

  function forgetJob() {
    try { window.localStorage.removeItem(jobStorageKey); } catch (e) { /* storage optional */ }
  }

  function recalledJob() {
    try { return window.localStorage.getItem(jobStorageKey); } catch (e) { return null; }
  }

  var TOAST_MS = 2500; // how long a finished result stays up
  var hideTimer = null;

  function toast() { return document.getElementById("message_library_refresh"); }

  // The server's messages start with "Library Refresh 🔄" and end with a status emoji.
  // The toast's own icon carries both, so they are trimmed from the text.
  function tidy(text) {
    return String(text).replace(/^[^🔄]*🔄\s*/, "").replace(/\s*[✅⛔⌛]\s*$/, "");
  }

  function cancelHide() {
    if (hideTimer) {
      clearTimeout(hideTimer);
      hideTimer = null;
    }
  }

  function scheduleHide() {
    cancelHide();
    hideTimer = setTimeout(window.dismissLibraryRefreshMessage, TOAST_MS);
  }

  // state: "busy" while the refresh runs (stays up), "done" or "error" once it has finished
  // (leaves by itself after TOAST_MS, but not while the pointer is over it).
  function showMessage(messages, state) {
    var box = toast();
    var para = document.getElementById("library_refresh_message");
    if (!box || !para) { return; }
    para.textContent = "";
    [].concat(messages).forEach(function (text, i) {
      if (i) { para.appendChild(document.createElement("br")); }
      para.appendChild(document.createTextNode(tidy(text)));
    });
    if (state === "error") {
      var links = [];
      if (box.dataset.failedImportsUrl) {
        links.push([box.dataset.failedImportsUrl,
                    box.dataset.failedImportsLabel || "Failed imports"]);
      }
      if (box.dataset.logsUrl) {
        links.push([box.dataset.logsUrl, box.dataset.logsLabel || "Logs"]);
      }
      links.forEach(function (pair) {
        para.appendChild(document.createTextNode(" "));
        var a = document.createElement("a");
        a.href = pair[0];
        a.textContent = pair[1];
        para.appendChild(a);
      });
    }
    box.classList.remove("is-busy", "is-done", "is-error", "is-leaving");
    box.classList.add("is-" + state);
    var icon = box.querySelector(".lily-refresh-toast-icon");
    if (icon) {
      icon.className = "glyphicon lily-refresh-toast-icon " +
        (state === "busy" ? "glyphicon-refresh" : state === "error" ? "glyphicon-warning-sign" : "glyphicon-ok");
    }
    box.hidden = false;
    cancelHide();
    if (state === "done") { scheduleHide(); }
    if (!box.dataset.hoverWired) {
      box.dataset.hoverWired = "1";
      box.addEventListener("mouseenter", cancelHide);
      box.addEventListener("mouseleave", function () {
        if (!box.hidden && box.classList.contains("is-done")) { scheduleHide(); }
      });
    }
  }

  function stopChecking() {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
  }

  function setButtonBusy(busy) {
    var btn = document.getElementById("refresh-library");
    if (btn) {
      btn.classList.toggle("disabled", busy);
      btn.setAttribute("aria-disabled", busy ? "true" : "false");
    }
  }

  function schedulePoll(delay) {
    if (timer || !activeStatusUrl) { return; }
    timer = setTimeout(function () {
      timer = null;
      pollJob();
    }, delay);
  }

  function finishJob(job, state) {
    activeStatusUrl = null;
    forgetJob();
    setButtonBusy(false);
    showMessage(job && job.message ? job.message : "", state);
  }

  function pollJob() {
    if (!activeStatusUrl || inFlight) { return; }
    if (document.hidden) {
      schedulePoll(POLL_MS);
      return;
    }
    inFlight = true;
    fetch(activeStatusUrl, { credentials: "same-origin",
                             headers: { "Accept": "application/json" } })
      .then(function (response) {
        var type = response.headers.get("Content-Type") || "";
        if (!response.ok || type.indexOf("json") === -1) {
          var box = toast();
          showMessage("Status unavailable; job may still be running", "busy");
          pollDelay = Math.min(pollDelay * 2, 30000);
          schedulePoll(pollDelay);
          return null;
        }
        return response.json();
      })
      .then(function (job) {
        if (!job) { return; }
        pollDelay = POLL_MS;
        if (job.state === "running") {
          showMessage(job.message || "", "busy");
          schedulePoll(POLL_MS);
        } else if (job.state === "succeeded" || job.state === "skipped") {
          finishJob(job, "done");
        } else {
          finishJob(job, "error");
        }
      })
      .catch(function () {
        var box = toast();
        showMessage("Status unavailable; job may still be running", "busy");
        pollDelay = Math.min(pollDelay * 2, 30000);
        schedulePoll(pollDelay);
      })
      .finally(function () { inFlight = false; });
  }

  function resumeJob(statusUrl) {
    if (!statusUrl) { return; }
    activeStatusUrl = statusUrl;
    setButtonBusy(true);
    showMessage("", "busy");
    schedulePoll(0);
  }

  window.refreshLibrary = function () {
    var btn = document.getElementById("refresh-library");
    if (btn && btn.classList.contains("disabled")) { return; }
    var csrfInput = document.querySelector("input[name='csrf_token']");
    setButtonBusy(true);
    fetch(root + "/cwa-library-refresh", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfInput ? csrfInput.value : ""
      }
    })
      .then(function (response) {
        if (!response.ok) { throw new Error("HTTP " + response.status); }
        return response.json();
      })
      .then(function (data) {
        showMessage(data.message, "busy");
        if (data.status_url) {
          rememberJob(data.status_url);
          resumeJob(data.status_url);
        } else {
          setButtonBusy(false);
        }
      })
      .catch(function (error) {
        console.error("Error:", error);
        setButtonBusy(false);
        var box = toast();
        showMessage(box ? box.getAttribute("data-error-message") : "Library refresh failed.", "error");
      });
  };

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden && activeStatusUrl) {
      if (timer) { clearTimeout(timer); timer = null; }
      schedulePoll(0);
    }
  });

  // Pick up an in-flight refresh after a navigation or reload.
  (function () {
    var saved = recalledJob();
    if (saved) {
      if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", function () { resumeJob(saved); });
      } else {
        resumeJob(saved);
      }
    }
  })();

  window.dismissLibraryRefreshMessage = function () {
    var box = toast();
    cancelHide();
    if (!box || box.hidden) { return; }
    box.classList.add("is-leaving");
    setTimeout(function () {
      if (box.classList.contains("is-leaving")) {
        box.hidden = true;
        box.classList.remove("is-leaving");
      }
    }, 200);
  };

})();

/*
 * Filter buttons sit in the title bar only when the page has no pagination and is wide enough.
 */
(function () {
  "use strict";

  function update() {
    document.querySelectorAll(".row-fluid").forEach(function (row) {
      var filterheader = row.querySelector(".filterheader");
      if (!filterheader) { return; }
      var hasPagination = !!row.querySelector(".pagination");
      filterheader.classList.toggle("filterheader-fixed", window.innerWidth >= 915 && !hasPagination);
    });
  }

  var rafPending = false;
  document.addEventListener("DOMContentLoaded", update);
  window.addEventListener("resize", function () {
    // At most one update per animation frame
    if (rafPending) { return; }
    rafPending = true;
    window.requestAnimationFrame(function () {
      rafPending = false;
      update();
    });
  });
})();

/*
 * Name-list dropdowns (image.list_menu): tick the picked option and show it on the toggle.
 * filter_list.js / filter_grid.js call this; it returns false when the option was already picked.
 */
window.lilyPickOption = function (item) {
  "use strict";
  var li = item.closest("li");
  if (!li || li.classList.contains("active")) { return false; }
  var menu = li.parentNode;
  Array.prototype.forEach.call(menu.children, function (other) {
    other.classList.remove("active");
    var a = other.querySelector("a");
    if (a) { a.removeAttribute("aria-current"); }
  });
  li.classList.add("active");
  item.setAttribute("aria-current", "true");
  var toggle = menu.parentNode.querySelector(".dropdown-toggle");
  if (toggle) {
    toggle.querySelector(".lily-sort-value").textContent = item.textContent;
    var icon = item.getAttribute("data-icon");
    if (icon) { toggle.querySelector(".glyphicon").className = "glyphicon " + icon; }
  }
  return true;
};

/*
 * Name-list direction button (image.list_menu): flip data-dir, swap the icon, label and tooltip,
 * and return the new direction ("asc" or "desc"). filter_list.js / filter_grid.js call this.
 */
window.lilyToggleSortDir = function (btn) {
  "use strict";
  // The callers reorder the rows next; scroll anchoring would then follow a moved row, so put
  // the page back where it was once they are done.
  var y = window.scrollY;
  window.requestAnimationFrame(function () { window.scrollTo(0, y); });
  var dir = btn.getAttribute("data-dir") === "desc" ? "asc" : "desc";
  var next = dir === "desc" ? "asc" : "desc";
  btn.setAttribute("data-dir", dir);
  btn.querySelector(".glyphicon").className = "glyphicon glyphicon-sort-by-attributes" + (dir === "desc" ? "-alt" : "");
  btn.querySelector(".lily-sort-value").textContent = btn.getAttribute("data-label-" + dir);
  btn.title = btn.getAttribute("data-tip-" + next);
  return dir;
};

/*
 * Book-list direction link (image.sort_menu, #lily-sort-dir-toggle): it reloads the list in the
 * other order, which would land at the top. Remember the scroll position for that URL and return
 * to it when the new page loads.
 */
(function () {
  "use strict";
  var KEY = "lily-sort-scroll";

  document.addEventListener("click", function (e) {
    var link = e.target.closest && e.target.closest("a#lily-sort-dir-toggle");
    if (!link || link.classList.contains("disabled")) { return; }
    try {
      sessionStorage.setItem(KEY, JSON.stringify({ url: link.pathname + link.search, y: window.scrollY }));
    } catch (err) { /* storage blocked: the list just opens at the top */ }
  });

  document.addEventListener("DOMContentLoaded", function () {
    var saved;
    try {
      saved = JSON.parse(sessionStorage.getItem(KEY) || "null");
      sessionStorage.removeItem(KEY);
    } catch (err) { return; }
    if (saved && saved.url === location.pathname + location.search) {
      window.scrollTo(0, saved.y);
    }
  });
})();

/*
 * Top bar search: suggest matching books while typing. The endpoint applies the same
 * visibility rules as the library lists, so nothing hidden is ever suggested.
 * Markup: layout.html (#query + data-suggest-url); menu look: lily-library.css.
 */
(function ($) {
  "use strict";
  if (!$ || !$.fn.typeahead || !window.Bloodhound) { return; }

  $(function () {
    var input = document.getElementById("query");
    var suggestUrl = input && input.getAttribute("data-suggest-url");
    if (!suggestUrl) { return; }

    var books = new Bloodhound({
      datumTokenizer: Bloodhound.tokenizers.obj.whitespace("name"),
      queryTokenizer: Bloodhound.tokenizers.whitespace,
      remote: {url: suggestUrl + "?q=%QUERY", wildcard: "%QUERY"}
    });

    $(input).typeahead({hint: false, minLength: 2}, {
      name: "books",
      display: "name",
      limit: 8,
      source: books,
      templates: {
        // Built as DOM nodes, so titles and authors are escaped rather than injected as HTML.
        suggestion: function (book) {
          var $item = $("<div>").addClass("tt-book");
          if (book.cover) {
            $("<img>").addClass("tt-cover").attr({src: book.cover, alt: "", loading: "lazy"}).appendTo($item);
          }
          var $text = $("<div>").addClass("tt-text").appendTo($item);
          $("<span>").addClass("tt-title").text(book.name).appendTo($text);
          if (book.author) {
            $("<span>").addClass("tt-author").text(book.author).appendTo($text);
          }
          return $item;
        }
      }
    });

    // The box is a search form, so picking a suggestion runs that search.
    $(input).on("typeahead:select", function (event, book) {
      if (book && book.url) {
        window.location.assign(book.url);
      }
    });
  });
})(window.jQuery);

/*
 * Colour theme button (layout.html #lily-theme-toggle): Light ↔ Dark.
 * lily_theme_head.html applies the stored choice before first paint; this keeps it in step.
 */
(function () {
  "use strict";

  var COLOURS = { light: "#F1EEEA", dark: "#1A1517" };

  function apply(pref) {
    var root = document.documentElement;
    root.setAttribute("data-theme-pref", pref);
    root.setAttribute("data-theme", pref);
    var metas = document.querySelectorAll('meta[name="theme-color"]');
    for (var i = 0; i < metas.length; i++) {
      metas[i].setAttribute("content", COLOURS[pref]);
    }
  }

  function label(btn, pref) {
    var text = btn.getAttribute("data-label-" + pref) || pref;
    btn.setAttribute("title", text);
    btn.setAttribute("aria-label", text);
  }

  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("lily-theme-toggle");
    if (!btn) { return; }
    var current = document.documentElement.getAttribute("data-theme-pref") || "light";
    label(btn, current);
    btn.addEventListener("click", function () {
      current = current === "dark" ? "light" : "dark";
      try { localStorage.setItem("lily-theme", current); } catch (e) {}
      // A cross-fade of the whole page where the browser supports it, an instant swap elsewhere.
      var swap = function () { apply(current); };
      if (document.startViewTransition && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        document.startViewTransition(swap);
      } else {
        swap();
      }
      label(btn, current);
      // Charts read their colours once when drawn; redraw them in the new scheme.
      if (document.querySelector("[_echarts_instance_]")) { window.location.reload(); }
    });
  });
})();

/* Admin "Get started" card (index.html): dismissed per user, per browser. */
(function () {
  "use strict";
  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest(".lily-setup-dismiss");
    if (!btn) { return; }
    var card = btn.closest(".lily-setup");
    if (!card) { return; }
    try { localStorage.setItem(card.getAttribute("data-dismiss-key"), "1"); } catch (err) {}
    card.hidden = true;
    var main = document.getElementById("lily-content");
    if (main) { main.focus(); }
  });
})();

/* Stagger index for the cover grid's entrance animation (lily-library.css reads --i). */
(function () {
  "use strict";
  document.addEventListener("DOMContentLoaded", function () {
    var cards = document.querySelectorAll(".lily-grid > .lily-book");
    for (var i = 0; i < cards.length; i++) { cards[i].style.setProperty("--i", i); }
  });
})();

/* Quick-action bar colour: take the dominant colour of the strip of cover the bar sits on (as
   cropped by object-fit: cover), so the bar reads as part of the cover. Sets --cover-tint on the
   bar and data-tone so lily-library.css picks a legible ink. */
(function () {
  "use strict";
  var W = 32, H = 8, canvas, ctx;

  function tint(img) {
    var card = img.closest(".lily-book"), bar = card && card.querySelector(".lily-cover-actions");
    var box = img.closest(".cover");
    if (!bar || !box || !img.naturalWidth) { return; }
    if (!ctx) {
      canvas = document.createElement("canvas");
      canvas.width = W;
      canvas.height = H;
      ctx = canvas.getContext("2d", { willReadFrequently: true });
    }
    try {
      var nw = img.naturalWidth, nh = img.naturalHeight;
      var bw = box.clientWidth || nw, bh = box.clientHeight || nh;
      var scale = Math.max(bw / nw, bh / nh);
      var visW = bw / scale, visH = bh / scale;
      var sx = (nw - visW) / 2, bottom = (nh - visH) / 2 + visH;
      var sh = Math.max(1, (bar.offsetHeight || 38) / scale);
      ctx.drawImage(img, sx, bottom - sh, visW, sh, 0, 0, W, H);
      var d = ctx.getImageData(0, 0, W, H).data, buckets = {}, best = null;
      for (var i = 0; i < d.length; i += 4) {
        var key = (d[i] >> 4) + "," + (d[i + 1] >> 4) + "," + (d[i + 2] >> 4);
        var b = buckets[key] || (buckets[key] = { n: 0, r: 0, g: 0, b: 0 });
        b.n++; b.r += d[i]; b.g += d[i + 1]; b.b += d[i + 2];
        if (!best || b.n > best.n) { best = b; }
      }
      var r = Math.round(best.r / best.n), g = Math.round(best.g / best.n), bl = Math.round(best.b / best.n);
      bar.style.setProperty("--cover-tint", "rgb(" + r + "," + g + "," + bl + ")");
      bar.setAttribute("data-tone", (0.2126 * r + 0.7152 * g + 0.0722 * bl) > 150 ? "light" : "dark");
    } catch (err) { /* tainted or undecodable image: keep the surface fallback */ }
  }

  document.addEventListener("load", function (e) {
    if (e.target.tagName === "IMG" && e.target.closest(".lily-book .cover")) { tint(e.target); }
  }, true);
  document.addEventListener("DOMContentLoaded", function () {
    var imgs = document.querySelectorAll(".lily-book .cover img");
    for (var i = 0; i < imgs.length; i++) { if (imgs[i].complete) { tint(imgs[i]); } }
  });
})();
