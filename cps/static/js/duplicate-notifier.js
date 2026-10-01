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
    let lastPreviewSignature = '';
    
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
        if (pollTimer) {
            return;
        }
        if (isModalActive()) {
            return;
        }
        pollAttempts = 0;
        pollTimer = setInterval(() => {
            pollAttempts += 1;
            fetchDuplicateStatus().then(handleStatusResponse);
            if (pollAttempts >= POLL_MAX_ATTEMPTS) {
                stopStatusPolling();
            }
        }, POLL_INTERVAL_MS);
    }

    function stopStatusPolling() {
        if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
    }

    // A Bootstrap .modal (layout.html): it handles the backdrop, Escape and focus.
    function isModalActive() {
        const modal = document.getElementById('duplicate-notification-modal');
        return !!modal && modal.classList.contains('in');
    }

    function isDuplicatesPage() {
        return window.location.pathname.replace(/\/+$/, '').endsWith('/duplicates');
    }
    
    /**
     * Show the notification modal
     */
    function showNotificationModal(data) {
        const { count, preview } = data;

        if (isModalActive()) {
            return;
        }
        
        const lastCount = getLastNotifiedCount();
        if (wasNotificationShown() && count <= lastCount) {
            return;
        }
        
        // Update count in modal
        const countBadge = document.getElementById('duplicate-notification-count');
        if (countBadge) {
            countBadge.textContent = count;
        }
        
        // Update preview list
        const previewList = document.getElementById('duplicate-notification-preview');
        if (previewList && preview && preview.length > 0) {
            const signature = preview.map(item => `${item.title}|${item.author}|${item.count}`).join('||');
            if (signature !== lastPreviewSignature) {
                lastPreviewSignature = signature;
                previewList.innerHTML = preview.map(item => `
                    <li class="duplicate-preview-item">
                        <strong>${escapeHtml(item.title)}</strong>
                        <small>${escapeHtml(item.author)} · ${escapeHtml(String(item.count))}×</small>
                    </li>
                `).join('');
            }
        }
        
        const modal = document.getElementById('duplicate-notification-modal');
        if (modal && window.jQuery) {
            // A short pause so the dialog doesn't appear while the page is still drawing
            setTimeout(() => {
                window.jQuery(modal).modal('show');
                markNotificationShown();
                setLastNotifiedCount(count);
            }, 500);
        }
    }

    function handleStatusResponse(data) {
        if (!data || !data.success) {
            return;
        }

        if (!window.CWADuplicateScanActive) {
            updateBadge(data.count);
        }
        document.dispatchEvent(new CustomEvent('cwa:duplicates-status', { detail: data }));

        if (isModalActive()) {
            return;
        }

        if (data.count > 0 && data.enabled && !isDuplicatesPage()) {
            showNotificationModal(data);
            if (isModalActive()) {
                return;
            }
        }

        if ((data.needs_scan || data.stale) && !isModalActive()) {
            startStatusPolling();
            return;
        }

        if (data.enabled) {
            startStatusPolling();
        }
    }
    
    /**
     * Hide the notification modal
     */
    function hideNotificationModal() {
        const modal = document.getElementById('duplicate-notification-modal');
        if (modal && window.jQuery) {
            window.jQuery(modal).modal('hide');
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
        // Check if user has permission (admin or edit)
        const userHasPermission = document.getElementById('duplicate-notification-modal');
        if (!userHasPermission) {
            return; // Modal not rendered, user doesn't have permission
        }
        
        // Fetch initial status once on page load
        // No periodic updates - badge refreshes after ingest operations only
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
        startStatusPolling();

        document.addEventListener('visibilitychange', function() {
            if (!document.hidden) {
                fetchDuplicateStatus().then(handleStatusResponse);
            }
        });
    }
    
    // Expose functions globally for use by other scripts
    window.CWADuplicates = {
        updateBadge: updateBadge,
        fetchStatus: fetchDuplicateStatus,
        hideModal: hideNotificationModal
    };
    
    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
    
})();
