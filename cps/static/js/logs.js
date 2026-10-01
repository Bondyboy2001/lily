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

$(document).ready(function () {
    var $panel = $(".lily-logs");
    if (!$panel.length) {
        return;
    }

    var dataUrl = $panel.attr("data-logs-url");
    var $source = $("#log_source");
    var $search = $("#log_search");
    var $errors = $("#log_errors");
    var $autorefresh = $("#log_autorefresh");
    var $refresh = $("#log_refresh");
    var $status = $("#logs_status");
    var output = document.getElementById("log_output");

    var rawText = "";
    var generation = 0;
    var inFlight = false;
    var refreshPending = false;
    var CONTEXT_LINES = 6;
    var POLL_MS = 5000;
    var ERROR_RE = /\b(WARN|WARNING|ERROR|CRIT|CRITICAL)\b|Traceback|Exception/i;

    function setStatus(message, isError) {
        $status.text(message);
        $status.toggleClass("is-error", !!isError);
    }

    function applyFilters() {
        var query = $search.val().toLowerCase();
        var lines = rawText ? rawText.split("\n") : [];

        if (query) {
            lines = lines.filter(function (line) {
                return line.toLowerCase().indexOf(query) !== -1;
            });
        }

        if ($errors.is(":checked")) {
            var keep = [];
            lines.forEach(function (line, i) {
                if (ERROR_RE.test(line)) {
                    for (var j = Math.max(0, i - CONTEXT_LINES); j <= i + CONTEXT_LINES; j++) {
                        keep[j] = true;
                    }
                }
            });
            lines = lines.filter(function (line, i) {
                return keep[i];
            });
        }

        var atBottom = output.scrollHeight - output.scrollTop - output.clientHeight < 20;
        var filtered = lines.join("\n");
        output.textContent = filtered ||
            $panel.attr("data-empty-message") || "No logs captured yet.";
        if (atBottom) {
            output.scrollTop = output.scrollHeight;
        }
    }

    function fillSources(sources) {
        var selected = $source.val() || "all";
        $source.find("option[value!='all']").remove();
        sources.forEach(function (s) {
            var option = document.createElement("option");
            option.value = s.id;
            option.textContent = s.label;
            $source.append(option);
        });
        if ($source.find("option[value='" + selected + "']").length) {
            $source.val(selected);
        } else {
            $source.val("all");
        }
    }

    function fetchLogs() {
        if (inFlight) {
            refreshPending = true;
            return;
        }
        inFlight = true;
        var wanted = $source.val() || "all";
        var myGeneration = ++generation;
        fetch(dataUrl + "?source=" + encodeURIComponent(wanted), {
            headers: { "Accept": "application/json" },
            credentials: "same-origin"
        }).then(function (response) {
            if (!response.ok) {
                throw new Error("HTTP " + response.status);
            }
            return response.json();
        }).then(function (payload) {
            if (myGeneration !== generation) {
                return;
            }
            if (!payload.success) {
                throw new Error(payload.error || "failed");
            }
            fillSources(payload.sources || []);
            rawText = payload.text || "";
            applyFilters();
            setStatus(payload.truncated ? "Showing the most recent entries only." : "", false);
        }).catch(function (err) {
            if (myGeneration === generation) {
                setStatus("Refresh failed: " + err.message, true);
            }
        }).finally(function () {
            inFlight = false;
            if (refreshPending) {
                refreshPending = false;
                fetchLogs();
            }
        });
    }

    $refresh.on("click", fetchLogs);
    $source.on("change", function () {
        generation++;
        fetchLogs();
    });
    $search.on("input", applyFilters);
    $errors.on("change", applyFilters);

    setInterval(function () {
        if ($autorefresh.is(":checked") && document.visibilityState === "visible") {
            fetchLogs();
        }
    }, POLL_MS);

    fetchLogs();
});
