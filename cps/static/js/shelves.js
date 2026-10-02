/*
 * Changing a book's shelves without leaving the page (shelf.set_book_on_shelf).
 * - Book page (detail.html): the Shelves menu's checkbox items put the book on a shelf or take
 *   it off at once; the facts panel's Shelves row and the sidebar counts follow.
 * - Shelf page (shelf.html): a cover's remove button takes the book off this shelf and the card goes.
 * Failures go to the page's message region (window.lilyFlash) and leave everything as it was.
 */
$(function () {
  "use strict";

  function setOnShelf(url, on) {
    return $.ajax({
      method: "POST",
      url: url,
      contentType: "application/json",
      dataType: "json",
      data: JSON.stringify({ on: on })
    });
  }

  function failed(xhr, fallback) {
    var message = (xhr && xhr.responseJSON && xhr.responseJSON.message) || fallback;
    if (window.lilyFlash) { window.lilyFlash(message, "danger"); }
  }

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

  // Book page: Shelves menu.
  $(document).on("click", ".book-shelves-menu .book-shelf-toggle", function (event) {
    event.preventDefault();
    event.stopPropagation(); // keep the menu open so several shelves can be ticked in a row
    var $item = $(this);
    if ($item.attr("aria-disabled") === "true") { return; }
    var on = $item.attr("aria-checked") !== "true";
    var $menu = $item.closest(".dropdown-menu");
    $item.attr("aria-disabled", "true");
    setOnShelf($item.data("url"), on).done(function (data) {
      $item.attr("aria-checked", data.on ? "true" : "false");
      var $row = $("#book-shelves-row");
      $row.find("a[data-shelf-id='" + $item.data("shelf-id") + "']").prop("hidden", !data.on);
      $row.prop("hidden", !$row.find("a[data-shelf-id]:not([hidden])").length);
      updateSidebarCount($item.data("shelf-url"), data.count);
    }).fail(function (xhr) {
      failed(xhr, $menu.data("failed"));
    }).always(function () {
      $item.removeAttr("aria-disabled");
    });
  });

  // Shelf page: remove a book from this shelf.
  $(document).on("click", ".lily-cover-actions .lily-shelf-remove", function () {
    var $btn = $(this);
    if ($btn.hasClass("is-busy")) { return; }
    $btn.addClass("is-busy");
    setOnShelf($btn.data("url"), false).done(function (data) {
      var $card = $btn.closest(".lily-book");
      updateSidebarCount($btn.data("shelf-url"), data.count);
      $card.remove();
      if (!data.count) { window.location.reload(); } // shows the shelf's empty state
    }).fail(function (xhr) {
      $btn.removeClass("is-busy");
      failed(xhr, $btn.data("failed"));
    });
  });
});
