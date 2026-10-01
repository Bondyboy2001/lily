/* global $, calibre, ePub, EPUBJS, ePubReader, LilyProgress, screenfull, themes */

var reader;

(function() {
    "use strict";

    EPUBJS.filePath = calibre.filePath;
    EPUBJS.cssPath = calibre.cssPath;

    // Reading position (progress-sync.js). This device's copy is shown by the very first
    // render, so the book opens where it was left without waiting for the network or for
    // locations; epub-progress.js then asks the server whether another device got further.
    var progressSync = window.lilyProgressSync = LilyProgress.create({
        url: calibre.progressUrl,
        storageKey: calibre.progressKey || calibre.bookUrl,
        format: calibre.progressFormat,
        statusEl: document.getElementById("progress-sync-status"),
        enabled: calibre.syncProgress === true
    });
    var local = progressSync.readLocal();
    var startCfi = local && local.cfi && local.cfi.indexOf("epubcfi(") === 0 ? local.cfi : undefined;

    // restore stays off: reader.min.js would otherwise keep its own copy of the position
    // (localStorage "epubjsreader:...") and jump there as well.
    window.reader = reader = ePubReader(calibre.bookUrl, {
        restore: false,
        previousLocationCfi: startCfi,
        bookmarks: calibre.bookmark ? [calibre.bookmark] : []
    });
    reader.lilyStartCfi = startCfi;

    // epub.js can land one page early on a saved position when that page starts in the middle
    // of a paragraph (display() rounds the paragraph's column down). Step on when the target
    // lies past the page shown.
    var cfiTool = new ePub.CFI();
    // The location is reported (relocated) a frame after display() resolves; read it then.
    function settled() {
        return new Promise(function (resolve) {
            var timer = setTimeout(resolve, 400);
            reader.rendition.once("relocated", function () {
                clearTimeout(timer);
                setTimeout(resolve, 0);
            });
        });
    }
    function alignTo(cfi) {
        try {
            var location = reader.rendition.location;
            if (location && location.end && location.end.cfi && !location.atEnd &&
                    cfiTool.compare(cfi, location.end.cfi) > 0) {
                return reader.rendition.next();
            }
        } catch (e) {}
        return null;
    }
    /** Shows a saved position (used for the server copy by epub-progress.js). */
    reader.lilyShow = function (cfi) {
        return reader.rendition.display(cfi).then(settled).then(function () {
            return alignTo(cfi);
        });
    };

    // A saved position from an older copy of the file may no longer exist: open at the start.
    var startPending = !!startCfi;
    function openAtStart() {
        startPending = false;
        reader.rendition.display().catch(function (error) {
            showReaderError("Unable to display this EPUB content.", error);
        });
    }
    if (startPending && reader.displayed && typeof reader.displayed.then === "function") {
        reader.displayed.then(function () {
            startPending = false;
            return settled().then(function () {
                return alignTo(startCfi);
            });
        }, openAtStart);
    }

    function showReaderError(message, error) {
        try {
            console.error(message, error || "");
        } catch (e) {}
        var loader = document.getElementById("loader");
        if (loader) {
            loader.style.display = "none";
        }
        var viewer = document.getElementById("viewer");
        if (viewer) {
            viewer.innerHTML = "<div class=\"reader-error\">" + message + "</div>";
        }
    }

    if (reader && reader.book && typeof reader.book.on === 'function') {
        reader.book.on("openFailed", function(error) {
            showReaderError("Failed to open this EPUB. It may be corrupted or DRM-protected.", error);
        });
        reader.book.on("error", function(error) {
            showReaderError("An error occurred while loading this EPUB.", error);
        });
    }

    if (reader && reader.rendition && typeof reader.rendition.on === 'function') {
        reader.rendition.on("displayerror", function(error) {
            if (startPending) {
                openAtStart();
                return;
            }
            showReaderError("Unable to display this EPUB content.", error);
        });
        reader.rendition.on("loaderror", function(error) {
            showReaderError("Unable to load this EPUB resource.", error);
        });
    }

    Object.keys(themes).forEach(function (theme) {
        reader.rendition.themes.register(theme, themes[theme].css_path);
    });

    if (calibre.useBookmarks) {
        reader.on("reader:bookmarked", updateBookmark.bind(reader, "add"));
        reader.on("reader:unbookmarked", updateBookmark.bind(reader, "remove"));
    } else {
        $("#bookmark, #show-Bookmarks").remove();
    }

    // Enable swipe support
    // The book is rendered inside an iframe, and touch events there never bubble up to the
    // parent document, so jQuery swipe plugins bound on the page never see them. epub.js
    // re-emits the iframe's touch events on the rendition, so the swipe is detected here.
    // Multi-touch gestures (pinch-zoom) and tiny movements (taps) don't turn the page.
    var SWIPE_MIN_DISTANCE = 30;
    var touchStart = 0;
    var touchEnd = 0;
    var multiTouch = false;

    if (reader && reader.rendition) {
        reader.rendition.on('touchstart', function(event) {
            multiTouch = event.touches && event.touches.length > 1;
            touchStart = event.changedTouches[0].screenX;
        });
        reader.rendition.on('touchmove', function(event) {
            if (event.touches && event.touches.length > 1) {
                multiTouch = true;
            }
        });
        reader.rendition.on('touchend', function(event) {
            touchEnd = event.changedTouches[0].screenX;
            if (multiTouch || Math.abs(touchEnd - touchStart) < SWIPE_MIN_DISTANCE) {
                if (!event.touches || event.touches.length === 0) {
                    multiTouch = false;
                }
                return;
            }
            if (touchStart < touchEnd) {
                if(reader.book.package.metadata.direction === "rtl") {
					reader.rendition.next();
				} else {
					reader.rendition.prev();
				}
                // Swiped Right
            }
            if (touchStart > touchEnd) {
                if(reader.book.package.metadata.direction === "rtl") {
					reader.rendition.prev();
				} else {
                    reader.rendition.next();
				}
                // Swiped Left
            }
        });
    }

    // On phones the page-turn buttons are invisible tap zones over the page edges; a swipe
    // that starts on one should still turn the page the way the swipe goes.
    $("#prev, #next").each(function () {
        var startX = null;
        this.addEventListener("touchstart", function (event) {
            startX = event.touches.length === 1 ? event.touches[0].screenX : null;
        }, {passive: true});
        this.addEventListener("touchend", function (event) {
            if (startX === null) {
                return;
            }
            var distance = event.changedTouches[0].screenX - startX;
            startX = null;
            if (Math.abs(distance) < SWIPE_MIN_DISTANCE) {
                return;  // a tap: the button's own click turns the page
            }
            event.preventDefault();  // no click as well
            var rtl = reader.book.package && reader.book.package.metadata.direction === "rtl";
            if ((distance > 0) !== rtl) {
                reader.rendition.prev();
            } else {
                reader.rendition.next();
            }
        });
    });

    // Pictures never outgrow a page: an oversized one would spill across the column and sit
    // on top of the text beside it. (Line spacing and margins: epub-settings.js.)
    var PICTURE_CSS = "img { max-width: 100% !important; max-height: 96vh !important; height: auto; " +
        "object-fit: contain; break-inside: avoid; }\n" +
        "svg, video { max-width: 100% !important; max-height: 96vh !important; }\n";
    if (reader.rendition.hooks && reader.rendition.hooks.content) {
        reader.rendition.hooks.content.register(function (contents) {
            var doc = contents && contents.document;
            if (!doc || doc.getElementById("lily-reader-pictures")) {
                return;
            }
            var style = doc.createElement("style");
            style.id = "lily-reader-pictures";
            style.textContent = PICTURE_CSS;
            (doc.head || doc.documentElement).appendChild(style);
        });
    }

    // iOS Safari has no Fullscreen API for pages: the button would only throw.
    if (!window.screenfull || !screenfull.isEnabled) {
        $("#fullscreen").remove();
    }

    // The sidebar slides over the page (see lily-reader.css), so the page never reflows.
    reader.settings.sidebarReflow = false;

    function closeSidebar() {
        if (reader.sidebarOpen) {
            document.getElementById("slider").click();
        }
    }

    document.getElementById("sidebar-scrim").addEventListener("click", closeSidebar);
    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
            closeSidebar();
        }
    });
    // Picking a chapter, bookmark or search hit jumps there and gets the sidebar out of the way.
    document.getElementById("sidebar").addEventListener("click", function (event) {
        var link = event.target.closest("a");
        if (link && !link.classList.contains("toc_toggle") && link.closest("#tocView, #bookmarksView, #searchResults")) {
            setTimeout(closeSidebar, 0);
        }
    });

    // Title bar: the chapter being read, falling back to the author the vendor shows.
    var chapterTitle = document.getElementById("chapter-title");
    var authorText = null;

    function sectionPath(href) {
        return (href || "").split("#")[0].split("/").pop();
    }

    // Several chapters can share one file, so among the contents entries for the
    // current file pick the last one that starts on or before this page (a chapter
    // opening part-way down the page is the one being read).
    function chapterFor(start, end) {
        var path = sectionPath(start.href);
        var candidates = [];
        (function walk(items) {
            (items || []).forEach(function (item) {
                if (sectionPath(item.href) === path) {
                    candidates.push(item);
                }
                walk(item.subitems);
            });
        })(reader.book.navigation && reader.book.navigation.toc);
        if (candidates.length < 2) {
            return candidates.length ? candidates[0].label.trim() : null;
        }
        var contents = reader.rendition.getContents().filter(function (c) {
            return c.sectionIndex === start.index;
        })[0];
        var cfi = new ePub.CFI();
        // When the page runs into the next file, every anchor in this one is above it.
        var limit = end && end.index === start.index ? end.cfi : null;
        var best = null;
        candidates.forEach(function (item) {
            var id = item.href.split("#")[1];
            if (!id) {
                best = best || item;
                return;
            }
            var el = contents && contents.document.getElementById(id);
            if (el && (!limit || cfi.compare(contents.cfiFromNode(el), limit) <= 0)) {
                best = item;
            }
        });
        return (best || candidates[0]).label.trim();
    }

    reader.rendition.on("relocated", function (location) {
        if (!chapterTitle || !location || !location.start) {
            return;
        }
        if (authorText === null) {
            authorText = chapterTitle.textContent;
        }
        var label = null;
        try {
            label = chapterFor(location.start, location.end);
        } catch (e) { /* an odd contents entry must not break the reader */ }
        chapterTitle.textContent = label || authorText;
    });

    /**
     * @param {string} action - Add or remove bookmark
     * @param {string|int} location - Location or zero
     */
    function updateBookmark(action, location) {
        // Remove other bookmarks (there can only be one)
        if (action === "add") {
            this.settings.bookmarks.filter(function (bookmark) {
                return bookmark && bookmark !== location;
            }).map(function (bookmark) {
                this.removeBookmark(bookmark);
            }.bind(this));
        }
        
        var csrftoken = $("input[name='csrf_token']").val();

        // Save to database
        $.ajax(calibre.bookmarkUrl, {
            method: "post",
            data: { bookmark: location || "" },
            headers: { "X-CSRFToken": csrftoken }
        }).fail(function (xhr, status, error) {
            alert(error);
        });
    }
})();
