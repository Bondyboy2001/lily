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

/* A live tail: poll the data endpoint, append when the log only grew, and stay
 * pinned to the bottom unless the reader has scrolled up. Polling stops while
 * the tab is hidden. "Copy logs" puts everything shown on the clipboard. */
$(document).ready(function () {
    var $panel = $(".lily-logs");
    if (!$panel.length) {
        return;
    }

    var dataUrl = $panel.attr("data-logs-url");
    var emptyMessage = $panel.attr("data-empty-message") || "No logs captured yet.";
    var $status = $("#logs_status");
    var output = document.getElementById("log_output");

    var POLL_MS = 2000;
    var RETRY_MS = 10000;
    var version = "";
    var shown = null;
    var truncated = false;
    var inFlight = false;
    var timer = null;
    var $copy = $("#log_copy");
    var $copyLabel = $copy.find(".logs-copy-label");
    var copyLabel = $copyLabel.text();
    var copyNote = "";
    var copyTimer = null;

    function setStatus(message, isError) {
        $status.text(message);
        $status.toggleClass("is-error", !!isError);
    }

    function render(text) {
        var atBottom = shown === null ||
            output.scrollHeight - output.scrollTop - output.clientHeight < 20;
        if (shown && text.length > shown.length && text.indexOf(shown) === 0) {
            output.appendChild(document.createTextNode(text.slice(shown.length)));
        } else if (text !== shown) {
            output.textContent = text || emptyMessage;
        }
        shown = text;
        $copy.prop("disabled", !text);
        if (atBottom) {
            output.scrollTop = output.scrollHeight;
        }
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
        var url = version ? dataUrl + "?since=" + encodeURIComponent(version) : dataUrl;
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
            version = payload.version || "";
            if (!payload.unchanged) {
                truncated = !!payload.truncated;
                render(payload.text || "");
            }
            setStatus(copyNote || (truncated ? "Showing the most recent entries only." : ""), !!copyNote);
        }).catch(function (err) {
            delay = RETRY_MS;
            setStatus("Couldn't load new lines (" + err.message + "). Trying again shortly.", true);
        }).finally(function () {
            inFlight = false;
            schedule(delay);
        });
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

    function selectOutput() {
        var range = document.createRange();
        range.selectNodeContents(output);
        var selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
    }

    $copy.on("click", function () {
        clearTimeout(copyTimer);
        copyText(shown || "").then(function () {
            copyNote = "";
            $copyLabel.text($copyLabel.attr("data-done"));
        }, function () {
            // Leave the text selected so the shortcut is all that's left to do.
            selectOutput();
            copyNote = $copyLabel.attr("data-failed");
            setStatus(copyNote, true);
        }).then(function () {
            $copy[0].focus();
            copyTimer = setTimeout(function () {
                $copyLabel.text(copyLabel);
                if (copyNote) {
                    copyNote = "";
                    setStatus(truncated ? "Showing the most recent entries only." : "", false);
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

    poll();
});
