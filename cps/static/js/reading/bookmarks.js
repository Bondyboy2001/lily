/* Lily reader bookmarks: any number per book, kept on the server for the signed-in user.
 *
 *   GET    <url>       -> {"bookmarks": [{id, key, label, excerpt}, ...]}
 *   POST   <url>       <- {key, label, excerpt}   (X-CSRFToken header) -> the bookmark
 *   DELETE <url>/<id>  -> 204
 * A key is a position in the progress sync's form: an epub CFI or "page:N".
 *
 * Usage:
 *   var marks = LilyBookmarks.create({url: "/ajax/bookmarks/12/EPUB", noticeEl: el,
 *                                     onChange: function (items) { ...redraw... }});
 *   marks.load(); marks.add(key, label, excerpt); marks.remove(item); marks.items();
 * A failed request leaves the list as it was and says so in noticeEl (a quiet
 * role=status line whose data-*-failed attributes hold the translated text).
 *
 * LilyBookmarks.paged() wires the pdf and djvu readers: a toggle for the current page,
 * a button that opens the list, and the list itself.
 */
(function (window) {
    "use strict";

    var NOTICE_CLEAR = 6000;

    function csrfToken() {
        var input = document.querySelector("input[name='csrf_token']");
        return input && input.value ? input.value : "";
    }

    function send(method, url, body) {
        if (!window.fetch) {
            return Promise.reject(new Error("fetch unavailable"));
        }
        var headers = {"Accept": "application/json"};
        if (method !== "GET") {
            headers["X-CSRFToken"] = csrfToken();
        }
        if (body !== undefined) {
            headers["Content-Type"] = "application/json";
        }
        return window.fetch(url, {
            method: method,
            credentials: "same-origin",
            headers: headers,
            body: body === undefined ? undefined : JSON.stringify(body)
        }).then(function (response) {
            if (!response.ok) {
                throw new Error("HTTP " + response.status);
            }
            if (response.status === 204) {
                return null;
            }
            var type = response.headers.get("Content-Type") || "";
            if (type.indexOf("json") === -1) {
                throw new Error("not JSON");   // e.g. the login page after a lapsed session
            }
            return response.json();
        });
    }

    function valid(item) {
        return item && typeof item.id === "number" && typeof item.key === "string" && item.key;
    }

    function create(options) {
        var url = options.url;
        var noticeEl = options.noticeEl || null;
        var onChange = typeof options.onChange === "function" ? options.onChange : function () {};
        var items = [];
        var clearTimer = null;

        function notice(kind) {
            if (!noticeEl) {
                return;
            }
            if (clearTimer) {
                clearTimeout(clearTimer);
            }
            noticeEl.textContent = noticeEl.getAttribute("data-" + kind + "-failed") || "";
            clearTimer = setTimeout(function () { noticeEl.textContent = ""; }, NOTICE_CLEAR);
        }

        function changed() {
            try { onChange(items.slice()); } catch (e) { /* a redraw must not break the reader */ }
        }

        function find(key) {
            for (var i = 0; i < items.length; i++) {
                if (items[i].key === key) {
                    return items[i];
                }
            }
            return null;
        }

        return {
            load: function () {
                return send("GET", url).then(function (data) {
                    items = (data && Array.isArray(data.bookmarks) ? data.bookmarks : []).filter(valid);
                    changed();
                    return items.slice();
                }).catch(function () {
                    notice("load");
                    changed();
                    return [];
                });
            },
            /** Resolves to the saved bookmark, or null when it could not be saved. */
            add: function (key, label, excerpt) {
                var known = find(key);
                if (known) {
                    return Promise.resolve(known);
                }
                return send("POST", url, {key: key, label: label || "", excerpt: excerpt || ""})
                    .then(function (item) {
                        if (!valid(item)) {
                            throw new Error("bad reply");
                        }
                        if (!find(item.key)) {
                            items.push(item);
                        }
                        changed();
                        return item;
                    }).catch(function () {
                        notice("save");
                        return null;
                    });
            },
            /** Resolves to true once the bookmark is gone. */
            remove: function (item) {
                return send("DELETE", url + "/" + item.id).then(function () {
                    items = items.filter(function (other) { return other.id !== item.id; });
                    changed();
                    return true;
                }).catch(function () {
                    notice("remove");
                    return false;
                });
            },
            items: function () { return items.slice(); },
            find: find
        };
    }

    /** A row for a bookmark list: a jump button and a remove icon button. */
    function row(options) {
        var li = document.createElement("li");
        li.className = options.rowClass || "reader-bookmark";
        var jump = document.createElement("button");
        jump.type = "button";
        jump.className = options.jumpClass || "reader-bookmark-jump";
        (options.lines || []).forEach(function (line) {
            if (line.text) {
                var span = document.createElement("span");
                span.className = line.className;
                span.textContent = line.text;
                jump.appendChild(span);
            }
        });
        jump.addEventListener("click", options.onJump);
        var remove = document.createElement("button");
        remove.type = "button";
        remove.className = options.removeClass || "icon-btn is-danger reader-bookmark-remove";
        remove.setAttribute("aria-label", options.removeLabel);
        remove.title = options.removeTitle;
        if (options.removeGlyph !== false) {
            var glyph = document.createElement("span");
            glyph.className = "glyphicon glyphicon-trash";
            glyph.setAttribute("aria-hidden", "true");
            remove.appendChild(glyph);
        }
        remove.addEventListener("click", function (event) {
            event.stopPropagation();
            remove.disabled = true;
            options.onRemove().then(function (done) {
                if (!done) {
                    remove.disabled = false;
                }
            });
        });
        li.appendChild(jump);
        li.appendChild(remove);
        return li;
    }

    function fill(template, values) {
        return String(template || "").replace(/%\((\w+)\)s/g, function (match, name) {
            return name in values ? values[name] : match;
        });
    }

    /* The pdf and djvu readers bookmark whole pages ("page:N").
     *   options.url, noticeEl        as for create()
     *   options.toggle               button with aria-pressed and data-add-label / data-remove-label
     *   options.listButton, panel    the button (aria-expanded) and the panel it shows
     *   options.list, empty          the <ul> and the "no bookmarks yet" line in the panel
     *   options.goTo(page)           turns the reader to a page
     *   options.classes              optional row/jump/remove class names (pdf.js chrome)
     *   options.showPanel(open)      optional; defaults to toggling panel.hidden
     * Returns {setPage(n)}: call it whenever the reader's current page changes.
     */
    function paged(options) {
        var toggle = options.toggle;
        var listButton = options.listButton;
        var panel = options.panel;
        var list = options.list;
        var empty = options.empty;
        var classes = options.classes || {};
        var page = 0;
        var busy = false;
        var text = function (name) { return list.getAttribute("data-" + name) || ""; };
        var showPanel = options.showPanel || function (open) { panel.hidden = !open; };

        var store = create({url: options.url, noticeEl: options.noticeEl, onChange: draw});

        function pageOf(item) {
            var number = window.LilyProgress ? window.LilyProgress.parseTagged(item.key, "page")
                : parseInt(String(item.key).replace(/^page:/, ""), 10);
            return number === null || isNaN(number) ? 0 : Math.floor(number);
        }

        function update() {
            var marked = page > 0 && !!store.find("page:" + page);
            var label = toggle.getAttribute(marked ? "data-remove-label" : "data-add-label");
            toggle.setAttribute("aria-pressed", marked ? "true" : "false");
            toggle.classList.toggle("toggled", marked && !!classes.toggled);
            if (label) {
                toggle.setAttribute("aria-label", label);
                toggle.title = label;
            }
        }

        function draw(items) {
            items = items.filter(function (item) { return pageOf(item) > 0; })
                .sort(function (a, b) { return pageOf(a) - pageOf(b); });
            list.textContent = "";
            items.forEach(function (item) {
                var number = pageOf(item);
                list.appendChild(row({
                    rowClass: classes.row,
                    jumpClass: classes.jump,
                    removeClass: classes.remove,
                    removeGlyph: classes.removeGlyph,
                    lines: [{className: "reader-bookmark-page", text: fill(text("page"), {page: number})}],
                    removeLabel: fill(text("remove"), {page: number}),
                    removeTitle: text("remove-title"),
                    onJump: function () {
                        options.goTo(number);
                        open(false);
                    },
                    onRemove: function () { return store.remove(item); }
                }));
            });
            empty.hidden = items.length > 0;
            update();
        }

        function isOpen() {
            return listButton.getAttribute("aria-expanded") === "true";
        }

        function open(on) {
            listButton.setAttribute("aria-expanded", on ? "true" : "false");
            listButton.classList.toggle("toggled", on && !!classes.toggled);
            showPanel(on);
        }

        toggle.addEventListener("click", function () {
            if (busy || page < 1) {
                return;
            }
            busy = true;
            var known = store.find("page:" + page);
            var done = known ? store.remove(known) : store.add("page:" + page, "", "");
            done.then(function () { busy = false; update(); });
        });

        listButton.addEventListener("click", function (event) {
            event.stopPropagation();
            open(!isOpen());
        });
        document.addEventListener("click", function (event) {
            if (isOpen() && !panel.contains(event.target) && !listButton.contains(event.target)) {
                open(false);
            }
        });
        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && isOpen()) {
                open(false);
                listButton.focus();
            }
        });

        toggle.hidden = false;
        listButton.hidden = false;
        store.load();

        return {
            setPage: function (number) {
                if (number !== page) {
                    page = number;
                    update();
                }
            }
        };
    }

    window.LilyBookmarks = {create: create, row: row, paged: paged, fill: fill};
})(window);
