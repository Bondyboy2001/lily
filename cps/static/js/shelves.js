/*
 * Book page (detail.html): the Shelves menu's checkbox items put the book on a shelf or take it
 * off at once (shelf.set_book_on_shelf); the sidebar counts follow. A failure goes to the page's
 * message region (window.lilyFlash) and leaves the tick as it was. The "On …" line under the
 * action bar shows the link of each shelf the book is on, and hides while it is on none.
 */
$(function () {
  "use strict";

  // The sidebar's count for a shelf: shown only above zero (§5.3).
  function updateSidebarCount(shelfUrl, count) {
    var link = $(".lily-sidebar a").filter(function () { return this.getAttribute("href") === shelfUrl; });
    if (!link.length) { return; }
    var badge = link.find(".nav-count");
    if (count > 0) {
      if (!badge.length) { badge = $("<span class='nav-count'></span>").appendTo(link); }
      badge.text(count);
    } else {
      badge.remove();
    }
  }

  function updateOnShelves(shelfUrl, on) {
    var line = document.getElementById("book-on-shelves");
    if (!line) { return; }
    $(line).children("a").filter(function () { return this.getAttribute("href") === shelfUrl; }).prop("hidden", !on);
    line.hidden = !$(line).children("a:not([hidden])").length;
  }

  $(document).on("click", ".book-shelves-menu .book-shelf-toggle", function (event) {
    event.preventDefault();
    event.stopPropagation(); // keep the menu open so several shelves can be ticked in a row
    var $item = $(this);
    if ($item.attr("aria-disabled") === "true") { return; }
    var on = $item.attr("aria-checked") !== "true";
    var $menu = $item.closest(".dropdown-menu");
    $item.attr("aria-disabled", "true");
    $.ajax({
      method: "POST",
      url: $item.data("url"),
      contentType: "application/json",
      dataType: "json",
      data: JSON.stringify({ on: on })
    }).done(function (data) {
      $item.attr("aria-checked", data.on ? "true" : "false");
      updateSidebarCount($item.data("shelf-url"), data.count);
      updateOnShelves($item.data("shelf-url"), data.on);
    }).fail(function (xhr) {
      var message = (xhr && xhr.responseJSON && xhr.responseJSON.message) || $menu.data("failed");
      if (window.lilyFlash) { window.lilyFlash(message, "danger"); }
    }).always(function () {
      $item.removeAttr("aria-disabled");
    });
  });
});
