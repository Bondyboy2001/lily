/* global reader, themes, readerFontStacks */

/* Settings sheet of the epub reader: page theme, text size, font, line spacing, margins,
 * two-page spread and sidebar reflow. Choices are kept per browser in localStorage
 * ("calibre.reader.*"). Until a page theme has been picked, the page follows the app's
 * light/dark theme (data-theme, set by lily_theme_head.html). */
(function () {
    "use strict";

    if (!window.reader || !reader.rendition) {
        return;
    }
    var rendition = reader.rendition;
    var root = document.documentElement;
    var APP_THEME = root.getAttribute("data-theme") === "dark" ? "darkTheme" : "lightTheme";
    // The width below which epub.js shows one page anyway and the sidebar covers the page.
    var NARROW = window.matchMedia("(max-width: 799px)");
    var FONT_SIZES = [70, 80, 90, 100, 110, 120, 135, 150, 175, 200];

    function load(key) {
        try {
            return window.localStorage.getItem("calibre.reader." + key);
        } catch (e) {
            return null;
        }
    }

    function store(key, value) {
        try {
            window.localStorage.setItem("calibre.reader." + key, value);
        } catch (e) {
            // Private mode or storage full: the choice lasts for this page only.
        }
    }

    function onNarrowChange(callback) {
        if (NARROW.addEventListener) {
            NARROW.addEventListener("change", callback);
        } else if (NARROW.addListener) {
            NARROW.addListener(callback);
        }
    }

    /** Puts the tick on the chosen button of a group and marks it pressed. */
    function tick(group, id) {
        if (!group) {
            return;
        }
        group.querySelectorAll("button").forEach(function (button) {
            var chosen = button.id === id;
            button.setAttribute("aria-pressed", chosen ? "true" : "false");
            var mark = button.querySelector("span");
            if (mark) {
                mark.textContent = chosen ? "✓" : "";
            }
        });
    }

    function onButtons(group, callback) {
        if (!group) {
            return;
        }
        group.querySelectorAll("button").forEach(function (button) {
            button.addEventListener("click", function () {
                callback(button.id);
            });
        });
    }

    // ------------------------------------------------------------------ page theme
    var themeGroup = document.getElementById("themes");

    function applyTheme(id) {
        if (!themes[id]) {
            id = APP_THEME;
        }
        var theme = themes[id];
        tick(themeGroup, id);
        rendition.themes.select(id);
        var main = document.getElementById("main");
        main.style.backgroundColor = theme.bgColor;
        main.style.color = theme["title-color"];
        // The toolbar, sidebar, settings sheet and the phone's status bar follow the page,
        // so a dark page never sits under a light toolbar.
        root.setAttribute("data-theme", theme.dark ? "dark" : "light");
        document.querySelectorAll('meta[name="theme-color"]').forEach(function (meta) {
            meta.setAttribute("content", theme.bgColor);
        });
        return id;
    }

    window.selectTheme = function (id) {
        store("theme", applyTheme(id));
    };
    onButtons(themeGroup, window.selectTheme);
    // Not stored: without a choice the page keeps following the app theme.
    applyTheme(load("theme"));

    // ------------------------------------------------------------------ text size
    var smaller = document.getElementById("fontSmaller");
    var larger = document.getElementById("fontLarger");
    var sizeValue = document.getElementById("fontSizeValue");
    var fontSize = parseInt(load("fontSize"), 10);

    function showFontSize() {
        if (sizeValue) {
            sizeValue.textContent = fontSize + "%";
        }
        if (smaller) {
            smaller.disabled = fontSize <= FONT_SIZES[0];
        }
        if (larger) {
            larger.disabled = fontSize >= FONT_SIZES[FONT_SIZES.length - 1];
        }
    }

    function stepFontSize(direction) {
        var next = fontSize;
        if (direction > 0) {
            next = FONT_SIZES.filter(function (size) { return size > fontSize; })[0];
        } else {
            next = FONT_SIZES.filter(function (size) { return size < fontSize; }).pop();
        }
        if (!next) {
            return;
        }
        fontSize = next;
        rendition.themes.fontSize(fontSize + "%");
        store("fontSize", fontSize);
        showFontSize();
    }

    if (fontSize >= FONT_SIZES[0] && fontSize <= FONT_SIZES[FONT_SIZES.length - 1]) {
        rendition.themes.fontSize(fontSize + "%");
    } else {
        // Untouched: leave the book's own size alone.
        fontSize = 100;
    }
    showFontSize();
    if (smaller) {
        smaller.addEventListener("click", function () { stepFontSize(-1); });
    }
    if (larger) {
        larger.addEventListener("click", function () { stepFontSize(1); });
    }

    // ------------------------------------------------------------------ font
    var fontGroup = document.getElementById("font");

    function applyFont(id) {
        if (id !== "default" && !readerFontStacks[id]) {
            id = "default";
        }
        tick(fontGroup, id);
        // An empty value drops the override, so "Book default" really is the book's font.
        rendition.themes.font(id === "default" ? "" : readerFontStacks[id]);
        return id;
    }

    window.selectFont = function (id) {
        store("font", applyFont(id));
    };
    onButtons(fontGroup, window.selectFont);
    if (load("font")) {
        applyFont(load("font"));
    } else {
        tick(fontGroup, "default");
    }

    // ------------------------------------------------------------------ line spacing, margins
    // Injected as a small stylesheet into every rendered section (epub.js content hook).
    var readerLayout = {
        lineHeight: load("lineHeight") || "default",
        margin: load("margin") || "0"
    };

    function readerLayoutCss() {
        var css = "";
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

    if (rendition.hooks && rendition.hooks.content) {
        rendition.hooks.content.register(applyReaderLayout);
    }

    function bindLayoutSelect(selectId, key, fallback) {
        var select = document.getElementById(selectId);
        if (!select) {
            return;
        }
        select.value = readerLayout[key];
        if (select.value !== readerLayout[key]) {
            select.value = fallback;
        }
        select.addEventListener("change", function () {
            readerLayout[key] = this.value;
            store(key, this.value);
            if (typeof rendition.getContents === "function") {
                rendition.getContents().forEach(applyReaderLayout);
            }
        });
    }
    bindLayoutSelect("lineHeightSelect", "lineHeight", "default");
    bindLayoutSelect("marginSelect", "margin", "0");

    // ------------------------------------------------------------------ spread
    // Two columns only make sense on a wide screen; below 800px the option is hidden and the
    // page is always one column, whatever was chosen on a wider one.
    var layoutGroup = document.getElementById("layout");
    var spreadChoice = load("spread") === "nonespread" ? "nonespread" : "spread";

    function applySpread() {
        tick(layoutGroup, spreadChoice);
        rendition.spread(spreadChoice === "spread" && !NARROW.matches ? "auto" : "none");
    }

    window.spread = function (id) {
        spreadChoice = id === "nonespread" ? "nonespread" : "spread";
        store("spread", spreadChoice);
        applySpread();
    };
    onButtons(layoutGroup, window.spread);
    applySpread();
    onNarrowChange(applySpread);

    // ------------------------------------------------------------------ sidebar reflow
    // Wide screens only: narrow the page beside the contents panel instead of sliding the
    // page over. reader.min.js reads reader.settings.sidebarReflow when the panel opens.
    var reflowBox = document.getElementById("sidebarReflow");

    function applyReflow() {
        if (reader.settings) {
            reader.settings.sidebarReflow = !!(reflowBox && reflowBox.checked) && !NARROW.matches;
        }
    }

    if (reflowBox) {
        reflowBox.checked = load("reflow") === "true";
        reflowBox.addEventListener("change", function () {
            store("reflow", this.checked);
            applyReflow();
        });
    }
    applyReflow();
    onNarrowChange(applyReflow);
})();
