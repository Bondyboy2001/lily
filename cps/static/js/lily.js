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

  // Quiet motion: no Bootstrap fade/slide transitions (DESIGN.md §1).
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

  function pickFormat(formats, priority) {
    for (var i = 0; i < priority.length; i++) {
      if (formats.indexOf(priority[i]) !== -1) { return priority[i]; }
    }
    return formats[0] || null;
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
      var $box = $(this).closest(".lily-cover-actions");
      var formats = String($box.data("book-formats") || "").split(",").filter(Boolean);
      var format = pickFormat(formats, ["epub", "pdf", "txt", "html", "mobi", "azw3", "fb2", "cbz", "cbr"]);
      if (format) {
        window.open(root + "/read/" + $box.data("book-id") + "/" + format, "_blank", "noopener");
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
        var $img = $book.find(".cover .img");
        $img.find(".badge.read").remove();
        if (nowRead) {
          $("<span class='badge read glyphicon glyphicon-ok'></span>").attr("title", $btn.data("label-read")).appendTo($img);
        }
      }).fail(function (xhr) {
        flash((xhr.responseJSON && xhr.responseJSON.message) || "Could not change the read status. Try again.", "danger");
      }).always(function () {
        $btn.removeClass("is-busy");
      });
    });

    $(document).on("click", ".lily-cover-actions .lily-send-ereader", function () {
      var $btn = $(this);
      var $box = $btn.closest(".lily-cover-actions");
      var formats = String($box.data("book-formats") || "").split(",").filter(Boolean);
      var format = pickFormat(formats, ["epub", "pdf", "mobi", "azw3", "azw"]);
      var email = window.cwaUserData && window.cwaUserData.primaryEmail;
      if (!format || !email) { return; }
      $btn.addClass("is-busy");
      $.ajax({
        url: root + "/send_selected/" + $box.data("book-id"),
        type: "POST",
        headers: { "X-Requested-With": "XMLHttpRequest" },
        data: { csrf_token: csrfToken(), selected_emails: email, book_format: format.toUpperCase(), convert: 0 }
      }).done(function (response) {
        if (response && response.length && response[0].message) { flash(response[0].message, "success"); }
      }).fail(function (xhr) {
        var message = "Sending failed. Check your eReader email settings and try again.";
        try {
          var body = JSON.parse(xhr.responseText);
          if (body && body.length && body[0].message) { message = body[0].message; }
        } catch (err) { /* keep the default message */ }
        flash(message, "danger");
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
  var POLL_MS = 500;
  var MAX_ATTEMPTS = 600; // ~5 minutes of visible polling
  var interval = null;
  var attempts = 0;
  var inFlight = false;

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
    box.classList.remove("is-busy", "is-done", "is-error", "is-leaving");
    box.classList.add("is-" + state);
    var icon = box.querySelector(".lily-refresh-toast-icon");
    if (icon) {
      icon.className = "glyphicon lily-refresh-toast-icon " +
        (state === "busy" ? "glyphicon-refresh" : state === "error" ? "glyphicon-warning-sign" : "glyphicon-ok");
    }
    // Sit just under the top bar, whose height grows when the search box wraps on phones.
    var bar = document.querySelector(".lily-topbar");
    if (bar) { box.style.top = Math.max(0, bar.getBoundingClientRect().bottom) + 12 + "px"; }
    box.hidden = false;
    cancelHide();
    if (state !== "busy") { scheduleHide(); }
    if (!box.dataset.hoverWired) {
      box.dataset.hoverWired = "1";
      box.addEventListener("mouseenter", cancelHide);
      box.addEventListener("mouseleave", function () {
        if (!box.hidden && !box.classList.contains("is-busy")) { scheduleHide(); }
      });
    }
  }

  function stopChecking() {
    if (interval) {
      clearInterval(interval);
      interval = null;
    }
  }

  function checkMessages() {
    // Pause while the tab is hidden and never overlap requests
    if (document.hidden || inFlight) { return; }
    attempts += 1;
    if (attempts > MAX_ATTEMPTS) {
      stopChecking();
      return;
    }
    inFlight = true;
    fetch(root + "/cwa-library-refresh/messages", { credentials: "same-origin" })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (data.messages.length > 0) {
          var failed = data.messages.some(function (m) { return m.indexOf("⛔") !== -1; });
          showMessage(data.messages, failed ? "error" : "done");
          stopChecking();
        }
      })
      .catch(function (error) { console.error("Error fetching messages:", error); })
      .finally(function () { inFlight = false; });
  }

  window.refreshLibrary = function () {
    var csrfInput = document.querySelector("input[name='csrf_token']");
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
        if (!interval) {
          attempts = 0;
          interval = setInterval(checkMessages, POLL_MS);
        }
      })
      .catch(function (error) {
        console.error("Error:", error);
        var box = toast();
        showMessage(box ? box.getAttribute("data-error-message") : "Library refresh failed.", "error");
      });
  };

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
    $(input).on("typeahead:select typeahead:autocomplete", function () {
      $(this).closest("form").trigger("submit");
    });
  });
})(window.jQuery);

/*
 * Colour theme button (layout.html #lily-theme-toggle): System → Light → Dark → System.
 * lily_theme_head.html applies the stored choice before first paint; this keeps it in step.
 */
(function () {
  "use strict";

  var ORDER = ["system", "light", "dark"];
  var COLOURS = { light: "#FDFCFA", dark: "#1B1719" };

  function apply(pref) {
    var root = document.documentElement;
    root.setAttribute("data-theme-pref", pref);
    if (pref === "system") { root.removeAttribute("data-theme"); } else { root.setAttribute("data-theme", pref); }
    var metas = document.querySelectorAll('meta[name="theme-color"]');
    for (var i = 0; i < metas.length; i++) {
      var media = metas[i].getAttribute("media") || "";
      var scheme = pref !== "system" ? pref : (media.indexOf("dark") !== -1 ? "dark" : "light");
      metas[i].setAttribute("content", COLOURS[scheme]);
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
    var current = document.documentElement.getAttribute("data-theme-pref") || "system";
    label(btn, current);
    btn.addEventListener("click", function () {
      current = ORDER[(ORDER.indexOf(current) + 1) % ORDER.length];
      try { localStorage.setItem("lily-theme", current); } catch (e) {}
      apply(current);
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
