/**
 * Created by SpeedProg on 05.04.2015.
 */
/* global Bloodhound, language, Modernizr, tinymce, getPath */

/* Description: a plain text box over the HTML Calibre stores. Paragraphs show as blank
   lines; an edited description is saved back as <p> paragraphs, an untouched one keeps
   its original HTML. Fetch Metadata fires "lily:set-html" after setting new HTML. */
(function () {
    var $box = $("#comments");
    if (!$box.length) { return; }
    var originalHtml, originalText;

    function htmlToText(html) {
        html = String(html || "");
        if (!/<[a-z][\s\S]*>/i.test(html)) { return html.trim(); }
        var marked = html.replace(/\s+/g, " ")
            .replace(/<br\s*\/?>/gi, "\n")
            .replace(/<\/(p|div|li|h[1-6]|blockquote)>/gi, "\n\n");
        var text = new DOMParser().parseFromString(marked, "text/html").body.textContent || "";
        return text.replace(/[ \t]*\n[ \t]*/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
    }

    function textToHtml(text) {
        return text.split(/\n\s*\n/).map(function (para) { return para.trim(); })
            .filter(function (para) { return para.length > 0; })
            .map(function (para) {
                return "<p>" + $("<div>").text(para).html().replace(/\n/g, "<br>") + "</p>";
            }).join("");
    }

    // Grow the box to its text, no empty lines below
    function fit() {
        var box = $box[0];
        box.style.height = "auto";
        box.style.height = (box.scrollHeight + box.offsetHeight - box.clientHeight) + "px";
    }

    function load(html) {
        originalHtml = html;
        originalText = htmlToText(html);
        $box.val(originalText);
        fit();
    }

    load($box.val());
    $box.on("input", fit);
    $(window).on("resize", fit);
    $box.on("lily:set-html", function () {
        var html = $box.val();
        load(html);
        originalHtml = null; // fetched text always saves
    });
    $("#book_edit_frm").on("submit", function () {
        var text = $box.val();
        $box.val(originalHtml !== null && text === originalText ? originalHtml : textToHtml(text));
    });
})();

if ($(".tiny_editor").length) {
    tinymce.init({
        selector: ".tiny_editor",
        plugins: 'code',
        branding: false,
        menubar: "edit view format",
        language: language
    });
}

$(".datepicker").datepicker({
    format: "yyyy-mm-dd",
    language: language
}).on("change", function () {
    // Show localized date over top of the standard YYYY-MM-DD date
    var pubDate;
    var results = /(\d{4})[-\/\\](\d{1,2})[-\/\\](\d{1,2})/.exec(this.value); // YYYY-MM-DD
    if (results) {
        pubDate = new Date(results[1], parseInt(results[2], 10) - 1, results[3]) || new Date(this.value);
        $(this).next('input')
            .val(pubDate.toLocaleDateString(language.replaceAll("_","-")))
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
function prefixedSource(prefix, query, cb, source) {
    function async(retArray) {
        retArray = retArray || [];
        var matches = [];
        for (var i = 0; i < retArray.length; i++) {
            var obj = {name : prefix + retArray[i].name};
            matches.push(obj);
        }
        cb(matches);
    }
    source.search(query, cb, async);
}

function sourceSplit(query, cb, split, source) {
    var tokens = query.split(split);
    var currentSource = tokens[tokens.length - 1].trim();

    tokens.splice(tokens.length - 1, 1); // remove last element
    var prefix = "";
    var newSplit;
    if (split === "&") {
        newSplit = " " + split + " ";
    } else {
        newSplit = split + " ";
    }
    for (var i = 0; i < tokens.length; i++) {
        prefix += tokens[i].trim() + newSplit;
    }
    prefixedSource(prefix, currentSource, cb, source);
}

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

/* Author editor: one input per author over the hidden #authors field (" & "-separated,
   what the server reads). Enter adds a row below, pasting "A & B" splits into rows, and
   Fetch Metadata sets #authors and fires "change", which redraws the rows. Authors are
   kept in alphabetical order: the saved value always, the rows once focus leaves the list. */
(function () {
    var $field = $("#authors");
    var $rows = $("#author-rows");
    if (!$field.length || !$rows.length) { return; }
    var placeholder = $rows.data("placeholder") || "Author name";
    var removeLabel = $rows.data("remove-label") || "Remove author";
    // typeahead copies the input's classes onto its .tt-hint overlay; skip that copy
    var AUTHOR_INPUT = "input.lily-author-input:not(.tt-hint)";

    function split(raw) {
        return raw.split("&").map(function (a) { return a.trim(); })
            .filter(function (a) { return a.length > 0; });
    }

    function sortNames(names) {
        return names.sort(function (a, b) {
            return a.localeCompare(b, undefined, {sensitivity: "base"});
        });
    }

    function inputs() {
        return $rows.find(AUTHOR_INPUT);
    }

    function sync() {
        var names = [];
        inputs().each(function () { names = names.concat(split($(this).typeahead("val"))); });
        $field.val(sortNames(names).join(" & "));
    }

    function iconButton(cls, icon, label) {
        return $("<button>", {type: "button", "class": "icon-btn " + cls, title: label, "aria-label": label})
            .append($("<span>", {"class": "glyphicon " + icon, "aria-hidden": "true"}));
    }

    function makeRow(name) {
        var $input = $("<input>", {type: "text", "class": "form-control typeahead lily-author-input",
            autocomplete: "off", placeholder: placeholder, "aria-label": placeholder});
        var $row = $("<li>", {"class": "lily-author-row"}).append(
            $("<div>", {"class": "lily-author-field"}).append($input),
            iconButton("lily-author-remove", "glyphicon-remove", removeLabel)
        );
        $input.typeahead(
            {highlight: true, minLength: 1, hint: true},
            {name: "authors", display: "name", source: authors}
        );
        $input.typeahead("val", name || "");
        return $row;
    }

    function render() {
        $rows.empty();
        var names = sortNames(split($field.val()));
        if (!names.length) { names = [""]; }
        names.forEach(function (name) { $rows.append(makeRow(name)); });
    }

    function addAfter($row, name) {
        var $new = makeRow(name);
        if ($row && $row.length) { $row.after($new); } else { $rows.append($new); }
        $new.find(AUTHOR_INPUT).trigger("focus");
        return $new;
    }

    $("#author-add").on("click", function () { addAfter(null, ""); });

    $rows.on("input typeahead:select typeahead:autocomplete", AUTHOR_INPUT, sync);

    $rows.on("keydown", AUTHOR_INPUT, function (e) {
        if (e.key === "Enter") {
            e.preventDefault();
            addAfter($(this).closest(".lily-author-row"), "");
        } else if (e.key === "Backspace" && !$(this).val() && inputs().length > 1) {
            e.preventDefault();
            var $row = $(this).closest(".lily-author-row");
            var $prev = $row.prev().length ? $row.prev() : $row.next();
            $row.remove();
            $prev.find(AUTHOR_INPUT).trigger("focus");
            sync();
        }
    });

    // Pasting "A & B & C" into one row spreads it over several
    $rows.on("paste", AUTHOR_INPUT, function (e) {
        var text = (e.originalEvent.clipboardData || window.clipboardData).getData("text");
        var names = split(text || "");
        if (names.length < 2) { return; }
        e.preventDefault();
        var $row = $(this).closest(".lily-author-row");
        $(this).typeahead("val", names.shift());
        names.forEach(function (name) { $row = addAfter($row, name); });
        sync();
    });

    $rows.on("click", ".lily-author-remove", function () {
        var $row = $(this).closest(".lily-author-row");
        if (inputs().length > 1) {
            $row.remove();
        } else {
            $row.find(AUTHOR_INPUT).typeahead("val", "");
        }
        sync();
    });

    // Keep focus in the input when pressing ×, so the redraw below can't swallow the click
    $rows.on("mousedown", ".icon-btn", function (e) { e.preventDefault(); });

    // Re-sort the rows once focus leaves the list, not while typing
    $rows.on("focusout", function () {
        setTimeout(function () {
            if (!$.contains($rows[0], document.activeElement)) { render(); }
        }, 0);
    });

    $field.on("change", render);
    render();
})();


var series = new Bloodhound({
    name: "series",
    datumTokenizer: function datumTokenizer(datum) {
        return [datum.name];
    },
    // queryTokenizer: Bloodhound.tokenizers.whitespace,
    queryTokenizer: function queryTokenizer(query) {
        return [query];
    },
    remote: {
        url: getPath() + "/get_series_json?q=%QUERY",
        wildcard: '%QUERY',
        /*replace: function replace(url, query) {
            return url + encodeURIComponent(query);
        }*/
    }
});
$(".form-group #series").typeahead(
    {
        highlight: true,
        minLength: 0,
        hint: true
    }, {
        name: "series",
        displayKey: "name",
        source: series
    }
);

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

/* Chip editor: one chip per value over a hidden field (read/write convert between the field
   and a list). The "+ Add" button opens the entry; Enter (or a comma, with splitOnComma) adds
   the typed value and keeps it open for another, Escape or leaving it closes it again, and
   Backspace in an empty entry drops the last chip. Code that sets the field fires "change".
   opts.accept, if given, maps a typed value to the one to add, or null to refuse it. */
function lilyChipEditor(opts) {
    var $field = opts.field, $chips = opts.chips, $entry = opts.entry;
    if (!$field.length || !$chips.length) { return; }
    var removeLabel = $chips.data("remove-label") || "Remove";
    var $entryWrap = $entry.closest(".lily-chip-entry");
    var $add = $entry.closest(".lily-tag-editor").find(".lily-chip-add");

    function open() {
        $add.prop("hidden", true);
        $entryWrap.prop("hidden", false);
        $entry.trigger("focus");
    }
    function close() {
        $entryWrap.prop("hidden", true);
        $add.prop("hidden", false);
    }
    $add.on("click", open);

    function current() { return opts.read($field.val()); }
    function save(values) { $field.val(opts.write(values)); render(); }

    function render() {
        $chips.empty();
        current().forEach(function (value) {
            var $remove = $("<button>", {type: "button", "class": "icon-btn lily-tag-remove",
                title: removeLabel, "aria-label": removeLabel + " " + value})
                .append($("<span>", {"class": "glyphicon glyphicon-remove", "aria-hidden": "true"}))
                .data("value", value);
            $chips.append($("<li>", {"class": "lily-tag-chip"}).append($("<span>").text(value), $remove));
        });
    }

    function add(raw) {
        var values = current();
        (opts.splitOnComma ? raw.split(",") : [raw]).map(function (t) { return t.trim(); }).forEach(function (t) {
            if (t && opts.accept) { t = opts.accept(t); }
            if (!t) { return; }
            var exists = values.some(function (x) { return x.toLowerCase() === t.toLowerCase(); });
            if (t && !exists) { values.push(t); }
        });
        save(values);
    }

    function commitEntry() {
        var value = $entry.typeahead("val") || $entry.val();
        if (value.trim()) { add(value); }
        $entry.typeahead("val", "");
    }
    $entry.on("input", function () { if (opts.onInput) { opts.onInput(); } });

    $chips.on("click", ".lily-tag-remove", function () {
        var gone = $(this).data("value");
        save(current().filter(function (t) { return t !== gone; }));
        $entry.trigger("focus");
    });

    $entry.typeahead(
        {highlight: true, minLength: 0, hint: true},
        {name: opts.name, display: opts.display, source: opts.source}
    ).on("typeahead:select", function () {
        commitEntry();
    }).on("keydown", function (e) {
        if (e.key === "Enter" || (opts.splitOnComma && e.key === ",")) {
            e.preventDefault();
            commitEntry();
        } else if (e.key === "Escape") {
            $entry.typeahead("val", "");
            close();
            $add.trigger("focus");
        } else if (e.key === "Backspace" && !$entry.val()) {
            var values = current();
            values.pop();
            save(values);
        }
    }).on("blur", function () {
        // A value typed but not confirmed still counts when the form is saved
        if ($entry.val().trim()) { commitEntry(); }
        // Deferred: picking a suggestion blurs and refocuses the entry
        setTimeout(function () {
            if (document.activeElement !== $entry[0]) { close(); }
        }, 150);
    });

    $field.on("change", render);
    render();
}

/* Tags: the hidden #tags field is the comma-separated list the server reads. */
lilyChipEditor({
    field: $("#tags"), chips: $("#tag-chips"), entry: $("#tag-entry"),
    name: "tags", display: "name", source: tags, splitOnComma: true,
    read: function (val) {
        return val.split(",").map(function (t) { return t.trim(); })
            .filter(function (t) { return t.length > 0; });
    },
    write: function (values) { return values.join(", "); }
});

/* Shelves: the hidden #shelves field is a JSON list of names (shelf names may hold commas).
   Only the user's existing shelves can be added; new shelves come from the sidebar's
   Create Shelf, so an unknown name adds no chip. */
(function () {
    var $entry = $("#shelf-entry");
    if (!$entry.length) { return; }
    var known = $entry.data("shelves") || [];
    var shelfNames = new Bloodhound({
        datumTokenizer: Bloodhound.tokenizers.whitespace,
        queryTokenizer: Bloodhound.tokenizers.whitespace,
        local: known
    });
    lilyChipEditor({
        field: $("#shelves"), chips: $("#shelf-chips"), entry: $entry,
        name: "shelves",
        source: function (query, sync) {
            if (query) { shelfNames.search(query, sync); } else { sync(shelfNames.all()); }
        },
        read: function (val) {
            try { return JSON.parse(val || "[]"); } catch (e) { return []; }
        },
        write: function (values) { return JSON.stringify(values); },
        accept: function (value) {
            var match = known.filter(function (n) { return n.toLowerCase() === value.toLowerCase(); })[0];
            return match || null;
        }
    });
})();

var languages = new Bloodhound({
    name: "languages",
    datumTokenizer: function datumTokenizer(datum) {
        return [datum.name];
    },
    queryTokenizer: function queryTokenizer(query) {
        return [query];
    },
    remote: {
        url: getPath() + "/get_languages_json?q=%QUERY",
        wildcard: '%QUERY'
        /*replace: function replace(url, query) {
            return url + encodeURIComponent(query);
        }*/
    }
});

$(".form-group #languages").typeahead(
    {
        highlight: true, minLength: 0,
        hint: true
    }, {
        name: "languages",
        display: "name",
        source: function source(query, cb, asyncResults) {
            return sourceSplit(query, cb, ",", languages);
        }
    }
);

var publishers = new Bloodhound({
    name: "publisher",
    datumTokenizer: function datumTokenizer(datum) {
        return [datum.name];
    },
    queryTokenizer: Bloodhound.tokenizers.whitespace,
    remote: {
        url: getPath() + "/get_publishers_json?q=%QUERY",
        wildcard: '%QUERY'
    }
});

$(".form-group #publisher").typeahead(
    {
        highlight: true, minLength: 0,
        hint: true
    }, {
        name: "publishers",
        displayKey: "name",
        source: publishers
    }
);

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

$("#book_edit_frm").on("submit", function () {
    if (typeof tinymce !== "undefined" && typeof tinymce.triggerSave === "function") {
        tinymce.triggerSave();
    }
});

