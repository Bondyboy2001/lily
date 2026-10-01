/* Calibre-Web Automated – fork of Calibre-Web
Copyright (C) 2018-2025 Calibre-Web contributors
Copyright (C) 2024-2025 Calibre-Web Automated contributors
SPDX-License-Identifier: GPL-3.0-or-later
See CONTRIBUTORS for full list of authors.
 */


// Helper: get the primary list container regardless of template id
function getListContainer() {
    var $l = $("#list");
    if ($l.length) return $l;
    // Prefer the container right after the filter header
    var $near = $(".filterheader").nextAll(".container").first().find("div[id$='_list']").first();
    if ($near.length) return $near;
    $l = $("div[id$='_list']");
    if ($l.length) return $l.first();
    $l = $(".col-xs-12.col-sm-6").not("#second").first();
    return $l;
}

// Delegate events to handle dynamically rendered elements
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
    var index = 0;
    var list = getListContainer();
    var second = $("#second");
    list.append(second.contents());
    var listItems = list.children(".row");
    var reversed = listItems.get().reverse();
    var elementLength = reversed.length;

    // Find count of middle element
    var count = list.find("> .row:visible").length;
    if (count > 20) {
        var middle = parseInt(count / 2, 10) + (count % 2);
        $(reversed).each(function() {
            index++;
            if ($(this).css("display") !== "none") {
                middle--;
                if (middle <= 0) {
                    return false;
                }
            }
        });
        list.append(reversed.slice(0, index));
        second.append(reversed.slice(index, elementLength));
    } else {
        list.append(reversed.slice(0, elementLength));
    }
});

$(document).on("click", "#all", function(e) {
    e.preventDefault();
    if (!lilyPickOption(this)) {
        return;
    }
    var cnt = $("#second").contents();
    var list = getListContainer();
    list.append(cnt);
    // Find count of middle element
    var listItems = list.children(".row");
    var listlength = listItems.length;
    var middle = parseInt(listlength / 2, 10) + (listlength % 2);
    // go through all elements and make them visible
    listItems.each(function() {
        $(this).show();
    });
    // Move second half of all elements
    if (listlength > 20) {
        $("#second").append(listItems.slice(middle, listlength));
    }
});

$(document).on("click", ".char", function(e) {
    e.preventDefault();
    if (!lilyPickOption(this)) {
        return;
    }
    var character = this.innerText;
    var count = 0;
    var index = 0;
    var list = getListContainer();
    // Append 2nd half of list to first half for easier processing
    var cnt = $("#second").contents();
    list.append(cnt);
    // Count no of elements
    var listItems = list.children(".row");
    var listlength = listItems.length;
    // check for each element if its Starting character matches
    listItems.each(function() {
        if (this.attributes["data-id"].value.charAt(0).toUpperCase() !== character) {
            $(this).hide();
        } else {
            $(this).show();
            count++;
        }
    });
    if (count > 20) {
        // Find count of middle element
        var middle = parseInt(count / 2, 10) + (count % 2);
        // search for the middle of all visible elements
        listItems.each(function() {
            index++;
            if ($(this).css("display") !== "none") {
                middle--;
                if (middle <= 0) {
                    return false;
                }
            }
        });
        // Move second half of visible elements
        $("#second").append(listItems.slice(index, listlength));
    }
});
