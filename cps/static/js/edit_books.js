/**
 * Created by SpeedProg on 05.04.2015.
 */
/* global Bloodhound, language, getPath */

$(".datepicker").datepicker({
    format: "yyyy-mm-dd",
    language: language
}).on("change", function () {
    // Show localized date over top of the standard YYYY-MM-DD date
    var pubDate;
    var results = /(\d{4})[-\/\\](\d{1,2})[-\/\\](\d{1,2})/.exec(this.value); // YYYY-MM-DD
    if (results) {
        pubDate = new Date(results[1], parseInt(results[2], 10) - 1, results[3]) || new Date(this.value);
        // The month in words ("2 Oct 1993" / "Oct 2, 1993", in the browser's own order), never
        // 10/2/1993, which reads two ways. 1 January is how a year alone is stored: just the year.
        var yearOnly = pubDate.getMonth() === 0 && pubDate.getDate() === 1;
        $(this).next('input')
            .val(yearOnly ? String(pubDate.getFullYear())
                          : pubDate.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }))
            .removeClass("hidden");
    }
}).trigger("change");

$(".datepicker_delete").click(function() {
    var inputs = $(this).parent().siblings('input');
    $(inputs[0]).data('datepicker').clearDates();
    $(inputs[1]).addClass('hidden');
});


/*
Takes a prefix, query typeahead callback, Bloodhound typeahead adapter
 and returns the completions it gets from the bloodhound engine prefixed.
 */
var authors = new Bloodhound({
    name: "authors",
    identify: function(obj) { return obj.name; },
    datumTokenizer: function datumTokenizer(datum) {
        return [datum.name];
    },
    queryTokenizer: Bloodhound.tokenizers.whitespace,
    remote: {
        url: getPath() + "/get_authors_json?q=%QUERY",
        wildcard: '%QUERY',
    },
});

/* Row editor: one input per value over a hidden field, which is what the server reads
   (opts.read / opts.write convert between the field and a list, opts.split splits one input's
   text into values). Enter (or opts.splitKey) adds a row below, Backspace in an empty row
   removes it, and pasting a list spreads it over several rows. Code that sets the field fires
   "change", which redraws the rows (Fetch Metadata does this). */
function lilyRowEditor(opts) {
    var $field = opts.field, $rows = opts.rows;
    if (!$field.length || !$rows.length) { return; }
    var placeholder = $rows.data("placeholder") || "";
    var removeLabel = $rows.data("remove-label") || "Remove";
    // typeahead copies the input's classes onto its .tt-hint overlay; skip that copy
    var INPUT = "input.lily-edit-input:not(.tt-hint)";

    function same(a, b) { return a.toLowerCase() === b.toLowerCase(); }

    function inputs() {
        return $rows.find(INPUT);
    }

    // Writes the field from the rows
    function sync() {
        var values = [];
        inputs().each(function () {
            opts.split($(this).typeahead("val")).forEach(function (value) {
                if (!values.some(function (v) { return same(v, value); })) { values.push(value); }
            });
        });
        $field.val(opts.write(values));
    }

    function makeRow(value) {
        var $input = $("<input>", {type: "text", "class": "form-control typeahead lily-edit-input",
            autocomplete: "off", placeholder: placeholder, "aria-label": placeholder});
        var $remove = $("<button>", {type: "button", "class": "icon-btn lily-edit-remove",
            title: removeLabel, "aria-label": removeLabel})
            .append($("<span>", {"class": "glyphicon glyphicon-remove", "aria-hidden": "true"}));
        var $row = $("<li>", {"class": "lily-edit-row"}).append(
            $("<div>", {"class": "lily-edit-field"}).append($input), $remove);
        $input.typeahead(
            {highlight: true, minLength: opts.minLength || 0, hint: true},
            {name: opts.name, display: opts.display, source: opts.source}
        );
        $input.typeahead("val", value || "");
        return $row;
    }

    function render() {
        $rows.empty();
        opts.read($field.val()).forEach(function (value) { $rows.append(makeRow(value)); });
    }

    function addAfter($row, value) {
        var $new = makeRow(value);
        if ($row && $row.length) { $row.after($new); } else { $rows.append($new); }
        $new.find(INPUT).trigger("focus");
        return $new;
    }

    function removeRow($row) {
        var $next = $row.prev().length ? $row.prev() : $row.next();
        $row.remove();
        if ($next.length) {
            $next.find(INPUT).trigger("focus");
        } else {
            opts.add.trigger("focus");
        }
        sync();
    }

    opts.add.on("click", function () { addAfter(null, ""); });

    $rows.on("input typeahead:select typeahead:autocomplete", INPUT, function () { sync(); });

    $rows.on("keydown", INPUT, function (e) {
        if (e.key === "Enter" || (opts.splitKey && e.key === opts.splitKey)) {
            e.preventDefault();
            addAfter($(this).closest(".lily-edit-row"), "");
        } else if (e.key === "Backspace" && !$(this).val()) {
            e.preventDefault();
            removeRow($(this).closest(".lily-edit-row"));
        }
    });

    // Pasting a list ("A & B", "a, b") into one row spreads it over several
    $rows.on("paste", INPUT, function (e) {
        var text = (e.originalEvent.clipboardData || window.clipboardData).getData("text");
        var values = opts.split(text || "");
        if (values.length < 2) { return; }
        e.preventDefault();
        var $row = $(this).closest(".lily-edit-row");
        $(this).typeahead("val", values.shift());
        values.forEach(function (value) { $row = addAfter($row, value); });
        sync();
    });

    $rows.on("click", ".lily-edit-remove", function () {
        removeRow($(this).closest(".lily-edit-row"));
    });

    // Keep focus in the input when pressing ×, so the tidy-up below can't swallow the click
    $rows.on("mousedown", ".icon-btn", function (e) { e.preventDefault(); });

    // Once focus leaves the list: drop blank rows
    $rows.on("focusout", function () {
        setTimeout(function () {
            if ($.contains($rows[0], document.activeElement)) { return; }
            sync();
            inputs().each(function () {
                if (!$(this).typeahead("val").trim()) {
                    $(this).closest(".lily-edit-row").remove();
                }
            });
        }, 0);
    });

    $field.on("change", render);
    render();
}

/* Authors: the hidden #authors field is the " & "-separated list the server reads, in the
   book's own order: the first is the book's author (its folder, "Bond et al."), so the rows
   are never sorted. */
lilyRowEditor({
    field: $("#authors"), rows: $("#author-rows"), add: $("#author-add"),
    name: "authors", display: "name", source: authors, minLength: 1,
    split: function (raw) {
        return raw.split("&").map(function (a) { return a.trim(); })
            .filter(function (a) { return a.length > 0; });
    },
    read: function (val) { return this.split(val); },
    write: function (values) { return values.join(" & "); }
});


var tags = new Bloodhound({
    name: "tags",
    datumTokenizer: function datumTokenizer(datum) {
        return [datum.name];
    },
    queryTokenizer: function queryTokenizer(query) {
        var tokens = query.split(",");
        tokens = [tokens[tokens.length - 1].trim()];
        return tokens;
    },
    remote: {
        url: getPath() + "/get_tags_json?q=%QUERY",
        wildcard: '%QUERY'
    }
});

/* Tags: the hidden #tags field is the comma-separated list the server reads. */
lilyRowEditor({
    field: $("#tags"), rows: $("#tag-rows"), add: $("#tag-add"),
    name: "tags", display: "name", source: tags, splitKey: ",",
    split: function (raw) {
        return raw.split(",").map(function (t) { return t.trim(); })
            .filter(function (t) { return t.length > 0; });
    },
    read: function (val) { return this.split(val); },
    write: function (values) { return values.join(", "); }
});

$("#search").on("change input.typeahead:selected", function(event) {
    if (event.target.type === "search" && event.target.tagName === "INPUT") {
        return;
    }
    var form = $("form").serialize();
    $.getJSON( getPath() + "/get_matching_tags", form, function( data ) {
        $(".tags_click").each(function() {
            if ($.inArray(parseInt($(this).val(), 10), data.tags) === -1) {
                if (!$(this).prop("selected")) {
                    $(this).prop("disabled", true);
                }
            } else {
                $(this).prop("disabled", false);
            }
        });
        $("#include_tag option:selected").each(function () {
            $("#exclude_tag").find("[value=" + $(this).val() + "]").prop("disabled", true);
        });
        $("#include_tag").selectpicker("refresh");
        $("#exclude_tag").selectpicker("refresh");
    });
});

$("#btn-upload-cover").on("change", function () {
    var filename = $(this).val();
    if (filename.substring(3, 11) === "fakepath") {
        filename = filename.substring(12);
    } // Remove c:\fake at beginning from localhost chrome
    $("#upload-cover").text(filename);
});


/* "Add edition" and "Add volume" show their field in its place, ready to type in. Fetch Metadata
   fires "lily:show-number" when it fills one, which shows that field the same way. */
function lilyShowNumber(field) {
    $("#" + field + "-field").prop("hidden", false);
    $("#" + field + "-add").prop("hidden", true);
    $(".editbook-number-adds").prop("hidden", !$(".editbook-number-adds button:not([hidden])").length);
}
$("#edition-add, #volume-add").on("click", function () {
    var field = this.id.replace("-add", "");
    lilyShowNumber(field);
    $("#" + field).trigger("focus");
});
$("#book_edit_frm").on("lily:show-number", function (e, field) { lilyShowNumber(field); });

/* Leaving with unsaved edits asks first (the browser's own "Leave site?" prompt). Only what
   the user types or picks counts: set-up code fills fields too. Saving, and Fetch Metadata's
   Apply, which saves by itself, leave freely. */
(function () {
    var $form = $("#book_edit_frm");
    if (!$form.length) { return; }
    var dirty = false;
    $form.on("input change", function (e) {
        if (e.originalEvent && e.originalEvent.isTrusted) { dirty = true; }
    });
    $form.on("submit", function () { dirty = false; });
    window.addEventListener("beforeunload", function (e) {
        if (!dirty) { return; }
        e.preventDefault();
        e.returnValue = "";
    });
})();

/* Clear metadata: after the dialog's confirm, every fetched field is emptied and the form
   saved, so the book keeps only its title, authors and shelves. The save treats an empty
   field as "remove it", the same as clearing it by hand. */
$("#clear_metadata_confirm").on("click", function () {
    var $form = $("#book_edit_frm");
    $("#edition, #volume").val("");
    /* Every datepicker (the published date and the custom columns) has a clear button that
       also hides its friendly-date overlay */
    $form.find(".datepicker_delete").trigger("click");
    /* Tags post from the hidden field the row editor reads; change redraws the rows empty.
       Authors use the same widget and stay. */
    $("#tags").val("").trigger("change");
    /* Identifiers post one row each: no rows left, nothing to keep */
    $("#identifier-table tbody tr").remove();
    /* Custom columns: selects back to their empty first option */
    $form.find("select[name^='custom_column_']").prop("selectedIndex", 0);
    $form.find("input[name^='custom_column_'], textarea[name^='custom_column_']").val("");
    $form.trigger("submit");
});
