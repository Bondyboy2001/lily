/* Lily – duplicate books notice
 * Copyright (C) 2024-2026 Calibre-Web Automated contributors
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * The sidebar badge is the everyday signal. layout.html embeds the current status
 * (render_template.py), so a page load costs no request. /duplicates/status is polled only
 * while a scan is pending, and stops as soon as it is done. The dialog opens only when the
 * number of duplicate groups has grown since this browser last acknowledged it (a new scan
 * found new groups), never on first sight and never during a snooze. "Remind me later"
 * snoozes it for 7 days; closing it acknowledges the current count.
 */

(function() {
    'use strict';

    const SEEN_KEY = 'lily-duplicates-seen-count';
    const SNOOZE_KEY = 'lily-duplicates-snooze-until';
    const SNOOZE_MS = 7 * 24 * 60 * 60 * 1000;
    // While a scan is pending: after 5 s, then backing off to once a minute, for at most 12
    // checks per page (~8 minutes); the next page load picks it up from there.
    const POLL_FIRST_MS = 5000;
    const POLL_MAX_MS = 60000;
    const POLL_MAX_ATTEMPTS = 12;

    let currentDuplicateCount = 0;
    let lastStatus = null;
    let pollAttempts = 0;
    let pollTimer = null;
    let pollInFlight = false;

    // localStorage can be missing or throw (private mode, blocked site data); the notice then
    // simply stays a badge.
    function readNumber(key) {
        try {
            const parsed = parseInt(localStorage.getItem(key), 10);
            return Number.isFinite(parsed) ? parsed : null;
        } catch (e) {
            return null;
        }
    }

    function writeNumber(key, value) {
        try {
            localStorage.setItem(key, String(value));
        } catch (e) { /* storage unavailable */ }
    }

    function isSnoozed() {
        const until = readNumber(SNOOZE_KEY);
        return until !== null && Date.now() < until;
    }

    function acknowledge(count) {
        writeNumber(SEEN_KEY, count || 0);
    }

    /**
     * Update the duplicate count badge in the sidebar
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

    function fetchDuplicateStatus() {
        const basePath = (typeof getPath === 'function') ? getPath() : '';
        return fetch(basePath + '/duplicates/status', {
            method: 'GET',
            headers: { 'Accept': 'application/json' },
            credentials: 'same-origin'
        })
        .then(response => response.json())
        .catch(error => {
            console.error('[Lily duplicates] Could not fetch status:', error);
            return { success: false, count: 0, preview: [], enabled: false };
        });
    }

    function scanPending(data) {
        return !!(data.stale || data.needs_scan) && !data.needs_full_scan;
    }

    function scheduleNextPoll() {
        if (pollTimer || pollInFlight || pollAttempts >= POLL_MAX_ATTEMPTS) {
            return;
        }
        const wait = Math.min(POLL_FIRST_MS * Math.pow(2, pollAttempts), POLL_MAX_MS);
        pollTimer = setTimeout(() => {
            pollTimer = null;
            if (document.hidden) {
                scheduleNextPoll();  // check again later rather than spend a request on a hidden tab
                return;
            }
            pollAttempts += 1;
            pollInFlight = true;
            fetchDuplicateStatus()
                .then(data => {
                    pollInFlight = false;
                    handleStatusResponse(data);
                })
                .catch(() => { pollInFlight = false; });
        }, wait);
    }

    function startStatusPolling() {
        scheduleNextPoll();
    }

    function stopStatusPolling() {
        if (pollTimer) {
            clearTimeout(pollTimer);
            pollTimer = null;
        }
        pollAttempts = POLL_MAX_ATTEMPTS;
    }

    function isModalActive() {
        const modal = document.getElementById('duplicate-notification-modal');
        return modal && modal.classList.contains('active');
    }

    function isDuplicatesPage() {
        return window.location.pathname.replace(/\/+$/, '').endsWith('/duplicates');
    }

    /**
     * Open the dialog when there are more groups than this browser has acknowledged.
     */
    function maybeShowModal(data) {
        const count = Number(data.count || 0);
        const seen = readNumber(SEEN_KEY);
        if (seen === null || isDuplicatesPage() || count < seen) {
            // First sight, on the duplicates page itself, or groups were resolved: just remember.
            acknowledge(count);
            return;
        }
        if (!data.enabled || count === 0 || count === seen || isSnoozed() || isModalActive()) {
            return;
        }
        showNotificationModal(data);
    }

    function showNotificationModal(data) {
        const countBadge = document.getElementById('duplicate-notification-count');
        if (countBadge) {
            countBadge.textContent = data.count;
        }
        const previewList = document.getElementById('duplicate-notification-preview');
        if (previewList) {
            previewList.textContent = '';
            (data.preview || []).forEach(item => {
                const li = document.createElement('li');
                li.className = 'duplicate-preview-item';
                const title = document.createElement('strong');
                title.textContent = item.title;
                const detail = document.createElement('small');
                detail.textContent = item.author + ' - ' + item.count + ' copies';
                li.appendChild(title);
                li.appendChild(detail);
                previewList.appendChild(li);
            });
        }
        const modal = document.getElementById('duplicate-notification-modal');
        const backdrop = document.getElementById('duplicate-notification-backdrop');
        if (modal && backdrop) {
            backdrop.classList.add('active');
            modal.classList.add('active');
            modal.focus();
        }
    }

    function handleStatusResponse(data) {
        if (!data || !data.success) {
            return;
        }
        lastStatus = data;
        if (!window.CWADuplicateScanActive) {
            updateBadge(Number(data.count || 0));
        }
        document.dispatchEvent(new CustomEvent('cwa:duplicates-status', { detail: data }));

        if (scanPending(data)) {
            startStatusPolling();
            return;
        }
        stopStatusPolling();
        if (!data.needs_full_scan) {
            maybeShowModal(data);
        }
    }

    function hideNotificationModal() {
        const modal = document.getElementById('duplicate-notification-modal');
        const backdrop = document.getElementById('duplicate-notification-backdrop');
        if (modal && backdrop) {
            modal.classList.remove('active');
            backdrop.classList.remove('active');
        }
    }

    // Closing acknowledges what is there now; "Remind me later" only snoozes.
    function dismissModal() {
        if (!isModalActive()) {
            return;
        }
        acknowledge(lastStatus ? Number(lastStatus.count || 0) : currentDuplicateCount);
        hideNotificationModal();
    }

    function snoozeModal() {
        writeNumber(SNOOZE_KEY, Date.now() + SNOOZE_MS);
        hideNotificationModal();
    }

    function initializeEventListeners() {
        const closeBtn = document.getElementById('duplicate-notification-close');
        if (closeBtn) {
            closeBtn.addEventListener('click', dismissModal);
        }
        const remindBtn = document.getElementById('duplicate-notification-remind');
        if (remindBtn) {
            remindBtn.addEventListener('click', snoozeModal);
        }
        const backdrop = document.getElementById('duplicate-notification-backdrop');
        if (backdrop) {
            backdrop.addEventListener('click', dismissModal);
        }
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                dismissModal();
            }
        });
    }

    function init() {
        // The dialog is only rendered for admins and editors.
        if (!document.getElementById('duplicate-notification-modal')) {
            return;
        }
        initializeEventListeners();
        const bootstrapData = window.cwaDuplicateBootstrap;
        if (bootstrapData && typeof bootstrapData === 'object' && Object.keys(bootstrapData).length) {
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
    }

    // Used by duplicates.js (badge while a scan runs) and anything that wants a fresh status.
    window.CWADuplicates = {
        updateBadge: updateBadge,
        fetchStatus: fetchDuplicateStatus,
        hideModal: hideNotificationModal
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
