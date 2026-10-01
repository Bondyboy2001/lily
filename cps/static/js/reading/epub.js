/* global $, calibre, EPUBJS, ePub, ePubReader */

var reader;

(function() {
    "use strict";

    EPUBJS.filePath = calibre.filePath;
    EPUBJS.cssPath = calibre.cssPath;

    window.reader = reader = ePubReader(calibre.bookUrl, {
        restore: false,
        bookmarks: calibre.bookmark ? [calibre.bookmark] : []
    });

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

    // Line spacing and page margins. Injected as a small stylesheet into every rendered
    // section (epub.js content hook) and persisted in localStorage like font size.
    var readerLayout = {
        lineHeight: localStorage.getItem("calibre.reader.lineHeight") || "default",
        margin: localStorage.getItem("calibre.reader.margin") || "0"
    };

    function readerLayoutCss() {
        // Pictures never outgrow a page: an oversized one would spill across the
        // column and sit on top of the text beside it.
        var css = "img { max-width: 100% !important; max-height: 96vh !important; height: auto; " +
            "object-fit: contain; break-inside: avoid; }\n" +
            "svg, video { max-width: 100% !important; max-height: 96vh !important; }\n";
        var lineHeight = parseFloat(readerLayout.lineHeight);
        if (readerLayout.lineHeight !== "default" && lineHeight > 0) {
            css += "body, body p, body li, body blockquote, body div, body dd, body dt " +
                "{ line-height: " + lineHeight + " !important; }\n";
        }
        var margin = parseFloat(readerLayout.margin);
        if (margin > 0) {
            // Inline padding on block children repeats in every column, so it works when paginated
            css += "body > * { padding-left: " + margin + "em !important; padding-right: " + margin +
                "em !important; box-sizing: border-box; }\n";
        }
        return css;
    }

    function applyReaderLayout(contents) {
        var doc = contents && contents.document;
        if (!doc) {
            return;
        }
        var style = doc.getElementById("lily-reader-layout");
        if (!style) {
            style = doc.createElement("style");
            style.id = "lily-reader-layout";
            (doc.head || doc.documentElement).appendChild(style);
        }
        style.textContent = readerLayoutCss();
    }

    function setReaderLayout(key, storageKey, value) {
        readerLayout[key] = value;
        localStorage.setItem(storageKey, value);
        if (reader && reader.rendition && typeof reader.rendition.getContents === "function") {
            reader.rendition.getContents().forEach(applyReaderLayout);
        }
    }

    if (reader && reader.rendition && reader.rendition.hooks && reader.rendition.hooks.content) {
        reader.rendition.hooks.content.register(applyReaderLayout);
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
    
    // Restore all settings after DOM and reader are ready
    document.addEventListener("DOMContentLoaded", function() {
        // Theme
        const theme = localStorage.getItem("calibre.reader.theme") ?? "lightTheme";
        if (typeof selectTheme === 'function') selectTheme(theme);

        // Font size (150% until the reader picks one)
        let savedFontSize = localStorage.getItem("calibre.reader.fontSize") || "150";
        let fontSizeFader = document.getElementById('fontSizeFader');
        if (savedFontSize && fontSizeFader && reader && reader.rendition && reader.rendition.themes) {
            fontSizeFader.value = savedFontSize;
            reader.rendition.themes.fontSize(`${savedFontSize}%`);
        }

        // Font (selectFont maps the saved id to its stack in window.readerFontStacks)
        let savedFont = localStorage.getItem("calibre.reader.font");
        if (savedFont && typeof selectFont === 'function') {
            selectFont(savedFont);
        }

        // Line spacing and margins
        var lineHeightSelect = document.getElementById("lineHeightSelect");
        if (lineHeightSelect) {
            lineHeightSelect.value = readerLayout.lineHeight;
            if (lineHeightSelect.value !== readerLayout.lineHeight) {
                lineHeightSelect.value = "default";
            }
            lineHeightSelect.addEventListener("change", function() {
                setReaderLayout("lineHeight", "calibre.reader.lineHeight", this.value);
            });
        }
        var marginSelect = document.getElementById("marginSelect");
        if (marginSelect) {
            marginSelect.value = readerLayout.margin;
            if (marginSelect.value !== readerLayout.margin) {
                marginSelect.value = "0";
            }
            marginSelect.addEventListener("change", function() {
                setReaderLayout("margin", "calibre.reader.margin", this.value);
            });
        }

        // Spread
        let savedSpread = localStorage.getItem("calibre.reader.spread");
        if (savedSpread && typeof spread === 'function') {
            spread(savedSpread);
        }
    });
})();
