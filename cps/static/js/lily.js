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
    $("<div role='status'></div>").attr({ id: "flash_" + tone, "class": "alert alert-" + tone }).text(message).appendTo($row);
    $(".navbar").first().after($row);
  }

  function pickFormat(formats, priority) {
    for (var i = 0; i < priority.length; i++) {
      if (formats.indexOf(priority[i]) !== -1) { return priority[i]; }
    }
    return formats[0] || null;
  }

  function setLabel($btn, label) {
    $btn.attr({ title: label, "aria-label": label, "data-original-title": label });
  }

  $(function () {
    // Tooltips for anything that asks for one; hide them once the control is used.
    if (!$("body").hasClass("epub")) {
      var $tips = $("[data-toggle='tooltip'], [data-toggle-two='tooltip'], .lily-chip[title], .lily-cover-actions .icon-btn[title], .book-action-icons .icon-btn[title], .book-metadata .icon-btn[title]");
      $tips.tooltip({ container: "body", trigger: "hover focus", placement: "bottom" });
      $tips.on("click", function () { $tips.tooltip("hide"); });
    }

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
 * Library refresh button (#refresh-library) and its status notice (#message_library_refresh).
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

  function showMessage(message) {
    var box = document.getElementById("message_library_refresh");
    var para = document.getElementById("library_refresh_message");
    if (box && para) {
      para.innerHTML = message;
      box.style.display = "inline-flex";
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
          showMessage(data.messages.join("<br>"));
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
        showMessage(data.message);
        if (!interval) {
          attempts = 0;
          interval = setInterval(checkMessages, POLL_MS);
        }
      })
      .catch(function (error) {
        console.error("Error:", error);
        var box = document.getElementById("message_library_refresh");
        showMessage(box ? box.getAttribute("data-error-message") : "Library refresh failed.");
      });
  };

  window.dismissLibraryRefreshMessage = function () {
    var box = document.getElementById("message_library_refresh");
    var para = document.getElementById("library_refresh_message");
    if (box && para) {
      para.innerHTML = "";
      box.style.display = "none";
    }
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
