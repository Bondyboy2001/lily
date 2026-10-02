/* Calibre-Web Automated – fork of Calibre-Web
Copyright (C) 2018-2025 Calibre-Web contributors
Copyright (C) 2024-2025 Calibre-Web Automated contributors
SPDX-License-Identifier: GPL-3.0-or-later
See CONTRIBUTORS for full list of authors.
 */


// list.html renders one .lily-list; CSS columns lay it out, so filtering and
// reversing only touch the rows and the columns rebalance on their own.
function getListContainer() {
    return $(".lily-list").first();
}

// The direction button (image.list_menu) flips between ascending and descending; lilyToggleSortDir
// (lily.js) relabels it and returns the new direction. Either way the rows simply reverse.
$(document).on("click", "#lily-order-toggle", function(e) {
    e.preventDefault();
    var dir = lilyToggleSortDir(this);

    var page = $(this).data("id");
    $.ajax({
        method:"post",
        contentType: "application/json; charset=utf-8",
        dataType: "json",
        url: getPath() + "/ajax/view",
        data: JSON.stringify({[page]: {dir: dir}}),
    });
    var list = getListContainer();
    list.append(list.children(".row").get().reverse());
});

$(document).on("click", "#all", function(e) {
    e.preventDefault();
    if (!lilyPickOption(this)) {
        return;
    }
    applyListFilter();
});

$(document).on("click", ".char", function(e) {
    e.preventDefault();
    if (!lilyPickOption(this)) {
        return;
    }
    applyListFilter();
});

// The Filter field (image.list_menu, long lists only): rows whose name lacks the typed text are
// hidden. It narrows within the letter chosen in the Letter menu, so the two stay in step.
function applyListFilter() {
    var text = ($("#lily-list-filter").val() || "").trim().toLowerCase();
    var letter = $(".lily-letter-menu li.active:not(.lily-letter-all) a").text();
    var shown = 0;
    getListContainer().children(".row").each(function() {
        var name = $(this).children("a").first().text().toLowerCase();
        var byLetter = !letter || this.attributes["data-id"].value.charAt(0).toUpperCase() === letter;
        var visible = byLetter && name.indexOf(text) !== -1;
        $(this).toggle(visible);
        shown += visible ? 1 : 0;
    });
    $("#lily-list-nomatch").prop("hidden", shown > 0);
}

$(document).on("input", "#lily-list-filter", applyListFilter);
