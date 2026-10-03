/* This file is part of the Calibre-Web-Automated (CWA) logs viewer
 *    Copyright (C) 2024-2026 CWA Contributors
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

/* Two live views behind two pills. App polls the log endpoint and draws the raw text as
 * one clean line per entry (a short time, a Warning/Error word, the message), grouped by
 * service; Metadata asks for lookups newer than the newest one shown and puts them on top.
 * Both read newest first. Only the view on screen is polled, every 2 s, and nothing while
 * the tab is hidden. New lines fade in; a reader scrolled down keeps their place.
 * "Copy logs" copies the view on screen (App: the raw text). */
(function (root) {
    "use strict";

    // s6-log's stamp: "2026-10-03 20:18:29.312064974  "
    var S6_STAMP = /^(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d)(?:\.\d+)?\s+/;
    // Python logging's: "[2026-10-03 20:18:29,311]  INFO {cps:160} "
    var PY_RECORD = /^\[(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d),\d+\]\s+([A-Z]+)\s+(?:\{[^}]*\}\s*)?/;
    // A service's own tag: "[cwa-ingest-service] ", "[cwa-db]: "
    var TAG = /^\[(?:cwa|lily)-[\w-]+\]:?\s*/;
    var BANNER = /^=+ .* =+$/;
    var SOURCE = /^===== (.*) =====$/;
    var LEVELS = { WARN: "warning", WARNING: "warning", ERROR: "error", CRITICAL: "error" };

    // "Lily web app (current)" and "Lily web app @4000….u" are one service; so are a log
    // file and its rotations.
    function sourceName(label) {
        return label
            .replace(/ \(current\)$/, "")
            .replace(/ @[0-9a-f]+\.[su]$/, "")
            .replace(/^Log (?:archive|file) (.+?)(?:\.\d+)?$/, "$1");
    }

    /* The endpoint's text as [{name, date, lines: [{key, date, time, level, text}]}], oldest
     * first: stamps, logger names, service tags and start-up banners dropped, consecutive
     * sources of one service merged. `date` is the group's newest. `key` is the raw line,
     * to tell new lines apart. */
    function parse(text) {
        var groups = [];
        var group = null;
        (text || "").split("\n").forEach(function (raw) {
            var source = SOURCE.exec(raw);
            if (source) {
                var name = sourceName(source[1]);
                if (!group || group.name !== name) {
                    group = { name: name, date: "", lines: [] };
                    groups.push(group);
                }
                return;
            }
            if (!raw.trim()) {
                return;
            }
            var date = "";
            var time = "";
            var level = "";
            var rest = raw;
            var stamp = S6_STAMP.exec(rest);
            if (stamp) {
                date = stamp[1];
                time = stamp[2];
                rest = rest.slice(stamp[0].length);
            }
            var record = PY_RECORD.exec(rest);
            if (record) {
                date = date || record[1];
                time = time || record[2];
                level = LEVELS[record[3]] || "";
                rest = rest.slice(record[0].length);
            }
            rest = rest.replace(TAG, "").trim();
            if (!rest || BANNER.test(rest)) {
                return;
            }
            if (!group) {
                group = { name: "", date: "", lines: [] };
                groups.push(group);
            }
            group.date = date || group.date;
            group.lines.push({ key: raw, date: date, time: time, level: level, text: rest });
        });
        return groups.filter(function (g) { return g.lines.length; });
    }

    root.LilyLogs = { parse: parse };

    if (typeof $ === "undefined") {
        return;
    }

    $(document).ready(function () {
        var panel = document.querySelector(".lily-logs");
        if (!panel) {
            return;
        }

        var POLL_MS = 2000;
        var RETRY_MS = 10000;
        var KEEP_LOOKUPS = 500;
        var dataUrl = panel.getAttribute("data-logs-url");
        var lookupsUrl = panel.getAttribute("data-lookups-url");
        var emptyMessage = panel.getAttribute("data-empty-message") || "No logs captured yet.";
        var words = {
            warning: panel.getAttribute("data-warning") || "Warning",
            error: panel.getAttribute("data-error") || "Error"
        };
        var status = document.getElementById("logs_status");
        var live = document.getElementById("logs_live");
        var output = document.getElementById("log_output");
        var lookupOutput = document.getElementById("lookup_output");
        var lookupEmpty = document.getElementById("lookup_empty");
        var tabs = [document.getElementById("logs_tab_app"), document.getElementById("logs_tab_metadata")];
        var panels = [document.getElementById("logs_app"), document.getElementById("logs_metadata")];
        var copy = document.getElementById("log_copy");
        var copyLabel = copy.querySelector(".logs-copy-label");
        var copyIcon = copy.querySelector(".glyphicon");
        var copyText0 = copyLabel.textContent;
        var copyNote = "";
        var copyTimer = null;

        var version = "";
        var latest = null; // newest text from the server
        var shown = null; // text on screen
        var seen = null; // raw line -> its element on screen, so new ones can be marked
        var inFlight = false;
        var timer = null;
        var view = 0;

        // The bar sticks just under the top bar, whose height changes as it wraps on phones.
        var bar = panel.querySelector(".logs-bar");
        var topbar = document.querySelector(".lily-topbar");
        function placeBar() {
            if (topbar) {
                panel.style.setProperty("--logs-bar-top", topbar.offsetHeight + "px");
            }
        }
        placeBar();
        window.addEventListener("resize", placeBar);

        function setStatus(message, isError) {
            status.textContent = message;
            status.classList.toggle("is-error", !!isError);
            live.classList.toggle("is-error", !!isError && !copyNote);
        }

        function setCopyEnabled() {
            copy.disabled = view === 0 ? !shown : !lookupOutput.querySelector(".logs-line");
        }

        // A selection inside the log would be lost on redraw, so wait until it's gone.
        function selecting() {
            var sel = window.getSelection && window.getSelection();
            return !!(sel && !sel.isCollapsed && sel.anchorNode && output.contains(sel.anchorNode));
        }

        /* New lines go on top. A reader who has scrolled down to older lines keeps their
         * place: note the first line showing under the bar, and after the redraw scroll by
         * however far it moved. A reader at the top just sees the new lines arrive. */
        function place(list) {
            var below = bar.getBoundingClientRect().bottom;
            if (list.getBoundingClientRect().top >= below) {
                return null;
            }
            var rows = list.querySelectorAll(".logs-line");
            for (var i = 0; i < rows.length; i++) {
                var top = rows[i].getBoundingClientRect().top;
                if (top >= below) {
                    return { el: rows[i], top: top };
                }
            }
            return null;
        }

        function keepPlace(mark, el) {
            if (mark && el && el.parentNode && window.scrollBy) {
                window.scrollBy(0, el.getBoundingClientRect().top - mark.top);
            }
        }

        function line(entry, fresh) {
            var p = document.createElement("p");
            p.className = "logs-line" + (entry.level ? " is-" + entry.level : "") + (fresh ? " is-new" : "");
            var time = document.createElement("time");
            time.textContent = entry.time;
            if (entry.date) {
                time.setAttribute("datetime", entry.date + "T" + entry.time);
            }
            var text = document.createElement("span");
            text.className = "logs-text";
            if (entry.level) {
                var word = document.createElement("b");
                word.className = "logs-level";
                word.textContent = words[entry.level];
                text.appendChild(word);
                text.appendChild(document.createTextNode(" "));
            }
            text.appendChild(document.createTextNode(entry.text));
            p.appendChild(time);
            p.appendChild(text);
            return p;
        }

        function today() {
            var d = new Date();
            return d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2) + "-" + ("0" + d.getDate()).slice(-2);
        }

        // "Auto zipper", or "Auto zipper, 2 October" when its lines are from another day.
        function groupLabel(group) {
            if (!group.date || group.date === today()) {
                return group.name;
            }
            var parts = group.date.split("-");
            var day = new Date(+parts[0], +parts[1] - 1, +parts[2]).toLocaleDateString(
                document.documentElement.lang || undefined, { day: "numeric", month: "long" });
            return group.name ? group.name + ", " + day : day;
        }

        function renderApp() {
            if (latest === null || latest === shown || selecting()) {
                return;
            }
            var mark = view === 0 ? place(output) : null;
            var markKey = null;
            if (mark) {
                for (var key in seen) {
                    if (seen[key] === mark.el) {
                        markKey = key;
                        break;
                    }
                }
            }
            var groups = parse(latest).reverse();
            var next = {};
            var frag = document.createDocumentFragment();
            groups.forEach(function (group) {
                var box = document.createElement("div");
                box.className = "logs-group";
                var head = document.createElement("p");
                head.className = "logs-source";
                head.textContent = groupLabel(group);
                box.appendChild(head);
                group.lines.slice().reverse().forEach(function (entry) {
                    var p = line(entry, seen !== null && !seen[entry.key]);
                    box.appendChild(p);
                    next[entry.key] = p;
                });
                frag.appendChild(box);
            });
            if (!groups.length) {
                var empty = document.createElement("p");
                empty.className = "logs-empty";
                empty.textContent = emptyMessage;
                frag.appendChild(empty);
            }
            output.textContent = "";
            output.appendChild(frag);
            seen = next;
            shown = latest;
            setCopyEnabled();
            keepPlace(mark, markKey === null ? null : next[markKey]);
        }

        function appendLookups(html) {
            if (!html) {
                return;
            }
            var mark = view === 1 ? place(lookupOutput) : null;
            var holder = document.createElement("div");
            holder.innerHTML = html;
            var frag = document.createDocumentFragment();
            var day = null;
            while (holder.firstChild) {
                var node = holder.firstChild;
                if (node.nodeType === 1 && node.classList.contains("logs-line")) {
                    node.classList.add("is-new");
                    day = node.getAttribute("data-day");
                }
                frag.appendChild(node);
            }
            // The new rows carry their days' headings, so the oldest of them, if it's the
            // day already on top, takes over that heading.
            var top = lookupOutput.firstElementChild;
            if (top && top.classList.contains("logs-source") && top.getAttribute("data-day") === day) {
                lookupOutput.removeChild(top);
            }
            lookupOutput.insertBefore(frag, lookupOutput.firstChild);
            // A long-open page during a library-wide rebuild keeps only the newest rows.
            var rows = lookupOutput.querySelectorAll(".logs-line");
            for (var i = KEEP_LOOKUPS; i < rows.length; i++) {
                lookupOutput.removeChild(rows[i]);
            }
            var last = lookupOutput.lastElementChild;
            while (last && last.classList.contains("logs-source")) {
                lookupOutput.removeChild(last);
                last = lookupOutput.lastElementChild;
            }
            lookupEmpty.hidden = true;
            setCopyEnabled();
            keepPlace(mark, mark && mark.el);
        }

        function lookupsQuery() {
            var newest = lookupOutput.querySelector(".logs-line");
            return newest ? "?after=" + encodeURIComponent(newest.getAttribute("data-id")) : "";
        }

        function stop() {
            if (timer !== null) {
                clearTimeout(timer);
                timer = null;
            }
        }

        function schedule(delay) {
            stop();
            if (document.visibilityState !== "hidden") {
                timer = setTimeout(poll, delay);
            }
        }

        function poll() {
            timer = null;
            if (inFlight) {
                return;
            }
            inFlight = true;
            var delay = POLL_MS;
            var forApp = view === 0;
            var url = forApp ? dataUrl + (version ? "?since=" + encodeURIComponent(version) : "")
                             : lookupsUrl + lookupsQuery();
            fetch(url, {
                headers: { "Accept": "application/json" },
                credentials: "same-origin"
            }).then(function (response) {
                if (!response.ok) {
                    throw new Error("HTTP " + response.status);
                }
                return response.json();
            }).then(function (payload) {
                if (!payload.success) {
                    throw new Error(payload.error || "failed");
                }
                if (forApp) {
                    version = payload.version || "";
                    if (!payload.unchanged) {
                        latest = payload.text || "";
                    }
                } else {
                    appendLookups(payload.html);
                }
                setStatus(copyNote, !!copyNote);
            }).catch(function (err) {
                delay = RETRY_MS;
                setStatus("Couldn't load new lines (" + err.message + "). Trying again shortly.", true);
            }).finally(function () {
                inFlight = false;
                renderApp();
                schedule(delay);
            });
        }

        function show(index) {
            view = index;
            tabs.forEach(function (tab, i) {
                tab.classList.toggle("active", i === index);
                tab.setAttribute("aria-selected", i === index ? "true" : "false");
                tab.tabIndex = i === index ? 0 : -1;
                panels[i].hidden = i !== index;
            });
            setCopyEnabled();
            renderApp();
            // Each view opens at its newest line.
            if (window.scrollTo) {
                window.scrollTo(0, 0);
            }
            if (!inFlight && document.visibilityState !== "hidden") {
                stop();
                poll();
            }
        }

        tabs.forEach(function (tab, i) {
            tab.addEventListener("click", function () {
                show(i);
            });
            tab.addEventListener("keydown", function (e) {
                if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
                    e.preventDefault();
                    show(1 - i);
                    tabs[1 - i].focus();
                }
            });
        });

        function viewText() {
            if (view === 0) {
                return shown || "";
            }
            return Array.prototype.map.call(lookupOutput.children, function (el) {
                var text = el.textContent.replace(/\s+/g, " ").trim();
                return el.classList.contains("logs-source") ? "\n" + text : text;
            }).join("\n").trim();
        }

        function copyText(text) {
            if (navigator.clipboard && window.isSecureContext) {
                return navigator.clipboard.writeText(text);
            }
            // Over plain HTTP (a NAS on the LAN) there is no Clipboard API, so copy from a
            // hidden textarea instead.
            return new Promise(function (resolve, reject) {
                var area = document.createElement("textarea");
                area.value = text;
                area.setAttribute("readonly", "");
                area.className = "logs-copy-buffer";
                document.body.appendChild(area);
                area.select();
                var copied = false;
                try {
                    copied = document.execCommand("copy");
                } catch (e) {
                    copied = false;
                }
                document.body.removeChild(area);
                if (copied) {
                    resolve();
                } else {
                    reject(new Error("copy refused"));
                }
            });
        }

        function selectView() {
            var range = document.createRange();
            range.selectNodeContents(view === 0 ? output : lookupOutput);
            var selection = window.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
        }

        copy.addEventListener("click", function () {
            clearTimeout(copyTimer);
            copyText(viewText()).then(function () {
                copyNote = "";
                copyLabel.textContent = copyLabel.getAttribute("data-done");
                // A tick in place of the copy glyph says it worked.
                copyIcon.classList.remove("glyphicon-copy");
                copyIcon.classList.add("glyphicon-ok");
            }, function () {
                // Leave the text selected so the shortcut is all that's left to do.
                selectView();
                copyNote = copyLabel.getAttribute("data-failed");
                setStatus(copyNote, true);
            }).then(function () {
                copy.focus();
                copyTimer = setTimeout(function () {
                    copyLabel.textContent = copyText0;
                    copyIcon.classList.remove("glyphicon-ok");
                    copyIcon.classList.add("glyphicon-copy");
                    if (copyNote) {
                        copyNote = "";
                        setStatus("", false);
                    }
                }, copyNote ? 8000 : 2000);
            });
        });

        document.addEventListener("visibilitychange", function () {
            if (document.visibilityState === "hidden") {
                stop();
            } else if (timer === null && !inFlight) {
                poll();
            }
        });

        // A selection that blocked a redraw may end without a poll; catch up then.
        document.addEventListener("selectionchange", function () {
            if (latest !== shown && !selecting()) {
                renderApp();
            }
        });

        setCopyEnabled();
        poll();
    });
}(typeof window !== "undefined" ? window : this));
