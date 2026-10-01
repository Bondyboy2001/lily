/**
 * Created by SpeedProg on 05.04.2015.
 */
/* global Bloodhound, language, Modernizr, tinymce, getPath */

/* Description: a plain text box over the HTML Calibre stores. Paragraphs show as blank
   lines; an edited description is saved back as <p> paragraphs, an untouched one keeps
   its original HTML. Fetch Metadata fires "lily:set-html" after setting new HTML. */
(function () {
    var $box = $("textarea#comments");  // not the one-line Description field on Advanced Search
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
    // Refit once Literata loads (the first fit measured the fallback font) and
    // whenever the box changes width
    if (document.fonts) { document.fonts.ready.then(fit); }
    var width = $box[0].clientWidth;
    new ResizeObserver(function () {
        if ($box[0].clientWidth !== width) { width = $box[0].clientWidth; fit(); }
    }).observe($box[0]);
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

/* Row editor: one input per value over a hidden field, which is what the server reads
   (opts.read / opts.write convert between the field and a list, opts.split splits one input's
   text into values). Enter (or opts.splitKey) adds a row below, Backspace in an empty row
   removes it, and pasting a list spreads it over several rows. Code that sets the field fires
   "change", which redraws the rows (Fetch Metadata does this).
   opts.accept, if given, maps a typed value to the one to keep, or null to refuse it: refused
   rows stay on screen marked invalid and are left out of the field. Values that arrive through
   the field are always kept.
   opts.sort keeps the saved value alphabetical always, and the rows once focus leaves the list.
   opts.minRows is how many rows to show when there are no values (0 or 1). */
function lilyRowEditor(opts) {
    var $field = opts.field, $rows = opts.rows;
    if (!$field.length || !$rows.length) { return; }
    var placeholder = $rows.data("placeholder") || "";
    var removeLabel = $rows.data("remove-label") || "Remove";
    var invalidLabel = $rows.data("invalid-label") || "";
    var minRows = opts.minRows || 0;
    // typeahead copies the input's classes onto its .tt-hint overlay; skip that copy
    var INPUT = "input.lily-edit-input:not(.tt-hint)";
    var fromField = [];

    function same(a, b) { return a.toLowerCase() === b.toLowerCase(); }

    function sortValues(values) {
        if (!opts.sort) { return values; }
        return values.sort(function (a, b) {
            return a.localeCompare(b, undefined, {sensitivity: "base"});
        });
    }

    function inputs() {
        return $rows.find(INPUT);
    }

    function keep(value) {
        if (fromField.some(function (v) { return same(v, value); })) { return value; }
        return opts.accept ? opts.accept(value) : value;
    }

    // Writes the field from the rows; markInvalid also flags refused rows (not while typing)
    function sync(markInvalid) {
        var values = [];
        inputs().each(function () {
            var refused = false;
            opts.split($(this).typeahead("val")).forEach(function (raw) {
                var value = keep(raw);
                if (!value) { refused = true; return; }
                if (!values.some(function (v) { return same(v, value); })) { values.push(value); }
            });
            if (markInvalid || !refused) {
                $(this).toggleClass("is-invalid", refused)
                    .attr({"aria-invalid": refused ? "true" : null, title: refused ? invalidLabel : null});
            }
        });
        $field.val(opts.write(sortValues(values)));
    }

    function makeRow(value) {
        var $input = $("<input>", {type: "text", "class": "form-control typeahead lily-edit-input",
            autocomplete: "off", placeholder: placeholder, "aria-label": placeholder});
        var $remove = $("<button>", {type: "button", "class": "icon-btn lily-edit-remove",
            title: removeLabel, "aria-label": removeLabel})
            .append($("<span>", {"class": "glyphicon glyphicon-trash", "aria-hidden": "true"}));
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
        var values = sortValues(opts.read($field.val()));
        fromField = values.slice();
        var shown = values.length ? values : new Array(minRows).fill("");
        shown.forEach(function (value) { $rows.append(makeRow(value)); });
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
        } else if (e.key === "Backspace" && !$(this).val() && inputs().length > minRows) {
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
        var $row = $(this).closest(".lily-edit-row");
        if (inputs().length > minRows) {
            removeRow($row);
        } else {
            $row.find(INPUT).typeahead("val", "").removeClass("is-invalid").removeAttr("aria-invalid title");
            sync();
        }
    });

    // Keep focus in the input when pressing ×, so the tidy-up below can't swallow the click
    $rows.on("mousedown", ".icon-btn", function (e) { e.preventDefault(); });

    // Once focus leaves the list: drop blank rows, flag refused ones, and re-sort if sorted
    $rows.on("focusout", function () {
        setTimeout(function () {
            if ($.contains($rows[0], document.activeElement)) { return; }
            sync(true);
            if (opts.sort) {
                render();
                return;
            }
            inputs().each(function () {
                if (!$(this).typeahead("val").trim() && inputs().length > minRows) {
                    $(this).closest(".lily-edit-row").remove();
                }
            });
        }, 0);
    });

    $field.on("change", render);
    render();
}

/* Authors: the hidden #authors field is the " & "-separated list the server reads,
   kept in alphabetical order. */
lilyRowEditor({
    field: $("#authors"), rows: $("#author-rows"), add: $("#author-add"),
    name: "authors", display: "name", source: authors, minLength: 1,
    sort: true, minRows: 1,
    split: function (raw) {
        return raw.split("&").map(function (a) { return a.trim(); })
            .filter(function (a) { return a.length > 0; });
    },
    read: function (val) { return this.split(val); },
    write: function (values) { return values.join(" & "); }
});


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

/* Shelves: the hidden #shelves field is a JSON list of names (shelf names may hold commas).
   Only the user's existing shelves can be typed in; new shelves come from the sidebar's
   Create Shelf, so an unknown name is flagged and not saved. */
(function () {
    var $rows = $("#shelf-rows");
    if (!$rows.length) { return; }
    var known = $rows.data("shelves") || [];
    var shelfNames = new Bloodhound({
        datumTokenizer: Bloodhound.tokenizers.whitespace,
        queryTokenizer: Bloodhound.tokenizers.whitespace,
        local: known
    });
    lilyRowEditor({
        field: $("#shelves"), rows: $rows, add: $("#shelf-add"),
        name: "shelves",
        source: function (query, sync) {
            if (query) { shelfNames.search(query, sync); } else { sync(shelfNames.all()); }
        },
        split: function (raw) { return raw.trim() ? [raw.trim()] : []; },
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


/* Optional fields (series, publisher, date, language, rating) are hidden while empty.
   An Add button shows one; Fetch Metadata fires "lily:reveal-filled" after filling the
   form, which shows every field that now has a value. */
(function () {
    var $form = $("#book_edit_frm");
    var $add = $form.find(".editbook-add-fields");
    if (!$add.length) { return; }

    function show(key) {
        $form.find('[data-optional="' + key + '"]').prop("hidden", false);
        $add.find('[data-optional-add="' + key + '"]').prop("hidden", true);
        $add.prop("hidden", !$add.find("[data-optional-add]:not([hidden])").length);
    }

    $add.on("click", "[data-optional-add]", function () {
        var key = this.dataset.optionalAdd;
        show(key);
        $form.find('[data-optional="' + key + '"] input:visible').first().trigger("focus");
    });

    $form.on("lily:reveal-filled", function () {
        $form.find("[data-optional][hidden]").each(function () {
            var value = $.trim($(this).find("[data-optional-value]").val() || "");
            if (value !== "" && value !== "0") { show(this.dataset.optional); }
        });
    });
})();
