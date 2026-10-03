/* This file is part of the Calibre-Web-Automated (CWA) duplicate management system
 *    Copyright (C) 2024 CWA Contributors
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

/* Duplicate book management functionality */

$(document).ready(function() {
    var selectedBooks = [];
    // The group whose "Merge selected" opened the merge dialog; merging stays inside it
    var mergeBookIds = [];
    // The books the delete dialog is about: the selection, or the one row whose trash was clicked
    var deleteBookIds = [];

    // Get CSRF token
    var csrfToken = $('input[name="csrf_token"]').val();
    
    function updateSelectionCount() {
        var count = selectedBooks.length;
        if (count === 0) {
            $('#selection_count').text('No books selected');
            $('#delete_selected').addClass('disabled').attr('aria-disabled', true);
        } else {
            $('#selection_count').text(count === 1 ? '1 book selected' : count + ' books selected');
            $('#delete_selected').removeClass('disabled').attr('aria-disabled', false);
        }
        // Each group's merge needs two of its own books checked
        $('.duplicate-group').each(function() {
            var enough = groupSelection($(this)).length > 1;
            $(this).find('.merge-selected-btn').toggleClass('disabled', !enough).attr('aria-disabled', !enough);
        });
    }

    function groupSelection(group) {
        return group.find('.book-checkbox:checked').map(function() { return parseInt($(this).val(), 10); }).get();
    }
    
    function updateBookItemVisuals() {
        $('.book-item').each(function() {
            var checkbox = $(this).find('.book-checkbox');
            if (checkbox.is(':checked')) {
                $(this).addClass('selected');
            } else {
                $(this).removeClass('selected');
            }
        });
    }

    // Outcomes are flash messages in the page's live region (lily.js), not dialogs.
    var FLASH_KEY = 'lily-duplicates-flash';
    function notify(message, tone) {
        if (window.lilyFlash) { window.lilyFlash(message, tone || 'danger'); }
    }
    // After a change that rebuilds the groups, reload and say what happened on the fresh page.
    function notifyAfterReload(message) {
        try { sessionStorage.setItem(FLASH_KEY, message); } catch (e) { /* storage blocked: reload quietly */ }
        window.location.reload();
    }
    try {
        var pending = sessionStorage.getItem(FLASH_KEY);
        if (pending) { sessionStorage.removeItem(FLASH_KEY); notify(pending, 'success'); }
    } catch (e) { /* storage blocked */ }

    function showResolutionSuccess(data) {
        notifyAfterReload('Resolved ' + data.resolved_count + ' groups: kept ' + data.kept_count +
                          ' books and deleted ' + data.deleted_count + '.');
    }

    function showResolutionError(message) {
        notify('Couldn\'t apply the resolution. ' + message);
    }

    function applyBatchOutcome(response) {
        var outcome = {failed: [], succeeded: [], reasons: []};
        var results = (response && $.isArray(response.results)) ? response.results : [];
        $.each(results, function (i, r) {
            if (r && r.status === 'succeeded') {
                outcome.succeeded.push(r.book_id);
            } else {
                outcome.failed.push(r ? r.book_id : null);
                if (r && r.message) { outcome.reasons.push('#' + r.book_id + ': ' + r.message); }
            }
        });
        var ok = !!(response && response.success === true);
        var summary = (response && response.summary) || {};
        var parts = [];
        if (summary.succeeded) { parts.push(summary.succeeded + ' succeeded'); }
        if (summary.failed) { parts.push(summary.failed + ' failed'); }
        if (summary.skipped) { parts.push(summary.skipped + ' skipped'); }
        var region = $('#batch-results');
        region.empty();
        var box = $('<div></div>')
            .attr('class', ok ? 'alert alert-success' : 'alert alert-danger')
            .attr('role', ok ? 'status' : 'alert');
        $('<p></p>').text(parts.join(', ') || (ok ? 'Done.' : 'The request failed.')).appendTo(box);
        if (outcome.reasons.length) {
            var list = $('<ul></ul>');
            $.each(outcome.reasons, function (i, m) { $('<li></li>').text(m).appendTo(list); });
            list.appendTo(box);
            $('<p></p>').text('Books that failed stay selected; retry the action to try them again.').appendTo(box);
        }
        region.append(box);
        return outcome;
    }

    function dropSucceededBooks(ids) {
        $.each(ids, function (i, id) {
            var checkbox = $('.book-checkbox[value="' + id + '"]');
            checkbox.prop('checked', false);
            checkbox.closest('.book-item').remove();
            var idx = selectedBooks.indexOf(String(id));
            if (idx > -1) { selectedBooks.splice(idx, 1); }
        });
        updateSelectionCount();
        updateBookItemVisuals();
    }

    function ajaxErrorMessage(xhr, fallback) {
        return (xhr && xhr.responseJSON &&
                (xhr.responseJSON.msg || xhr.responseJSON.message || xhr.responseJSON.reason)) || fallback;
    }

    // Handle individual checkbox changes  
    $(document).on('change', '.book-checkbox', function() {
        var bookId = $(this).val();
        
        if ($(this).is(':checked')) {
            if (selectedBooks.indexOf(bookId) === -1) {
                selectedBooks.push(bookId);
            }
        } else {
            var index = selectedBooks.indexOf(bookId);
            if (index > -1) {
                selectedBooks.splice(index, 1);
            }
        }
        updateSelectionCount();
        updateBookItemVisuals();
    });
    
    // Select All button - intelligently select duplicates to delete
    $('#select_all').click(function() {
        selectedBooks = [];
        
        // For each duplicate group, select all books except the first one
        $('.duplicate-group').each(function() {
            var checkboxes = $(this).find('.book-checkbox');
            
            // Skip the first checkbox (index 0) and check the rest
            checkboxes.each(function(index) {
                if (index > 0) {
                    $(this).prop('checked', true);
                    selectedBooks.push($(this).val());
                } else {
                    // Ensure the first book is unchecked
                    $(this).prop('checked', false);
                }
            });
        });
        
        updateSelectionCount();
        updateBookItemVisuals();
    });
    
    // Select None button
    $('#select_none').click(function() {
        $('.book-checkbox').prop('checked', false);
        selectedBooks = [];
        updateSelectionCount();
        updateBookItemVisuals();
    });
    
    // Merge Selected button (delegated for multiple buttons)
    $(document).on('click', '.merge-selected-btn', function(event) {
        if ($(this).hasClass('disabled')) {
            event.stopPropagation();
        } else {
            // Only this group's checked books, newest first as listed; the first is kept
            var bookIds = groupSelection($(this).closest('.duplicate-group'));
            if (bookIds.length < 2) {
                notify('Select at least two books in this group to merge.', 'warning');
                return;
            }
            mergeBookIds = bookIds;

            // Use a relative URL to respect base paths
            var relativeUrl = window.location.pathname + "/../ajax/displayselectedbooks";

            $('#merge_selected_modal').modal('show');

            // Show list of books to be merged
            var ajaxData = {"selections": bookIds};

            $.ajax({
                method: 'post',
                contentType: 'application/json; charset=utf-8',
                dataType: 'json',
                url: relativeUrl,
                data: JSON.stringify(ajaxData),
                beforeSend: function(xhr) {
                    // Add CSRF token as header
                    if (csrfToken) {
                        xhr.setRequestHeader('X-CSRFToken', csrfToken);
                    }
                },
                success: function(response) {
                    $('#display-merge-target-book').empty();
                    $('#display-merge-source-books').empty();

                    // First book is the target (kept)
                    if (response.books && response.books.length > 0) {
                        $('<div class="dup-modal-item">').text(response.books[0]).appendTo('#display-merge-target-book');

                        // Rest are source books (merged and deleted)
                        for (var i = 1; i < response.books.length; i++) {
                            $('<div class="dup-modal-item">').text(response.books[i]).appendTo('#display-merge-source-books');
                        }
                    }
                },
                error: function(xhr, status, error) {
                    notify('Couldn\'t load the books to merge (error ' + xhr.status + '). Reload the page and try again.');
                }
            });
        }
    });

    // Delete Selected button
    $('#delete_selected').click(function(event) {
        if ($(this).hasClass('disabled')) {
            event.stopPropagation();
        } else {
            // Check if any books are actually selected
            if (selectedBooks.length === 0) {
                notify('Select the books to delete first.', 'warning');
                return;
            }
            
            // Use a relative URL to respect base paths
            var relativeUrl = window.location.pathname + "/../ajax/displayselectedbooks";
            
            $('#delete_selected_modal').modal('show');
            
            // Convert book IDs to integers
            var bookIds = selectedBooks.map(function(id) { return parseInt(id, 10); });
            deleteBookIds = bookIds;
            
            // Show list of books to be deleted
            var ajaxData = {"selections": bookIds};
            
            $.ajax({
                method: "post",
                contentType: "application/json; charset=utf-8",
                dataType: "json",
                url: relativeUrl,
                data: JSON.stringify(ajaxData),
                beforeSend: function(xhr) {
                    // Add CSRF token as header
                    if (csrfToken) {
                        xhr.setRequestHeader('X-CSRFToken', csrfToken);
                    }
                },
                success: function(response) {
                    $('#display-delete-selected-books').empty();
                    $.each(response.books, function(i, item) {
                        $('<div class="dup-modal-item">').text(item).appendTo('#display-delete-selected-books');
                    });
                },
                error: function(xhr, status, error) {
                    notify('Couldn\'t load the books to delete (error ' + xhr.status + '). Reload the page and try again.');
                }
            });
        }
    });

    // A row's trash deletes that copy alone, after the same confirmation; the selection is left as it is
    $(document).on('click', '.dup-delete-book', function() {
        var row = $(this).closest('.book-item');
        deleteBookIds = [parseInt($(this).data('book-id'), 10)];
        $('#display-delete-selected-books').empty().append(
            $('<div class="dup-modal-item">').text(
                $.trim(row.find('.book-title-link').text()) + ' (' + deleteBookIds[0] + ')'));
        $('#delete_selected_modal').modal('show');
    });

    // Confirm merge
    $('#merge_selected_confirm').click(function() {
        var mergeUrl = window.location.pathname + "/../ajax/mergebooks";

        // First book in array is target, rest are merged into it
        var mergeData = {"Merge_books": mergeBookIds};

        $.ajax({
            method: 'post',
            contentType: 'application/json; charset=utf-8',
            dataType: 'json',
            url: mergeUrl,
            data: JSON.stringify(mergeData),
            beforeSend: function(xhr) {
                // Add CSRF token as header
                if (csrfToken) {
                    xhr.setRequestHeader('X-CSRFToken', csrfToken);
                }
            },
            success: function(response) {
                $('#merge_selected_modal').modal('hide');
                var outcome = applyBatchOutcome(response);
                dropSucceededBooks(outcome.succeeded);
                if (response && response.success === true) {
                    notifyAfterReload('Merged the selected books.');
                } else {
                    notify('Some books couldn\'t be merged and are still selected. The reasons are listed above the results.');
                }
            },
            error: function(xhr, status, error) {
                $('#merge_selected_modal').modal('hide');
                notify(ajaxErrorMessage(xhr, 'The merge request failed. Check the library, then try again.'));
            }
        });
    });
    
    // Confirm delete
    $('#delete_selected_confirm').click(function() {
        var deleteUrl = window.location.pathname + "/../ajax/deleteselectedbooks";
        
        var deleteData = {"selections": deleteBookIds};
        
        $.ajax({
            method: "post",
            contentType: "application/json; charset=utf-8",
            dataType: "json",
            url: deleteUrl,
            data: JSON.stringify(deleteData),
            beforeSend: function(xhr) {
                // Add CSRF token as header
                if (csrfToken) {
                    xhr.setRequestHeader('X-CSRFToken', csrfToken);
                }
            },
            success: function(response) {
                $('#delete_selected_modal').modal('hide');
                var outcome = applyBatchOutcome(response);
                dropSucceededBooks(outcome.succeeded);
                if (response && response.success === true) {
                    notifyAfterReload(deleteBookIds.length === 1 ? 'Deleted the book.' : 'Deleted the selected books.');
                } else {
                    notify('Some books couldn\'t be deleted and are still selected. The reasons are listed above the results.');
                }
            },
            error: function(xhr, status, error) {
                $('#delete_selected_modal').modal('hide');
                notify(ajaxErrorMessage(xhr, 'The delete request failed. Check the library, then try again.'));
            }
        });
    });
    
    // Dismiss a group: it leaves the page and its books leave the selection
    $(document).on('click', '.dismiss-duplicate-btn', function(e) {
        e.preventDefault();
        var btn = $(this);
        var groupContainer = btn.closest('.duplicate-group');
        btn.prop('disabled', true);
        $.ajax({
            url: duplicateScanEndpoint('/duplicates/dismiss/' + encodeURIComponent(btn.data('group-hash'))),
            type: 'POST',
            headers: {'X-CSRFToken': csrfToken},
            dataType: 'json',
            success: function(response) {
                if (!response.success) {
                    btn.prop('disabled', false);
                    notify('Couldn\'t dismiss this group. Reload the page and try again.');
                    return;
                }
                groupContainer.find('.book-checkbox').each(function() {
                    var idx = selectedBooks.indexOf($(this).val());
                    if (idx > -1) { selectedBooks.splice(idx, 1); }
                });
                groupContainer.fadeOut(300, function() {
                    groupContainer.remove();
                    updateSelectionCount();
                    if (!$('.duplicate-group').length) { window.location.reload(); }
                });
                if (window.CWADuplicates && window.CWADuplicates.updateBadge) {
                    window.CWADuplicates.updateBadge(response.count);
                }
            },
            error: function() {
                btn.prop('disabled', false);
                notify('Couldn\'t dismiss this group. Reload the page and try again.');
            }
        });
    });

    var duplicateScanPollTimer = null;
    var duplicateScanTaskId = null;
    window.CWADuplicateScanActive = false;

    function duplicateScanEndpoint(path) {
        if (typeof getPath === 'function') {
            return getPath() + path;
        }
        return path;
    }

    function parseTaskProgress(task) {
        var progress = parseInt(task.progress, 10);
        if (isNaN(progress)) {
            return 0;
        }
        return Math.max(0, Math.min(100, progress));
    }

    function isDuplicateScanTask(task) {
        if (duplicateScanTaskId && String(task.task_id) === String(duplicateScanTaskId)) {
            return true;
        }
        return String(task.taskMessage || '').toLowerCase().indexOf('duplicate scan') !== -1;
    }

    function isRunningDuplicateScanTask(task) {
        return isDuplicateScanTask(task) && task.stat !== 1 && task.stat !== 3 && task.stat !== 4 && task.stat !== 5;
    }

    function setDuplicateScanNotice(task) {
        var progress = parseTaskProgress(task);
        window.CWADuplicateScanActive = true;
        if (window.CWADuplicates && window.CWADuplicates.updateBadge) {
            window.CWADuplicates.updateBadge(0);
        }
        $('#duplicate_index_setup_notice').hide();
        $('#duplicate_results_content').hide();
        $('#no_duplicate_books_message').hide();
        $('#duplicate_scan_results_status').addClass('is-active');
        $('#duplicate_scan_task_title').text('Duplicate Scan Running');
        // The bar shows how far it got; the task's own line ("Building duplicate index: n/N books") isn't shown
        $('#duplicate_scan_task_message').text('You can keep using Lily while it runs.');
        $('#duplicate_scan_task_progress_container').show();
        $('#duplicate_scan_task_link').hide();
        $('#duplicate_scan_task_progress')
            .addClass('active')
            .css('width', progress + '%')
            .attr('aria-valuenow', progress);
        $('#duplicate_scan_task_progress_label').text(progress + '%');
    }

    function showDuplicateScanFinishedNotice() {
        window.CWADuplicateScanActive = false;
        $('#duplicate_index_setup_notice').hide();
        $('#duplicate_results_content').hide();
        $('#no_duplicate_books_message').hide();
        $('#duplicate_scan_results_status').addClass('is-active');
        $('#duplicate_scan_task_title').text('Duplicate Scan Running');
        $('#duplicate_scan_task_message').text('Duplicate scan finished. Updating results...');
        $('#duplicate_scan_task_progress_container').show();
        $('#duplicate_scan_task_link').hide();
        $('#duplicate_scan_task_progress')
            .removeClass('active')
            .css('width', '100%')
            .attr('aria-valuenow', 100);
        $('#duplicate_scan_task_progress_label').text('100%');
        setTimeout(function() {
            window.location.reload();
        }, 500);
    }

    function showDuplicateResultsAvailableNotice(count) {
        if (duplicateScanWasActive) {
            return;
        }
        if (!$('#no_duplicate_books_message').length) {
            return;
        }
        if ($('#duplicate_scan_results_status').hasClass('is-active')) {
            if ($('#duplicate_scan_task_title').text() === 'Duplicate Books Found') {
                $('#duplicate_scan_task_message').text(
                    'Found ' + count + ' duplicate ' + (count === 1 ? 'group' : 'groups') + '. Refresh the page to review them.'
                );
            }
            return;
        }
        $('#duplicate_results_content').hide();
        $('#no_duplicate_books_message').hide();
        $('#duplicate_scan_results_status').addClass('is-active');
        $('#duplicate_scan_task_title').text('Duplicate Books Found');
        $('#duplicate_scan_task_message').text(
            'Found ' + count + ' duplicate ' + (count === 1 ? 'group' : 'groups') + '. Refresh the page to review them.'
        );
        $('#duplicate_scan_task_progress_container').hide();
        $('#duplicate_scan_task_link')
            .attr('href', window.location.href)
            .text('Refresh Page')
            .show();
    }

    // A scan queued by the page render (the one-time index baseline) may finish
    // before this script's first poll, so treat it as already seen running.
    var autoQueuedAttr = $('#duplicate_scan_results_status').data('scan-auto-queued');
    var duplicateScanWasActive = autoQueuedAttr === true || autoQueuedAttr === 'true';

    var duplicateScanPollInFlight = false;
    function pollDuplicateScanTask() {
        // Avoid overlapping requests, and skip interval ticks while the tab is hidden
        if (duplicateScanPollInFlight || (document.hidden && duplicateScanPollTimer)) {
            return;
        }
        duplicateScanPollInFlight = true;
        $.getJSON(duplicateScanEndpoint('/ajax/emailstat'), function(tasks) {
            var runningTask = null;
            $.each(tasks || [], function(index, task) {
                if (isRunningDuplicateScanTask(task)) {
                    runningTask = task;
                    return false;
                }
            });

            if (runningTask) {
                duplicateScanWasActive = true;
                duplicateScanTaskId = runningTask.task_id;
                setDuplicateScanNotice(runningTask);
                if (!duplicateScanPollTimer) {
                    duplicateScanPollTimer = setInterval(pollDuplicateScanTask, 2000);
                }
            } else if (duplicateScanWasActive) {
                showDuplicateScanFinishedNotice();
                clearInterval(duplicateScanPollTimer);
                duplicateScanPollTimer = null;
            }
        }).always(function() {
            duplicateScanPollInFlight = false;
        });
    }

    pollDuplicateScanTask();

    // "Scan for duplicates" on the empty state: queue a full scan, then follow it like any
    // other running scan (the page reloads with the new results when it finishes).
    $('#scan_duplicates').on('click', function() {
        var btn = $(this);
        btn.prop('disabled', true);
        $.ajax({
            url: duplicateScanEndpoint('/duplicates/trigger-scan'),
            method: 'POST',
            headers: {
                'X-CSRFToken': csrfToken
            },
            dataType: 'json',
            success: function(data) {
                if (data && data.success) {
                    duplicateScanWasActive = true;
                    duplicateScanTaskId = data.task_id || null;
                    setDuplicateScanNotice({ progress: 0 });
                    if (!duplicateScanPollTimer) {
                        duplicateScanPollTimer = setInterval(pollDuplicateScanTask, 2000);
                    }
                } else {
                    notify((data && data.message) || 'Couldn\'t start the scan. Try again.');
                    btn.prop('disabled', false);
                }
            },
            error: function(xhr) {
                notify(ajaxErrorMessage(xhr, 'Couldn\'t start the scan. Try again.'));
                btn.prop('disabled', false);
            }
        });
    });

    document.addEventListener('cwa:duplicates-status', function(event) {
        var data = event.detail || {};
        if (data.count > 0 && !data.needs_scan && !data.needs_full_scan) {
            showDuplicateResultsAvailableNotice(Number(data.count || 0));
        }
    });

    // Apply resolves exactly the groups the last preview showed, with its strategy
    var previewedGroupHashes = null;
    function forgetPreview() {
        previewedGroupHashes = null;
        $('#execute_resolution').addClass('disabled').attr('aria-disabled', true);
    }
    $('#resolution_strategy').on('change', forgetPreview);

    // Auto-resolution preview
    $('#preview_resolution').on('click', function() {
        var strategy = $('#resolution_strategy').val();
        var btn = $(this);
        btn.prop('disabled', true);
        btn.html('<span class="glyphicon glyphicon-refresh glyphicon-spin"></span> Loading...');
        
        $.ajax({
            url: duplicateScanEndpoint('/duplicates/preview-resolution'),
            method: 'POST',
            contentType: 'application/json',
            headers: {
                'X-CSRFToken': csrfToken
            },
            data: JSON.stringify({ strategy: strategy }),
            success: function(data) {
                if (data.success && data.preview) {
                    showResolutionPreview(data);
                    previewedGroupHashes = data.preview.map(function(group) { return group.group_hash; });
                    var any = previewedGroupHashes.length > 0;
                    $('#execute_resolution').toggleClass('disabled', !any).attr('aria-disabled', !any);
                } else {
                    forgetPreview();
                    notify('Couldn\'t preview the resolution. ' + (data.errors || []).join(' '));
                }
            },
            error: function(xhr) {
                forgetPreview();
                notify(ajaxErrorMessage(xhr, 'Couldn\'t preview the resolution. Try again.'));
            },
            complete: function() {
                btn.prop('disabled', false);
                btn.html('<span class="glyphicon glyphicon-eye-open"></span> Preview');
            }
        });
    });
    
    // Execute resolution
    $('#execute_resolution').on('click', function() {
        if ($(this).hasClass('disabled') || !previewedGroupHashes) return;

        $('#execute_resolution_strategy_name').text($('#resolution_strategy option:selected').text());
        $('#execute_resolution_modal').modal('show');
    });

    $('#execute_resolution_confirm').on('click', function() {
        var strategy = $('#resolution_strategy').val();
        var btn = $('#execute_resolution');
        btn.addClass('disabled').html('<span class="glyphicon glyphicon-refresh glyphicon-spin"></span> Applying…');
        
        $.ajax({
            url: duplicateScanEndpoint('/duplicates/execute-resolution'),
            method: 'POST',
            contentType: 'application/json',
            headers: {
                'X-CSRFToken': csrfToken
            },
            data: JSON.stringify({ strategy: strategy, group_hashes: previewedGroupHashes }),
            success: function(data) {
                if (data.success) {
                    showResolutionSuccess(data);
                } else {
                    var errors = data.errors || ['Unknown error occurred during resolution'];
                    showResolutionError(errors.join(' '));
                    btn.removeClass('disabled').html('<span class="glyphicon glyphicon-flash"></span> Apply resolution');
                }
            },
            error: function(xhr, status, error) {
                console.error('[CWA Duplicates] Failed to execute resolution:', error);
                var response = xhr.responseJSON || {};
                showResolutionError(response.message || response.error || 'Reload the page and try again.');
                btn.removeClass('disabled').html('<span class="glyphicon glyphicon-flash"></span> Apply resolution');
            }
        });
    });
    
    function showResolutionPreview(data) {
        var html = '<div class="dup-preview-summary">' +
            '<div class="resolution-success-stat"><span class="resolution-success-value">' + data.resolved_count + '</span><span class="resolution-success-label">Groups to resolve</span></div>' +
            '<div class="resolution-success-stat"><span class="resolution-success-value">' + data.kept_count + '</span><span class="resolution-success-label">Books to keep</span></div>' +
            '<div class="resolution-success-stat"><span class="resolution-success-value">' + data.deleted_count + '</span><span class="resolution-success-label">Books to delete</span></div>' +
            '</div>';

        if (data.preview && data.preview.length > 0) {
            data.preview.forEach(function(group) {
                html += '<div class="dup-preview-group">' +
                    '<div class="dup-preview-title">' + escapeHtml(group.title) + ' <span class="dup-preview-author">' + escapeHtml(group.author) + '</span></div>' +
                    '<div class="dup-preview-row">' +
                    '<span class="label label-success">Keep</span> ' +
                    '<span>Book ID ' + group.kept_book_id + '</span> ' +
                    '<span class="dup-preview-meta">Added ' + escapeHtml(group.kept_book_timestamp) + ' · ' + escapeHtml(group.kept_book_formats.join(', ')) + '</span>' +
                    '</div>';

                group.deleted_books_info.forEach(function(book) {
                    html += '<div class="dup-preview-row">' +
                        '<span class="label label-danger">Delete</span> ' +
                        '<span>Book ID ' + book.id + '</span> ' +
                        '<span class="dup-preview-meta">Added ' + escapeHtml(book.timestamp) + ' · ' + escapeHtml(book.formats.join(', ')) + '</span>' +
                        '</div>';
                });

                html += '</div>';
            });
        }

        $('#resolution_preview_body').html(html);
        $('#resolution_preview_modal').modal('show');
    }
    
    function escapeHtml(text) {
        text = String(text == null ? '' : text);
        var map = {
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#039;'
        };
        return text.replace(/[&<>"']/g, function(m) { return map[m]; });
    }
    
    // Initialize
    updateSelectionCount();
    updateBookItemVisuals();
});
