/* This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
 *    Copyright (C) 2020 OzzieIsaacs
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

/* exported EbookActions, responseHandler, bookCheckboxFormatter, ratingFormatter */
/* global getPath */

var selections = [];
var reload = false;
// Remember last clicked row index for shift-range selection on books table
var lastBooksTableIndex = null;
// No row-based shift logic; we handle shift ranges on checkbox clicks only to avoid conflicts

$(function() {
    $(document).on('click', '#select_all', function() {
        $('#books-table').bootstrapTable('checkAll');
    });

    $(document).on('click', '#unselect_all', function() {
        $('#books-table').bootstrapTable('uncheckAll');
    });

    // Shift-click range selection for books table checkboxes (capture phase to bypass stopPropagation in plugin)
    (function setupBooksTableShiftClick() {
        document.addEventListener('click', function (e) {
            var target = e.target;
            if (!target || target.type !== 'checkbox') return;
            // Only handle row selection checkboxes inside the books table
            var td = target.closest('td');
            if (!td || !td.classList || !td.classList.contains('bs-checkbox')) return;
            var tableEl = document.getElementById('books-table');
            if (!tableEl || !tableEl.contains(target)) return;

            // If no shift, just update the anchor index and exit
            if (!e.shiftKey) {
                var tr0 = target.closest('tr');
                lastBooksTableIndex = tr0 ? parseInt(tr0.getAttribute('data-index'), 10) : null;
                return;
            }

            // Run after the checkbox default toggled state is applied
            setTimeout(function () {
                var tr = target.closest('tr');
                if (!tr) return;
                var currentIndex = parseInt(tr.getAttribute('data-index'), 10);
                if (isNaN(currentIndex)) return;

                // If no previous anchor, just set it and exit
                if (lastBooksTableIndex === null) {
                    lastBooksTableIndex = currentIndex;
                    return;
                }

                var start = Math.min(lastBooksTableIndex, currentIndex);
                var end = Math.max(lastBooksTableIndex, currentIndex);
                var $table = $('#books-table');
                if (!$table.length) return;
                var data = $table.bootstrapTable('getData') || [];
                var idsInRange = [];
                for (var i = start; i <= end; i++) {
                    if (data[i] && typeof data[i].id !== 'undefined') idsInRange.push(data[i].id);
                }

                if (idsInRange.length) {
                    if (target.checked) {
                        $table.bootstrapTable('checkBy', { field: 'id', values: idsInRange });
                    } else {
                        $table.bootstrapTable('uncheckBy', { field: 'id', values: idsInRange });
                    }
                }

                // Set anchor to current row
                lastBooksTableIndex = currentIndex;
            }, 0);
        }, true); // capture phase
    })();

    // Reset the anchor when table view changes (pagination, sorting, searching, reload)
    $('#books-table').on('page-change.bs.table sort.bs.table search.bs.table load-success.bs.table', function () {
        lastBooksTableIndex = null;
    });

    // Label the row-select checkboxes (reapplied on table updates). The title attribute
    // gives the browser's own popup; the app no longer builds Bootstrap tooltips.
    function applyBooksTableCheckboxTooltips() {
        var $checks = $('#books-table tbody td.bs-checkbox input[type="checkbox"]');
        if (!$checks.length) return;
        $checks.attr('title', 'Shift-click to select a range');
        $checks.attr('aria-label', 'Select row (Shift-click to select a range)');
    }

    // Move Select/Clear buttons into the columns-right toolbar group
    function moveBooksToolbarButtons() {
        var $table = $('#books-table');
        if (!$table.length) return;
        var $toolbarRight = $table.closest('.bootstrap-table').find('.fixed-table-toolbar .columns.columns-right.btn-group.pull-right');
        if (!$toolbarRight.length) return;

        var $selectAll = $('#select_all');
        var $unselectAll = $('#unselect_all');
        if (!$selectAll.length && !$unselectAll.length) return;

        // Place before the columns dropdown toggle if present; otherwise append at end
        var $columnsBtn = $toolbarRight.find('button.dropdown-toggle').first();

        if ($selectAll.length && !$selectAll.data('moved-to-columns')) {
            if ($columnsBtn.length) {
                $selectAll.insertBefore($columnsBtn);
            } else {
                $toolbarRight.append($selectAll);
            }
            $selectAll.data('moved-to-columns', true);
        }
        if ($unselectAll.length && !$unselectAll.data('moved-to-columns')) {
            if ($columnsBtn.length) {
                $unselectAll.insertBefore($columnsBtn);
            } else {
                $toolbarRight.append($unselectAll);
            }
            $unselectAll.data('moved-to-columns', true);
        }
    }

    // Apply on initial render and on table updates
    $('#books-table')
        .on('post-body.bs.table load-success.bs.table page-change.bs.table sort.bs.table search.bs.table', function () {
            applyBooksTableCheckboxTooltips();
            moveBooksToolbarButtons();
        });
    // Also try once after DOM ready in case table is already present
    applyBooksTableCheckboxTooltips();
    moveBooksToolbarButtons();


    $("#books-table").on("check.bs.table check-all.bs.table uncheck.bs.table uncheck-all.bs.table",
        function (e, rowsAfter, rowsBefore) {
            var rows = rowsAfter;

            if (e.type === "uncheck-all") {
                selections = [];
            } else {
                var ids = $.map(!$.isArray(rows) ? [rows] : rows, function (row) {
                    return row.id;
                });

                var func = $.inArray(e.type, ["check", "check-all"]) > -1 ? "union" : "difference";
                selections = window._[func](selections, ids);
            }
            if (selections.length >= 2) {
                $("#merge_books").removeClass("disabled");
                $("#merge_books").attr("aria-disabled", false);
            } else {
                $("#merge_books").addClass("disabled");
                $("#merge_books").attr("aria-disabled", true);
            }
            if (selections.length >= 1) {
                $("#delete_selected_books").removeClass("disabled");
                $("#delete_selected_books").attr("aria-disabled", false);

                $("#read_selected_books").removeClass("disabled");
                $("#read_selected_books").attr("aria-disabled", false);

                $("#unread_selected_books").removeClass("disabled");
                $("#unread_selected_books").attr("aria-disabled", false);

                $("#edit_selected_books").removeClass("disabled");
                $("#edit_selected_books").attr("aria-disabled", false);
                $("#add_to_shelf_btn").removeClass("disabled");
                $("#add_to_shelf_btn").attr("aria-disabled", false);
            } else {
                $("#delete_selected_books").addClass("disabled");
                $("#delete_selected_books").attr("aria-disabled", true);

                $("#read_selected_books").addClass("disabled");
                $("#read_selected_books").attr("aria-disabled", true);

                $("#unread_selected_books").addClass("disabled");
                $("#unread_selected_books").attr("aria-disabled", true);

                $("#edit_selected_books").addClass("disabled");
                $("#edit_selected_books").attr("aria-disabled", true);
                $("#add_to_shelf_btn").addClass("disabled");
                $("#add_to_shelf_btn").attr("aria-disabled", true);
            }
            if (selections.length < 1) {
                $("#table_xchange").addClass("disabled");
                $("#table_xchange").attr("aria-disabled", true);
            } else {
                $("#table_xchange").removeClass("disabled");
                $("#table_xchange").attr("aria-disabled", false);

            }

        });

    // Small block to initialize the state of the author/title sort inputs in metadata form
    {
        let checkA = $('#autoupdate_authorsort').prop('checked');
        $('#author_sort_input').prop('disabled', checkA);
        let checkT = $('#autoupdate_titlesort').prop('checked');
        $('#title_sort_input').prop('disabled', checkT);
    }

    // Disable/enable author and title sort input in respect to auto-update title/author sort being checked on or not
    $("#autoupdate_authorsort").on('change', function(event) {
            let checkA = $('#autoupdate_authorsort').prop('checked');
            $('#author_sort_input').prop('disabled', checkA);
    })

    $("#autoupdate_titlesort").on('change', function(event) {
            let checkT = $('#autoupdate_titlesort').prop('checked');
            $('#title_sort_input').prop('disabled', checkT);
    })
    /////

    function batchOutcome(response) {
        var out = {ok: !!(response && response.success === true),
                   retry: [], reasons: [], summary: ""};
        var results = (response && $.isArray(response.results)) ? response.results : [];
        var summary = (response && response.summary) || {};
        var parts = [];
        if (summary.succeeded) { parts.push(summary.succeeded + " succeeded"); }
        if (summary.failed) { parts.push(summary.failed + " failed"); }
        if (summary.skipped) { parts.push(summary.skipped + " skipped"); }
        $.each(results, function (i, r) {
            if (!r || r.status !== "succeeded") {
                out.retry.push(r ? r.book_id : null);
                if (r && r.message) { out.reasons.push("#" + r.book_id + ": " + r.message); }
            }
        });
        if (!results.length && response && response.msg) { parts.push(String(response.msg)); }
        out.summary = parts.join(", ");
        return out;
    }

    function showBatchOutcome(response) {
        var info = batchOutcome(response);
        var region = $("#batch-results");
        region.empty();
        var box = $("<div></div>")
            .attr("class", info.ok ? "alert alert-success" : "alert alert-danger")
            .attr("role", info.ok ? "status" : "alert");
        $("<p></p>").text(info.summary ||
            (info.ok ? "Done." : "The request failed; check the library before retrying"))
            .appendTo(box);
        if (info.reasons.length) {
            var list = $("<ul></ul>");
            $.each(info.reasons, function (i, m) { $("<li></li>").text(m).appendTo(list); });
            list.appendTo(box);
            $("<p></p>").text("Books that failed or were skipped stay selected; " +
                              "confirm the action again to retry them.").appendTo(box);
        }
        region.append(box);
        if (window.lilyFlash && info.summary) {
            window.lilyFlash(info.summary, info.ok ? "success" : "danger");
        }
        return info;
    }

    function reselectAfterRefresh(ids) {
        var table = $("#books-table");
        var wanted = (ids || []).slice();
        selections = [];
        table.bootstrapTable("uncheckAll");
        if (wanted.length) {
            table.one("load-success.bs.table", function () {
                table.bootstrapTable("uncheckAll");
                table.bootstrapTable("checkBy", {field: "id", values: wanted});
                selections = wanted.slice();
            });
        }
        table.bootstrapTable("refresh");
    }

    function ajaxErrorResult(xhr) {
        var msg = (xhr && xhr.responseJSON &&
                   (xhr.responseJSON.msg || xhr.responseJSON.message || xhr.responseJSON.reason)) ||
            "Request failed; check the library before retrying";
        showBatchOutcome({success: false, results: [], summary: {}, msg: msg});
        if (window.lilyFlash) { window.lilyFlash(msg, "danger"); }
    }

    window.LilyBatch = {
        outcome: batchOutcome,
        mergeRetrySelection: function (targetId, info) {
            return info.retry.length ? [targetId].concat(info.retry) : [];
        }
    };

    function renderTitleList(target, items) {
        $(target).empty();
        $.each(items, function (i, item) {
            $("<p>").append($("<span>").text("- " + item)).appendTo(target);
        });
    }

    $("#merge_confirm").click(function() {
        var mergeIds = selections.slice();
        $.ajax({
            method:"post",
            contentType: "application/json; charset=utf-8",
            dataType: "json",
            url: window.location.pathname + "/../ajax/mergebooks",
            data: JSON.stringify({"Merge_books":mergeIds}),
            success: function success(response) {
                var info = showBatchOutcome(response);
                reselectAfterRefresh(window.LilyBatch.mergeRetrySelection(mergeIds[0], info));
            },
            error: ajaxErrorResult
        });
    });

    $("#merge_books").click(function(event) {
        if ($(this).hasClass("disabled")) {
            event.stopPropagation()
        } else {
            $('#mergeModal').modal("show");
        }
        $.ajax({
            method:"post",
            contentType: "application/json; charset=utf-8",
            dataType: "json",
            url: window.location.pathname + "/../ajax/simulatemerge",
            data: JSON.stringify({"Merge_books":selections}),
            success: function success(booTitles) {
                var conflicts = booTitles.conflicts || [];
                $("#merge_confirm").prop("disabled", conflicts.length > 0);
                if (conflicts.length) {
                    $('#merge_from').empty();
                    $.each(conflicts, function (i, conflict) {
                        $("<p>").append($("<span class='text-danger'>").text(conflict))
                            .appendTo("#merge_from");
                    });
                } else {
                    renderTitleList('#merge_from', booTitles.from);
                }
                $("#merge_to").text("- " + booTitles.to);

            },
            error: ajaxErrorResult
        });
    });

    $("#edit_selected_books").click(function(event) {
        if ($(this).hasClass("disabled")) {
            event.stopPropagation()
        } else {
            $('#edit_selected_modal').modal("show");
        }
    });

    $("#edit_selected_confirm").click(function(event) {
        $.ajax({
            method:"post",
            contentType: "application/json; charset=utf-8",
            dataType: "json",
            url: window.location.pathname + "/../ajax/editselectedbooks",
            data: JSON.stringify({
                "selections": selections,
                "title": $("#title_input").val(),
                "title_sort": $("#title_sort_input").val(),
                "author_sort": $("#author_sort_input").val(),
                "authors": $("#authors_input").val(),
                "categories": $("#categories_input").val(),
                "series": $("#series_input").val(),
                "languages": $("#languages_input").val(),
                "publishers": $("#publishers_input").val(),
                "comments": $("#comments_input").val().toString(),
                "checkA": $("#autoupdate_authorsort").prop('checked').toString()
            }),
            error: ajaxErrorResult,
            success: function success(response) {
                var info = showBatchOutcome(response);
                reselectAfterRefresh(info.retry);
                if (!response || response.success !== true) {
                    return;
                }

                $("#title_input").val("");
                $("#title_sort_input").val("");
                $("#author_sort_input").val("");
                $("#authors_input").val("");
                $("#categories_input").val("");
                $("#series_input").val("");
                $("#languages_input").val("");
                $("#publishers_input").val("");
                $("#comments_input").val("");
            }
        });
    });

    $(document).on('click', '#delete_selected_books', function(event) {
        if ($(this).hasClass("disabled")) {
            event.stopPropagation()
        } else {
            $('#delete_selected_modal').modal("show");
        }
        $.ajax({
            method:"post",
            contentType: "application/json; charset=utf-8",
            dataType: "json",
            url: window.location.pathname + "/../ajax/displayselectedbooks",
            data: JSON.stringify({"selections":selections}),
            error: ajaxErrorResult,
            success: function success(booTitles) {
                renderTitleList('#display-delete-selected-books', booTitles.books);

            }
        });
    });

    $(document).on('click', '#delete_selected_confirm', function(event) {
        $.ajax({
            method:"post",
            contentType: "application/json; charset=utf-8",
            dataType: "json",
            url: window.location.pathname + "/../ajax/deleteselectedbooks",
            data: JSON.stringify({"selections":selections}),
            success: function success(response) {
                reselectAfterRefresh(showBatchOutcome(response).retry);
            },
            error: ajaxErrorResult
        });
    });

    $(document).on('click', '#read_selected_books', function(event) {
        if ($(this).hasClass("disabled")) {
            event.stopPropagation()
        } else {
            $('#read_selected_modal').modal("show");
        }
        $.ajax({
            method:"post",
            contentType: "application/json; charset=utf-8",
            dataType: "json",
            url: window.location.pathname + "/../ajax/displayselectedbooks",
            data: JSON.stringify({"selections":selections}),
            error: ajaxErrorResult,
            success: function success(booTitles) {
                renderTitleList('#display-read-selected-books', booTitles.books);

            }
        });
    });

    $(document).on('click', '#read_selected_confirm', function(event) {
        $.ajax({
            method:"post",
            contentType: "application/json; charset=utf-8",
            dataType: "json",
            url: window.location.pathname + "/../ajax/readselectedbooks",
            data: JSON.stringify({"selections":selections, "markAsRead": true}),
            success: function success(response) {
                reselectAfterRefresh(showBatchOutcome(response).retry);
            },
            error: ajaxErrorResult
        });
    });

    $(document).on('click', '#unread_selected_books', function(event) {
        if ($(this).hasClass("disabled")) {
            event.stopPropagation()
        } else {
            $('#unread_selected_modal').modal("show");
        }
        $.ajax({
            method:"post",
            contentType: "application/json; charset=utf-8",
            dataType: "json",
            url: window.location.pathname + "/../ajax/displayselectedbooks",
            data: JSON.stringify({"selections":selections}),
            error: ajaxErrorResult,
            success: function success(booTitles) {
                renderTitleList('#display-unread-selected-books', booTitles.books);

            }
        });
    });

    $(document).on('click', '#unread_selected_confirm', function(event) {
        $.ajax({
            method:"post",
            contentType: "application/json; charset=utf-8",
            dataType: "json",
            url: window.location.pathname + "/../ajax/readselectedbooks",
            data: JSON.stringify({"selections":selections, "markAsRead": false}),
            success: function success(response) {
                reselectAfterRefresh(showBatchOutcome(response).retry);
            },
            error: ajaxErrorResult
        });
    });


    $("#table_xchange").click(function() {
        $.ajax({
            method:"post",
            contentType: "application/json; charset=utf-8",
            dataType: "json",
            url: window.location.pathname + "/../ajax/xchange",
            data: JSON.stringify({"xchange":selections}),
            success: function success(response) {
                reselectAfterRefresh(showBatchOutcome(response).retry);
            },
            error: ajaxErrorResult
        });
    });

    var column = [];
    $("#books-table > thead > tr > th").each(function() {
        var element = {};
        if ($(this).attr("data-edit")) {
            element = {
                editable: {
                    mode: "inline",
                    emptytext: "<span class='glyphicon glyphicon-plus'></span>",
                    success: function (response, __) {
                        if (!response.success) return response.msg;
                        return {newValue: response.newValue};
                    },
                    params: function (params) {
                        params.checkA = $('#autoupdate_authorsort').prop('checked');
                        params.checkT = $('#autoupdate_titlesort').prop('checked');
                        return params
                    }
                }
            };
            if ($(this).attr("data-editable-type") == "wysihtml5") {
                element.editable.display = shorten_html;
            }
            var validateText = $(this).attr("data-edit-validate");
            if (validateText) {
                element.editable.validate = function (value) {
                    if ($.trim(value) === "") return validateText;
                };
            }
        }
        column.push(element);
    });

    $("#books-table").bootstrapTable({
        sidePagination: "server",
        pageList: "[10, 25, 50, 100]",
        queryParams: queryParams,
        pagination: true,
        paginationLoop: false,
        paginationDetailHAlign: "right",
        paginationHAlign: "left",
        idField: "id",
        uniqueId: "id",
        search: true,
        showColumns: true,
        searchAlign: "left",
        showSearchButton : true,
        searchOnEnterKey: true,
    checkboxHeader: false,
        maintainMetaData: true,
    clickToSelect: true,
        responseHandler: responseHandler,
        columns: column,
        formatNoMatches: function () {
            return "";
        },
        // eslint-disable-next-line no-unused-vars
        onEditableSave: function (field, row, oldvalue, $el) {
            if ($.inArray(field, [ "title", "sort" ]) !== -1 && $('#autoupdate_titlesort').prop('checked')
                || $.inArray(field, [ "authors", "author_sort" ]) !== -1 && $('#autoupdate_authorsort').prop('checked')) {
                $.ajax({
                    method:"get",
                    dataType: "json",
                    url: window.location.pathname + "/../ajax/sort_value/" + field + "/" + row.id,
                    success: function success(data) {
                        var key = Object.keys(data)[0];
                        $("#books-table").bootstrapTable("updateCellByUniqueId", {
                            id: row.id,
                            field: key,
                            value: data[key]
                        });
                    }
                });
            }
        },
        // eslint-disable-next-line no-unused-vars
        onColumnSwitch: function (field, checked) {
            var visible = $("#books-table").bootstrapTable("getVisibleColumns");
            var hidden  = $("#books-table").bootstrapTable("getHiddenColumns");
            var st = "";
            visible.forEach(function(item) {
                st += "\"" + item.field + "\":\"" + "true" + "\",";
            });
            hidden.forEach(function(item) {
                st += "\"" + item.field + "\":\"" + "false" + "\",";
            });
            st = st.slice(0, -1);
            $.ajax({
                method:"post",
                contentType: "application/json; charset=utf-8",
                dataType: "json",
                url: window.location.pathname + "/../ajax/table_settings",
                data: "{" + st + "}",
            });
        },
    });

});

/* Function for deleting books */
function EbookActions (value, row) {
    return [
        "<div class=\"book-remove\" data-toggle=\"modal\" data-target=\"#deleteModal\" data-ajax=\"1\" data-delete-id=\"" + row.id + "\" title=\"Remove\">",
        "<i class=\"glyphicon glyphicon-trash\"></i>",
        "</div>"
    ].join("");
}

/* Function for keeping checked rows */
function responseHandler(res) {
    $.each(res.rows, function (i, row) {
        row.state = $.inArray(row.id, selections) !== -1;
    });
    return res;
}

function bookCheckboxFormatter(value, row){
    if (value)
        return '<input type="checkbox" class="chk" data-pk="' + row.id + '" data-name="' + this.field + '" checked onchange="BookCheckboxChange(this, ' + row.id + ', \'' + this.name + '\')">';
    else
        return '<input type="checkbox" class="chk" data-pk="' + row.id + '" data-name="' + this.field + '" onchange="BookCheckboxChange(this, ' + row.id + ', \'' + this.name + '\')">';
}


function ratingFormatter(value, row) {
    if (value == 0) {
        return "";
    }
    return (value/2);
}


/* Server replies to the books table go to the page's one live region (lily.js). */
function handleListServerResponse (data) {
    if (!jQuery.isEmptyObject(data) && window.lilyFlash) {
        data.forEach(function(item) {
            window.lilyFlash(item.message, item.type);
        });
    }
}

function BookCheckboxChange(checkbox, userId, field) {
    var value = checkbox.checked ? "True" : "False";
    var element = checkbox;
    $.ajax({
        method: "post",
        url: getPath() + "/ajax/editbooks/" + field,
        data: {"pk": userId, "value": value},
        error: function(data) {
            element.checked = !element.checked;
            handleListServerResponse([{type:"danger", message:data.responseText}])
        },
        success: handleListServerResponse
    });
}


function queryParams(params)
{
    params.state = JSON.stringify(selections);
    return params;
}

function shorten_html(value, response) {
    if(value) {
        $(this).html("[...]");
        // value.split('\n').slice(0, 2).join("") +
    }
}

    $("#add_to_shelf_btn").click(function(event) {
        if ($(this).hasClass("disabled")) {
            event.stopPropagation();
        } else {
            // Clear previous selections in dropdown and any error messages
            $('#shelf_selection_dropdown').val('');
            $('#addToShelfModal .modal-body .alert-danger').remove();
            $('#addToShelfModal').modal("show");
        }
    });

    $("#confirm_add_to_shelf_btn").click(function() {
        var selectedShelfId = $("#shelf_selection_dropdown").val();
        var bookIds = selections; // 'selections' is already maintained globally

        // Clear previous error messages
        $('#addToShelfModal .modal-body .alert-danger').remove();

        if (!selectedShelfId) {
            $('#shelf_selection_dropdown').after('<div class="alert alert-danger">Please select a shelf.</div>');
            return;
        }
        if (bookIds.length === 0) {
            $('#shelf_selection_dropdown').after('<div class="alert alert-danger">No books selected.</div>');
            return;
        }

        var csrfToken = $('input[name="csrf_token"]').val();

        $.ajax({
            method: "POST",
            url: getPath() + "/shelf/add_selected_to_shelf",
            contentType: "application/json; charset=utf-8",
            dataType: "json",
            data: JSON.stringify({
                "shelf_id": parseInt(selectedShelfId),
                "book_ids": bookIds
            }),
            headers: {
                'X-CSRFToken': csrfToken
            },
            success: function(response) {
                $('#addToShelfModal').modal("hide");
                var messages = [];
                if (response.status === 'success' || (response.status === 'partial_success' && response.added_count > 0)) {
                    messages.push({type: "success", message: response.message || `Successfully added ${response.added_count} books.`});
                }
                if (response.errors && response.errors.length > 0) {
                    messages.push({type: "warning", message: "Some books could not be added: " + response.errors.join(", ")});
                }
                if (messages.length >0) {
                    handleListServerResponse(messages);
                }
                $("#books-table").bootstrapTable("uncheckAll");
            },
            error: function(xhr, status, error) {
                $('#addToShelfModal .modal-body .alert-danger').remove(); // Remove old errors just in case
                var errorMsg = "Error adding books to shelf.";
                if (xhr.responseJSON && xhr.responseJSON.message) {
                    errorMsg = xhr.responseJSON.message;
                } else if (xhr.responseJSON && xhr.responseJSON.errors && xhr.responseJSON.errors.length > 0) {
                    errorMsg = xhr.responseJSON.errors.join("<br>");
                } else if (xhr.responseText) {
                    try {
                        var errResponse = JSON.parse(xhr.responseText);
                        errorMsg = errResponse.message || errResponse.error || errorMsg;
                    } catch (e) {
                        // xhr.responseText might not be JSON
                        errorMsg = xhr.responseText.substring(0,200); // Show a snippet
                    }
                }
                $('#shelf_selection_dropdown').after('<div class="alert alert-danger">' + errorMsg + '</div>');
            }
        });
    });
