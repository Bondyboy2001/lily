/* This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
 *    Copyright (C) 2021 Ozzieisaacs
 *
 *  This program is free software: you can redistribute it and/or modify
 *  it under the terms of the GNU General Public License as published by
 *  the Free Software Foundation, either version 3 of the License, or
 *  (at your option) any later version.
 *
 *  This program is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 *  GNU General Public License for more details.
 *
 *  You should have received a copy of the GNU General Public License
 *  along with this program. If not, see <http://www.gnu.org/licenses/>.
 */

/* Lily controls for the djvu-html5 viewer (readdjvu.html).
 *
 * The viewer draws its own toolbar; lily-reader.css hides it and the title bar's page box,
 * zoom and arrows drive its buttons and selects instead. The viewer sets those controls as
 * properties without firing events, so sync() reads them back on a short timer.
 * The position is saved as "page:N" through LilyProgress, like the pdf reader, and
 * bookmarks are whole pages kept through LilyBookmarks (bookmarks.js).
 */

// Read by the viewer when it starts. The canvas paints its own backdrop, so it takes the
// page's --sunk token (light or dark) instead of a fixed grey.
var DJVU_CONTEXT = {
    get background() {
        var sunk = window.getComputedStyle(document.documentElement).getPropertyValue("--sunk").trim();
        return sunk || null;    // null keeps the viewer's own default
    },
    // The viewer's own toolbar is hidden, so it never needs to slide away.
    uiHideDelay: 0,
    pageMargin: 12
};

(function () {
    "use strict";

    var SYNC_EVERY = 250;
    // Phones (§4.5 small phone) open at page width, wider screens at a whole page.
    var PHONE = window.matchMedia("(max-width: 600px)");

    function $(id) { return document.getElementById(id); }

    function start() {
        var container = $("djvuContainer");
        if (!container) {
            return;
        }
        var ui = {
            prev: $("djvu-prev"), next: $("djvu-next"),
            sidePrev: $("prev"), sideNext: $("next"),
            page: $("djvu-page"), count: $("djvu-page-count"),
            zoom: $("djvu-zoom"), zoomIn: $("djvu-zoom-in"), zoomOut: $("djvu-zoom-out"),
            fullscreen: $("fullscreen"), status: $("djvu-status")
        };
        var viewer = null;      // the viewer's own controls, once its toolbar exists
        var pages = 0;
        var page = 0;
        var restored = false;
        var failed = false;
        var progress = null;
        var bookmarks = null;   // LilyBookmarks.paged(), once the pages are known
        var zoomList = "";      // the viewer's zoom options last copied into ui.zoom
        // One trackpad swipe turns at most one page (see the wheel listener).
        var swipe = {page: 0, at: 0};

        if (container.getAttribute("data-progress-key")) {
            progress = LilyProgress.create({
                url: container.getAttribute("data-progress-url"),
                storageKey: container.getAttribute("data-progress-key"),
                format: container.getAttribute("data-progress-format"),
                statusEl: $("progress-sync-status"),
                enabled: container.getAttribute("data-sync") === "true"
            });
        }

        function findViewer() {
            var selects = container.querySelectorAll(".toolbar select.comboBoxSelection");
            var texts = container.querySelectorAll(".toolbar .comboBoxText");
            if (selects.length < 2 || texts.length < 2) {
                return null;
            }
            return {
                zoomSelect: selects[0], zoomText: texts[0],
                pageSelect: selects[1],
                zoomIn: container.querySelector(".toolbar .buttonZoomIn"),
                zoomOut: container.querySelector(".toolbar .buttonZoomOut"),
                prev: container.querySelector(".toolbar .buttonPagePrev"),
                next: container.querySelector(".toolbar .buttonPageNext"),
                status: container.querySelector(".statusImage")
            };
        }

        function choose(select, value) {
            select.value = value;
            select.dispatchEvent(new Event("change"));
        }

        function goTo(number) {
            if (viewer && number >= 1 && number <= pages && number !== page) {
                choose(viewer.pageSelect, String(number));
                sync();
            }
        }

        function step(delta) {
            var button = delta < 0 ? viewer && viewer.prev : viewer && viewer.next;
            if (button && !button.disabled) {
                button.click();
                sync();
            }
        }

        // The zoom list matches the viewer's, with its "Fit width"/"Fit page" in the page's language.
        // The viewer rebuilds its list once the first page's resolution is known, so this
        // runs again whenever the list changes.
        function fillZoom() {
            var values = Array.prototype.map.call(viewer.zoomSelect.options, function (option) {
                return option.value;
            }).join("|");
            if (values === zoomList) {
                return;
            }
            zoomList = values;
            var labels = {"Fit width": ui.zoom.getAttribute("data-fit-width"),
                          "Fit page": ui.zoom.getAttribute("data-fit-page")};
            ui.zoom.textContent = "";
            Array.prototype.forEach.call(viewer.zoomSelect.options, function (option) {
                ui.zoom.add(new Option(labels[option.value] || option.text, option.value));
            });
            // Shown when ctrl +/- lands between the listed steps.
            var custom = new Option("", "custom");
            custom.hidden = true;
            custom.disabled = true;
            ui.zoom.add(custom);
        }

        function showZoom(value) {
            if (document.activeElement === ui.zoom) {
                return;
            }
            var match = Array.prototype.some.call(ui.zoom.options, function (option) {
                return option.value === value && option.value !== "custom";
            });
            if (match) {
                ui.zoom.value = value;
            } else {
                var custom = ui.zoom.options[ui.zoom.options.length - 1];
                custom.text = value;
                ui.zoom.value = "custom";
            }
        }

        // The viewer keeps its hourglass up for ever when the file is missing or isn't a
        // DjVu, so the first bytes are checked here: every DjVu file starts "AT&TFORM".
        function checkFile() {
            if (!window.fetch) {
                return;
            }
            window.fetch(container.getAttribute("file"), {
                credentials: "same-origin",
                headers: {"Range": "bytes=0-15"}
            }).then(function (response) {
                if (!response.ok) {
                    return false;
                }
                var reader = response.body && response.body.getReader();
                if (!reader) {
                    return true;
                }
                return reader.read().then(function (chunk) {
                    reader.cancel().catch(function () {});
                    var bytes = chunk.value || new Uint8Array(0);
                    return String.fromCharCode.apply(null, bytes.subarray(0, 8)) === "AT&TFORM";
                });
            }).catch(function () {
                return true;    // offline or similar: leave it to the viewer
            }).then(function (ok) {
                failed = !ok;
                sync();
            });
        }

        function showStatus() {
            var image = viewer ? viewer.status : container.querySelector(".statusImage");
            var state = "";
            if (failed && !pages) {
                state = "error";
            } else if (image && image.style.display !== "none") {
                // The viewer's status sprite: hourglass at 0px, error face further along.
                state = /^0(px)?\b/.test(image.style.backgroundPosition || "0px") ? "loading" : "error";
            }
            if (ui.status.getAttribute("data-state") !== state) {
                ui.status.setAttribute("data-state", state);
                ui.status.textContent = state ? ui.status.getAttribute("data-" + state + "-text") : "";
                ui.status.classList.toggle("reader-error", state === "error");
            }
        }

        function setDisabled(control, disabled) {
            if (control.disabled !== disabled) {
                control.disabled = disabled;
            }
        }

        function sync() {
            viewer = viewer || findViewer();
            showStatus();
            if (!viewer) {
                return;
            }
            var count = viewer.pageSelect.options.length;
            if (!count) {
                return;
            }
            if (count !== pages) {
                pages = count;
                ui.count.textContent = ui.count.getAttribute("data-template").replace("%(count)s", pages);
                ui.page.style.width = (String(pages).length + 2) + "ch";
                [ui.page, ui.zoom].forEach(function (control) { setDisabled(control, false); });
                // The page box and zoom stay hidden until there is a document to drive.
                Array.prototype.forEach.call(document.querySelectorAll(".djvu-controls[hidden]"),
                                             function (group) { group.hidden = false; });
                ready();
            }
            fillZoom();
            var current = parseInt(viewer.pageSelect.value, 10) || 1;
            if (current !== page) {
                page = current;
                if (restored && progress) {
                    progress.save("page:" + page, page / pages);
                }
                if (bookmarks) {
                    bookmarks.setPage(page);
                }
            }
            if (document.activeElement !== ui.page && ui.page.value !== String(page)) {
                ui.page.value = page;
            }
            [ui.prev, ui.sidePrev].forEach(function (b) { setDisabled(b, viewer.prev.disabled); });
            [ui.next, ui.sideNext].forEach(function (b) { setDisabled(b, viewer.next.disabled); });
            setDisabled(ui.zoomIn, viewer.zoomIn.disabled);
            setDisabled(ui.zoomOut, viewer.zoomOut.disabled);
            showZoom(viewer.zoomText.value);
        }

        // First time the document's pages are known: starting zoom, then the saved page.
        function ready() {
            choose(viewer.zoomSelect, PHONE.matches ? "Fit width" : "Fit page");
            startBookmarks();
            if (!progress) {
                restored = true;
                return;
            }
            progress.load().then(function (saved) {
                var number = saved ? LilyProgress.parseTagged(saved.cfi, "page") : null;
                if (number !== null) {
                    goTo(Math.floor(number));
                }
            }).catch(function () {}).then(function () {
                restored = true;
            });
        }

        // Bookmarked pages ("page:N"), for signed-in readers: the bar's bookmark button marks
        // the page on screen, the list button opens the bookmarks to jump to or remove.
        function startBookmarks() {
            var url = container.getAttribute("data-bookmarks-url");
            if (bookmarks || !url || !window.LilyBookmarks) {
                return;
            }
            bookmarks = LilyBookmarks.paged({
                url: url,
                noticeEl: $("bookmark-status"),
                toggle: $("bookmark"),
                listButton: $("bookmarks-button"),
                panel: $("bookmarks-panel"),
                list: $("bookmarks"),
                empty: $("bookmarks-empty"),
                goTo: goTo
            });
        }

        ui.prev.addEventListener("click", function () { step(-1); });
        ui.next.addEventListener("click", function () { step(1); });
        ui.sidePrev.addEventListener("click", function () { step(-1); });
        ui.sideNext.addEventListener("click", function () { step(1); });
        ui.zoomIn.addEventListener("click", function () { viewer.zoomIn.click(); sync(); });
        ui.zoomOut.addEventListener("click", function () { viewer.zoomOut.click(); sync(); });

        ui.zoom.addEventListener("change", function () {
            if (viewer && ui.zoom.value !== "custom") {
                choose(viewer.zoomSelect, ui.zoom.value);
                sync();
            }
        });

        ui.page.addEventListener("change", function () {
            var number = parseInt(ui.page.value, 10);
            if (number >= 1 && number <= pages) {
                goTo(number);
            }
            ui.page.value = page;
        });
        ui.page.addEventListener("keydown", function (event) {
            if (event.key === "Enter") {
                ui.page.dispatchEvent(new Event("change"));
                ui.page.select();
            } else if (event.key === "Escape") {
                ui.page.value = page;
                ui.page.blur();
            }
        });
        ui.page.addEventListener("focus", function () { ui.page.select(); });

        // Left/right turn the page, as in the epub reader; the viewer keeps its own keys.
        // The zoom menu keeps focus after a pick and only needs up/down, so it passes them on.
        document.addEventListener("keydown", function (event) {
            if (!viewer || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey
                    || (event.target !== ui.zoom && /^(INPUT|SELECT|TEXTAREA)$/.test(event.target.tagName))) {
                return;
            }
            if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
                step(event.key === "ArrowLeft" ? -1 : 1);
                event.preventDefault();
            }
        });

        // At a page's edge the viewer turns the page on every wheel event, so a trackpad
        // swipe and its momentum ran through several pages. Once a page has turned, the
        // rest of that stream of wheel events is dropped; a pause starts a new swipe.
        container.addEventListener("wheel", function (event) {
            if (!viewer || event.ctrlKey) {
                return;     // ctrl + wheel (and pinch) is the viewer's zoom
            }
            var current = viewer.pageSelect.selectedIndex;
            if (event.timeStamp - swipe.at > 200) {
                swipe.page = current;
            }
            swipe.at = event.timeStamp;
            if (current !== swipe.page) {
                event.preventDefault();
                event.stopPropagation();
            }
        }, {capture: true, passive: false});

        if (document.fullscreenEnabled) {
            ui.fullscreen.hidden = false;
            ui.fullscreen.addEventListener("click", function () {
                if (document.fullscreenElement) {
                    document.exitFullscreen();
                } else {
                    document.documentElement.requestFullscreen().catch(function () {});
                }
            });
            document.addEventListener("fullscreenchange", function () {
                ui.fullscreen.setAttribute("aria-pressed", document.fullscreenElement ? "true" : "false");
            });
        }

        checkFile();
        sync();
        window.setInterval(sync, SYNC_EVERY);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();
