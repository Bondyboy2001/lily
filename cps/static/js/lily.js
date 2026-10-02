/*
 * Lily shell behaviour (templates/layout.html, css/lily-shell.css).
 * On phones the sidebar is a drawer that .lily-drawer-toggle and Cmd/Ctrl+B open and close
 * (the scrim and Escape close it too); wider screens always show it and hide the toggle.
 * While the drawer is open the page behind it is inert and focus sits in the drawer;
 * closing it puts focus back on the toggle.
 * Plain DOM APIs only, so it does not depend on the jQuery version.
 */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var app = document.querySelector(".lily-app");
    var toggle = document.querySelector(".lily-drawer-toggle");
    var scrim = document.querySelector(".lily-scrim");
    var sidebar = document.getElementById("lily-sidebar");
    var main = document.querySelector(".lily-main");
    if (!app || !toggle) { return; }

    var phone = window.matchMedia("(max-width: 767px)");
    // The tooltip names the shortcut with ⌘; other platforms use Ctrl.
    var shortcut = /Mac|iPhone|iPad/.test(navigator.platform || "") ? "⌘B" : "Ctrl+B";

    // aria-expanded and the label follow the drawer.
    function sync() {
      var shown = app.classList.contains("drawer-open");
      var label = shown ? toggle.getAttribute("data-label-hide") : toggle.getAttribute("data-label-show");
      toggle.setAttribute("aria-expanded", shown ? "true" : "false");
      if (label) {
        toggle.setAttribute("aria-label", label);
        toggle.title = label + " (" + shortcut + ")";
      }
    }

    function setOpen(open, restoreFocus) {
      var wasOpen = app.classList.contains("drawer-open");
      app.classList.toggle("drawer-open", open);
      if (main) { main.inert = open; }
      sync();
      if (open && !wasOpen && sidebar) {
        var first = sidebar.querySelector(".lily-nav a[href]") || sidebar.querySelector("a[href]");
        if (first) { first.focus(); }
      } else if (!open && wasOpen && restoreFocus !== false) {
        toggle.focus();
      }
    }

    toggle.addEventListener("click", function () { setOpen(!app.classList.contains("drawer-open")); });

    if (scrim) {
      scrim.addEventListener("click", function () { setOpen(false); });
    }

    // Leaving phone width with the drawer open would leave the page inert.
    function widthChanged() {
      if (!phone.matches && app.classList.contains("drawer-open")) { setOpen(false, false); }
      sync();
    }
    if (phone.addEventListener) { phone.addEventListener("change", widthChanged); } else if (phone.addListener) { phone.addListener(widthChanged); }
    sync();

    document.addEventListener("keydown", function (e) {
      if (phone.matches && (e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && (e.key === "b" || e.key === "B")) {
        if (e.target && e.target.isContentEditable) { return; }
        e.preventDefault();
        setOpen(!app.classList.contains("drawer-open"));
        return;
      }
      if (e.key === "Escape" && app.classList.contains("drawer-open")) {
        setOpen(false);
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
    // #messageContainer (layout.html) is the page's polite live region; errors also interrupt,
    // and stay until they are closed so there is time to read what went wrong.
    if (tone === "danger") {
      $alert.attr("role", "alert");
      $("<button type='button' class='close' data-dismiss='alert'><span aria-hidden='true'>&times;</span></button>")
        .attr("aria-label", "Close").appendTo($alert);
    }
    var $region = $("#messageContainer");
    if ($region.length) { $region.append($row); } else { $(".navbar").first().after($row); }
  }

  // Exposed so other scripts (e.g. table.js) can report failures the same way.
  window.lilyFlash = flash;

  // News steps aside by itself after a while. Errors and notices with their own close button
  // (update, setup) stay until dismissed.
  var FLASH_MS = 5000;
  function autoHide(el) {
    if (el.dataset.lilyAutoHide || $(el).is(".alert-cwa, .alert-danger") || $(el).find(".close").length) { return; }
    el.dataset.lilyAutoHide = "1";
    setTimeout(function () {
      var $row = $(el).closest(".row-fluid");
      ($row.length ? $row : $(el)).remove();
    }, FLASH_MS);
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

  // Cover buttons carry no title: the design wants no popups over them, so only screen readers get the label.
  function setLabel($btn, label) {
    $btn.attr("aria-label", label);
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

    // Books open in a new tab, so the library stays where it was. Installed as an app (added
    // to the home screen), a new tab would leave the app for the browser, so there the reader
    // opens in place and its Back link returns to the book.
    var installedApp = window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
    if (installedApp) {
      $("a[data-reader-link]").each(function () {
        this.removeAttribute("target");
        var label = this.getAttribute("data-reader-link");
        if (label) { this.setAttribute("aria-label", label); }
      });
    }

    // Quick actions under grid covers (image.html cover_actions).
    $(document).on("click", ".lily-cover-actions .lily-read-now", function () {
      var url = this.getAttribute("data-reader-url");
      if (!url) { return; }
      if (installedApp) {
        window.location.href = url;
      } else {
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
        // The label names the next action and flips, so the state is a class (design §5.1).
        var nowRead = !$btn.hasClass("is-on");
        $btn.toggleClass("is-on", nowRead);
        setLabel($btn, $btn.attr(nowRead ? "data-label-read" : "data-label-unread"));
        $book.toggleClass("is-read", nowRead);
        var $img = $book.find(".cover .img");
        $img.find(".badge.read").remove();
        if (nowRead) {
          $("<span class='badge read is-new glyphicon glyphicon-ok-circle' aria-hidden='true'></span>").appendTo($img);
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

  var TOAST_MS = 6000; // how long a finished result stays up: long enough to reach its link
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
  // (leaves by itself after TOAST_MS, but not while the pointer or focus is on it).
  // link, when given, is [href, label] and follows the text.
  function showMessage(messages, state, link) {
    var box = toast();
    var para = document.getElementById("library_refresh_message");
    if (!box || !para) { return; }
    para.textContent = "";
    [].concat(messages).forEach(function (text, i) {
      if (i) { para.appendChild(document.createElement("br")); }
      para.appendChild(document.createTextNode(tidy(text)));
    });
    if (link) {
      para.appendChild(document.createTextNode(" "));
      var more = document.createElement("a");
      more.href = link[0];
      more.textContent = link[1];
      para.appendChild(more);
    }
    if (state === "error") {
      var links = [];
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
      // The pointer or keyboard focus on the toast (its "Show" link, Close) holds it up.
      var resume = function (next) {
        if (box.hidden || !box.classList.contains("is-done")) { return; }
        if (box.matches(":hover") || box.contains(next || document.activeElement)) { return; }
        scheduleHide();
      };
      box.addEventListener("mouseenter", cancelHide);
      box.addEventListener("focusin", cancelHide);
      box.addEventListener("mouseleave", function () { resume(); });
      box.addEventListener("focusout", function (e) { resume(e.relatedTarget); });
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

  // Where the books a refresh or import added can be seen: the one book, or the newest first
  function addedLink(count, bookId) {
    var box = toast();
    if (!box || !count) { return null; }
    return count === 1 && bookId
      ? [box.dataset.bookUrl.replace(/0$/, String(bookId)), box.dataset.bookLabel]
      : [box.dataset.libraryUrl, box.dataset.libraryLabel];
  }

  function finishJob(job, state) {
    activeStatusUrl = null;
    forgetJob();
    setButtonBusy(false);
    showMessage(job && job.message ? job.message : "", state,
                job && state === "done" ? addedLink(job.count, job.book_id) : null);
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

  /*
   * Browser uploads: uploadprogress.js leaves the queued ingest file names in sessionStorage,
   * and this follows them in the same toast until the ingest has finished with each one.
   */
  var UPLOAD_KEY = "lily.pendingUploads";
  var UPLOAD_GIVE_UP_MS = 15 * 60 * 1000; // the ingest's own per-file timeout
  var DONE_STATES = ["succeeded", "failed", "skipped", "interrupted"];

  function pendingUploads() {
    try { return JSON.parse(window.sessionStorage.getItem(UPLOAD_KEY) || "null"); } catch (e) { return null; }
  }

  function forgetUploads() {
    try { window.sessionStorage.removeItem(UPLOAD_KEY); } catch (e) { /* storage optional */ }
  }

  // The ingest's reason, when it reads as a sentence for people (not a tool's stderr dump).
  function importFailureReason(files, name) {
    var file = files.filter(function (f) { return f.file === name; })[0];
    var reason = file && String(file.error || "").trim();
    if (!reason || reason.length > 160 || /calibredb|traceback|exited with|errno|\.py\b/i.test(reason)) { return ""; }
    reason = reason.charAt(0).toUpperCase() + reason.slice(1);
    return /[.!?]$/.test(reason) ? reason : reason + ".";
  }

  function uploadText(key, uploads) {
    var box = toast();
    var many = uploads.length > 1;
    var text = (box && box.dataset[many ? key + "Many" : key]) || (box && box.dataset[key]) || "";
    return text.replace("{name}", uploads[0].name).replace("{count}", String(uploads.length));
  }

  function watchUploads() {
    var pending = pendingUploads();
    if (!pending || !pending.uploads || !pending.uploads.length || !pending.statusUrl) { return; }
    if (Date.now() - (pending.since || 0) > UPLOAD_GIVE_UP_MS) {
      forgetUploads();
      return;
    }
    showMessage(uploadText("importing", pending.uploads), "busy");
    var query = pending.uploads.map(function (u) { return "file=" + encodeURIComponent(u.file); }).join("&");
    fetch(pending.statusUrl + "?" + query, { credentials: "same-origin",
                                            headers: { "Accept": "application/json" } })
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (data) {
        if (!data) {
          forgetUploads();
          window.dismissLibraryRefreshMessage();
          return;
        }
        var files = data.files || [];
        var finished = files.filter(function (f) { return DONE_STATES.indexOf(f.state) !== -1; });
        if (finished.length < files.length) {
          setTimeout(watchUploads, POLL_MS);
          return;
        }
        forgetUploads();
        var box = toast();
        var failed = pending.uploads.filter(function (u) {
          return files.some(function (f) { return f.file === u.file && f.state !== "succeeded"; });
        });
        if (failed.length) {
          var lines = [uploadText("importFailed", failed)];
          var reason = failed.length === 1 && importFailureReason(files, failed[0].file);
          if (reason) { lines.push(reason); }
          showMessage(lines, "error");
        } else {
          // One new book: open it. Several: the library, newest first.
          var added = pending.uploads.length === 1 && files[0] && files[0].book_id;
          showMessage(uploadText("imported", pending.uploads), "done",
                      !box ? null : added ? [box.dataset.bookUrl.replace(/0$/, String(added)), box.dataset.bookLabel]
                                          : [box.dataset.libraryUrl, box.dataset.libraryLabel]);
        }
      })
      .catch(function () { setTimeout(watchUploads, POLL_MS * 2); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", watchUploads);
  } else {
    watchUploads();
  }

  /*
   * Books dropped in the ingest folder come with no upload to follow. While the page is open,
   * ask now and then whether any arrived since the last answer, and say so in the toast.
   * A refresh or an upload in this tab reports its own books, so the count starts afresh after.
   */
  var IMPORTS_MS = 20000;
  var importsAfter = null;

  function checkImports() {
    if (document.hidden || activeStatusUrl || pendingUploads()) {
      importsAfter = null;
      setTimeout(checkImports, IMPORTS_MS);
      return;
    }
    var url = root + "/ajax/recent-imports" + (importsAfter !== null ? "?after=" + importsAfter : "");
    fetch(url, { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (data) {
        if (!data) { return; }
        var box = toast();
        if (importsAfter !== null && data.count && box && (box.hidden || !box.classList.contains("is-busy"))) {
          showMessage(data.message, "done", addedLink(data.count, data.book_id));
        }
        importsAfter = data.last;
      })
      .catch(function () { /* offline or signed out: try again later */ })
      .finally(function () { setTimeout(checkImports, IMPORTS_MS); });
  }

  var signedIn = document.body && document.body.dataset && document.body.dataset.userId;
  if (signedIn && signedIn !== "anonymous" && toast() && typeof fetch === "function") {
    checkImports();
  }

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

  function apply(pref) {
    var root = document.documentElement;
    root.setAttribute("data-theme-pref", pref);
    root.setAttribute("data-theme", pref);
    // The browser chrome takes the page colour, read from the token once the theme is set.
    var paper = getComputedStyle(root).getPropertyValue("--paper").trim();
    if (!paper) { return; }
    var metas = document.querySelectorAll('meta[name="theme-color"]');
    for (var i = 0; i < metas.length; i++) {
      metas[i].setAttribute("content", paper);
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

/*
 * Covers more than 5% wider than the A4 tile get .cover-wide (docs/design.md §5.15). Load
 * does not bubble, so a capturing listener catches every cover; earlier loads are swept up.
 */
(function () {
  "use strict";
  var COVERS = ".lily-book .cover img, .continue-reading-item .cover img, .lily-order-cover";
  var WIDEST = (1 / 1.414) * 1.05;

  function mark(img) {
    img.classList.toggle("cover-wide", img.naturalWidth / img.naturalHeight > WIDEST);
  }

  document.addEventListener("load", function (e) {
    var img = e.target;
    if (img.tagName === "IMG" && img.matches(COVERS)) { mark(img); }
  }, true);

  document.addEventListener("DOMContentLoaded", function () {
    var imgs = document.querySelectorAll(COVERS);
    for (var i = 0; i < imgs.length; i++) {
      if (imgs[i].complete) { mark(imgs[i]); }
    }
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
