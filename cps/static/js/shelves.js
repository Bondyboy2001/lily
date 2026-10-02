/*
 * Shelf page (shelf.html): a cover's remove button takes the book off this shelf
 * (shelf.set_book_on_shelf) and the card goes; the sidebar count follows. A failure goes to the
 * page's message region (window.lilyFlash) and leaves the card where it was.
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
