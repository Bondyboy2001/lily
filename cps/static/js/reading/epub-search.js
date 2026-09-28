/* global reader */

/* In-book search for the epub reader.
 * reader.min.js has no search panel of its own, but its sidebar switches to any
 * "<View>Controller" found on the reader, so registering reader.SearchController
 * makes the "Search" tab work. Each spine section is loaded, searched with epub.js'
 * Section.find() and unloaded again, one at a time to keep memory flat. */
(function () {
    "use strict";

    var MAX_RESULTS = 200;
    var view = document.getElementById("searchView");
    var form = document.getElementById("searchForm");
    var box = document.getElementById("searchBox");
    var list = document.getElementById("searchResults");
    var status = document.getElementById("searchStatus");
    if (!view || !form || !box || !list || !window.reader || !reader.book) {
        return;
    }

    var searchRun = 0;
    var highlighted = null;

    reader.SearchController = {
        show: function () {
            view.style.display = "block";
            setTimeout(function () { box.focus(); }, 50);
        },
        hide: function () {
            view.style.display = "none";
        }
    };

    function text(key, count) {
        var value = view.getAttribute("data-" + key) || "";
        return value.replace("%(count)s", count);
    }

    function chapterLabel(book, item) {
        try {
            var nav = book.navigation && book.navigation.get(item.href);
            if (nav && nav.label) {
                return nav.label.trim();
            }
        } catch (e) {}
        return "";
    }

    function excerptNode(excerpt, query) {
        var p = document.createElement("span");
        p.className = "reader-search-excerpt";
        var lower = excerpt.toLowerCase();
        var at = lower.indexOf(query.toLowerCase());
        if (at === -1) {
            p.textContent = excerpt;
            return p;
        }
        p.appendChild(document.createTextNode(excerpt.slice(0, at)));
        var mark = document.createElement("mark");
        mark.textContent = excerpt.slice(at, at + query.length);
        p.appendChild(mark);
        p.appendChild(document.createTextNode(excerpt.slice(at + query.length)));
        return p;
    }

    function addResult(match, label, query) {
        var li = document.createElement("li");
        li.className = "list_item";
        var link = document.createElement("a");
        link.href = "#";
        if (label) {
            var chapter = document.createElement("span");
            chapter.className = "reader-search-chapter";
            chapter.textContent = label;
            link.appendChild(chapter);
        }
        link.appendChild(excerptNode(match.excerpt || "", query));
        link.addEventListener("click", function (event) {
            event.preventDefault();
            var rendition = reader.rendition;
            rendition.display(match.cfi).then(function () {
                try {
                    if (highlighted) {
                        rendition.annotations.remove(highlighted, "highlight");
                    }
                    rendition.annotations.highlight(match.cfi, {}, null, "lily-search-hit");
                    highlighted = match.cfi;
                } catch (e) {}
            });
        });
        li.appendChild(link);
        list.appendChild(li);
    }

    function search(query) {
        var run = ++searchRun;
        list.innerHTML = "";
        if (query.length < 2) {
            status.textContent = text("short", 0);
            return;
        }
        status.textContent = text("searching", 0);
        var book = reader.book;
        var found = 0;
        book.ready.then(function () {
            var items = (book.spine && book.spine.spineItems) || [];
            return items.reduce(function (previous, item) {
                return previous.then(function () {
                    if (run !== searchRun || found >= MAX_RESULTS) {
                        return null;
                    }
                    return item.load(book.load.bind(book)).then(function () {
                        var matches = item.find(query) || [];
                        var label = chapterLabel(book, item);
                        item.unload();
                        if (run !== searchRun) {
                            return;
                        }
                        matches.forEach(function (match) {
                            if (found < MAX_RESULTS) {
                                found += 1;
                                addResult(match, label, query);
                            }
                        });
                        status.textContent = text("searching", found);
                    }).catch(function () {});
                });
            }, Promise.resolve());
        }).then(function () {
            if (run === searchRun) {
                status.textContent = found ? text("results", found) : text("none", 0);
            }
        });
    }

    form.addEventListener("submit", function (event) {
        event.preventDefault();
        search(box.value.trim());
    });
    // Arrow keys in the field must not turn pages.
    box.addEventListener("keydown", function (event) {
        event.stopPropagation();
    });
})();
