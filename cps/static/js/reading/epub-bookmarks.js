/* global reader, calibre, ePub, LilyBookmarks */

/* Bookmarks for the epub reader: any number per book.
 * The title bar's bookmark button marks the page being read (its first CFI) or, when the
 * page already holds a bookmark, removes it; the button is pressed on a bookmarked page.
 * The sidebar's Bookmarks tab lists them in book order with the chapter and the first
 * words of the page, to jump to or remove.
 * reader.min.js has a one-bookmark version of all this; its button handler is stopped
 * before it runs and its own list stays empty (the reader starts with no bookmarks). */
(function () {
    "use strict";

    var button = document.getElementById("bookmark");
    var tab = document.getElementById("show-Bookmarks");
    var list = document.getElementById("bookmarks");
    var empty = document.getElementById("bookmarks-empty");
    if (!window.reader || !reader.rendition || !button || !list) {
        return;
    }
    if (!calibre.useBookmarks || !calibre.bookmarksUrl) {
        button.remove();
        if (tab) {
            tab.remove();
        }
        return;
    }

    var EXCERPT_LENGTH = 90;
    var cfi = new ePub.CFI();
    var here = null;    // the page on screen, from "relocated"
    var busy = false;

    var store = LilyBookmarks.create({
        url: calibre.bookmarksUrl,
        noticeEl: document.getElementById("bookmark-status"),
        onChange: draw
    });

    function compare(a, b) {
        try {
            return cfi.compare(a, b);
        } catch (e) {
            return a < b ? -1 : (a > b ? 1 : 0);
        }
    }

    // Bookmarks that fall on the page on screen.
    function onPage(loc) {
        if (!loc || !loc.start || !loc.end) {
            return [];
        }
        return store.items().filter(function (item) {
            return compare(item.key, loc.start.cfi) >= 0 && compare(item.key, loc.end.cfi) <= 0;
        });
    }

    function update() {
        var marked = onPage(here).length > 0;
        var label = button.getAttribute(marked ? "data-remove-label" : "data-add-label");
        button.setAttribute("aria-pressed", marked ? "true" : "false");
        if (label) {
            button.setAttribute("aria-label", label);
            button.title = label;
        }
    }

    function shorten(text) {
        text = text.replace(/\s+/g, " ").trim();
        if (text.length <= EXCERPT_LENGTH) {
            return text;
        }
        var cut = text.slice(0, EXCERPT_LENGTH);
        var space = cut.lastIndexOf(" ");
        return (space > EXCERPT_LENGTH / 2 ? cut.slice(0, space) : cut).replace(/[\s.,;:]+$/, "") + "…";
    }

    // The first words on the page: the text between the page's first and last CFI, less
    // the chapter heading when the page opens with it.
    function pageExcerpt(loc, chapter) {
        try {
            var start = reader.rendition.getRange(loc.start.cfi);
            if (!start) {
                return "";
            }
            var doc = start.startContainer.ownerDocument;
            var range = doc.createRange();
            range.setStart(start.startContainer, start.startOffset);
            var end = loc.end && loc.end.index === loc.start.index ? reader.rendition.getRange(loc.end.cfi) : null;
            if (end && end.endContainer.ownerDocument === doc) {
                range.setEnd(end.endContainer, end.endOffset);
            } else {
                range.setEnd(start.startContainer, start.startContainer.length || start.startContainer.childNodes.length);
            }
            var text = range.toString().replace(/\s+/g, " ").trim();
            var heading = (chapter || "").replace(/\s+/g, " ").trim();
            if (heading && text.toLowerCase().indexOf(heading.toLowerCase()) === 0) {
                text = text.slice(heading.length);
            }
            return shorten(text);
        } catch (e) {
            return "";
        }
    }

    // The chapter for a bookmark saved without one (from when a book had a single bookmark).
    function chapterOf(key) {
        try {
            var section = reader.book.spine.get(key);
            var nav = section && reader.book.navigation && reader.book.navigation.get(section.href);
            return nav && nav.label ? nav.label.trim() : "";
        } catch (e) {
            return "";
        }
    }

    function draw(items) {
        items.sort(function (a, b) { return compare(a.key, b.key); });
        list.textContent = "";
        items.forEach(function (item) {
            var chapter = item.label || chapterOf(item.key) || list.getAttribute("data-untitled");
            list.appendChild(LilyBookmarks.row({
                rowClass: "reader-bookmark",
                lines: [{className: "reader-bookmark-chapter", text: chapter},
                        {className: "reader-bookmark-excerpt", text: item.excerpt}],
                removeLabel: LilyBookmarks.fill(list.getAttribute("data-remove"), {name: chapter}),
                removeTitle: list.getAttribute("data-remove-title"),
                onJump: function () { reader.rendition.display(item.key); },
                onRemove: function () { return store.remove(item); }
            }));
        });
        if (empty) {
            empty.hidden = items.length > 0;
        }
        update();
    }

    function toggle() {
        var loc = here || reader.rendition.currentLocation();
        if (busy || !loc || !loc.start) {
            return;
        }
        busy = true;
        var marked = onPage(loc);
        var done;
        if (marked.length) {
            done = Promise.all(marked.map(function (item) { return store.remove(item); }));
        } else {
            var chapter = "";
            try {
                chapter = (reader.lilyChapterFor && reader.lilyChapterFor(loc.start, loc.end)) || "";
            } catch (e) { /* an odd contents entry leaves the label to chapterOf() */ }
            chapter = chapter || chapterOf(loc.start.cfi);
            done = store.add(loc.start.cfi, chapter, pageExcerpt(loc, chapter));
        }
        done.then(function () {
            busy = false;
            update();
        });
    }

    // Runs before the vendor's own handler on the button, and stops it.
    document.addEventListener("click", function (event) {
        if (event.target.closest && event.target.closest("#bookmark")) {
            event.stopPropagation();
            event.preventDefault();
            toggle();
        }
    }, true);

    reader.rendition.on("relocated", function (loc) {
        here = loc;
        update();
    });

    store.load();
})();
