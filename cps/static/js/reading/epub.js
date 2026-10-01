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

    // Narrow screens: the contents panel covers most of the page, so close it once a chapter,
    // bookmark or search result has been picked.
    var narrowScreen = window.matchMedia("(max-width: 799px)");
    $("#sidebar").on("click", ".toc_link, #bookmarks a, #searchResults a", function () {
        if (narrowScreen.matches && reader.sidebarOpen) {
            $("#slider").trigger("click");
        }
    });

    // iOS Safari has no Fullscreen API for pages: the button would only throw.
    if (!window.screenfull || !screenfull.isEnabled) {
        $("#fullscreen").remove();
    }

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
