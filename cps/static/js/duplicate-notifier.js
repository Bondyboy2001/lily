/* Lily – Modern Duplicates Notification System
 * Copyright (C) 2024-2025 Calibre-Web Automated contributors
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

(function() {
    'use strict';
    
    const STORAGE_KEY = 'cwa_duplicates_notification_shown';
    const LAST_COUNT_KEY = 'cwa_duplicates_last_count';
    const POLL_INTERVAL_MS = 2500;
    const POLL_MAX_ATTEMPTS = 60; // ~2.5 minutes
    
    let currentDuplicateCount = 0;
    let pollAttempts = 0;
    let pollTimer = null;
    // Polling runs only while a scan is pending, at most POLL_MAX_ATTEMPTS times per page
    let pollingExhausted = false;
    
    /**
     * Check if notification was already shown in this session
     */
    function wasNotificationShown() {
        return sessionStorage.getItem(STORAGE_KEY) === 'true';
    }
    
    /**
     * Mark notification as shown for this session
     */
    function markNotificationShown() {
        sessionStorage.setItem(STORAGE_KEY, 'true');
    }

    function getLastNotifiedCount() {
        const val = sessionStorage.getItem(LAST_COUNT_KEY);
        const parsed = parseInt(val, 10);
        return Number.isFinite(parsed) ? parsed : 0;
    }

    function setLastNotifiedCount(count) {
        sessionStorage.setItem(LAST_COUNT_KEY, String(count || 0));
    }
    
    /**
     * Update the duplicate count badge in sidebar
     */
    function updateBadge(count) {
        currentDuplicateCount = count;
        const badge = document.getElementById('duplicate-count-badge');
        
        if (badge) {
            if (count > 0) {
                badge.textContent = count > 99 ? '99+' : count;
                badge.style.display = 'inline-block';
            } else {
                badge.style.display = 'none';
            }
        }
    }
    
    /**
     * Fetch duplicate status from API
     */
    function fetchDuplicateStatus() {
        const basePath = (typeof getPath === 'function') ? getPath() : '';
        const statusUrl = basePath + '/duplicates/status';
        return fetch(statusUrl, {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json'
            },
            credentials: 'same-origin'
        })
        .then(response => response.json())
        .catch(error => {
            console.error('[CWA Duplicates] Error fetching status:', error);
            return { success: false, count: 0, preview: [], enabled: false };
        });
    }

    function startStatusPolling() {
        if (pollTimer || pollingExhausted) {
            return;
        }
        pollTimer = setInterval(() => {
            if (document.hidden) {
                return;
            }
            pollAttempts += 1;
            if (pollAttempts >= POLL_MAX_ATTEMPTS) {
                pollingExhausted = true;
                stopStatusPolling();
            }
            fetchDuplicateStatus().then(handleStatusResponse);
        }, POLL_INTERVAL_MS);
    }

    function stopStatusPolling() {
        if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
    }

    function noticeConfig() {
        return document.getElementById('duplicate-notice-config');
    }

    function isDuplicatesPage() {
        return window.location.pathname.replace(/\/+$/, '').endsWith('/duplicates');
    }
    
    /**
     * Show the duplicates notice: a dismissible banner above the page, never a dialog over it
     */
    function showNotice(data) {
        const config = noticeConfig();
        const host = document.getElementById('messageContainer');
        if (!config || !host || document.getElementById('duplicate-notice')) {
            return;
        }

        const count = data.count;
        if (wasNotificationShown() && count <= getLastNotifiedCount()) {
            return;
        }

        const text = (count === 1 ? config.dataset.one : config.dataset.many).replace('{count}', String(count));
        const row = document.createElement('div');
        row.className = 'row-fluid';
        row.id = 'duplicate-notice';
        row.innerHTML =
            '<div class="alert alert-warning alert-cwa">' + escapeHtml(text) + ' ' +
            '<a href="' + escapeHtml(config.dataset.url) + '">' + escapeHtml(config.dataset.link) + '</a>' +
            '<button type="button" class="close" data-dismiss="alert" aria-label="' + escapeHtml(config.dataset.close) + '">' +
            '<span aria-hidden="true">&times;</span></button></div>';
        host.appendChild(row);
        markNotificationShown();
        setLastNotifiedCount(count);
    }

    function handleStatusResponse(data) {
        if (!data || !data.success) {
            return;
        }

        if (!window.CWADuplicateScanActive) {
            updateBadge(data.count);
        }
        document.dispatchEvent(new CustomEvent('cwa:duplicates-status', { detail: data }));

        if (data.count > 0 && data.enabled && !isDuplicatesPage()) {
            showNotice(data);
        }

        // Keep checking only while a scan is pending; once the results are in, stop
        if (data.needs_scan || data.stale) {
            startStatusPolling();
        } else {
            stopStatusPolling();
        }
    }
    
    /**
     * Escape HTML to prevent XSS
     */
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    /**
     * Main initialization function
     */
    function init() {
        // The notice's config is only rendered for users who may resolve duplicates
        if (!noticeConfig()) {
            return;
        }

        // The page arrives with the cached status; one fetch refreshes it, and polling
        // only follows while a scan is pending
        const bootstrapData = window.cwaDuplicateBootstrap;
        if (bootstrapData && typeof bootstrapData === 'object') {
            handleStatusResponse({
                success: true,
                enabled: !!bootstrapData.enabled,
                count: Number(bootstrapData.count || 0),
                preview: bootstrapData.preview || [],
                cached: !!bootstrapData.cached,
                stale: !!bootstrapData.stale,
                needs_scan: !!bootstrapData.stale
            });
        }

        fetchDuplicateStatus().then(handleStatusResponse);

        document.addEventListener('visibilitychange', function() {
            if (!document.hidden) {
                fetchDuplicateStatus().then(handleStatusResponse);
            }
        });
    }
    
    // Expose functions globally for use by other scripts
    window.CWADuplicates = {
        updateBadge: updateBadge,
        fetchStatus: fetchDuplicateStatus
    };
    
    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
    
})();
