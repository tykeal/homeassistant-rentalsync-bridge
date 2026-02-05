// SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
// SPDX-License-Identifier: Apache-2.0

/**
 * RentalSync Bridge Admin UI JavaScript
 */

// Detect base path for API calls (handles HA ingress proxy)
// Extracts everything before /admin in the path
const getBasePath = () => {
    const path = window.location.pathname;
    // Find /admin in the path and return everything before it
    const adminIndex = path.indexOf('/admin');
    if (adminIndex === -1) {
        return '';
    }
    return path.substring(0, adminIndex);
};

const API_BASE = getBasePath();

// DOM Elements
const elements = {
    statusOverall: document.getElementById('status-overall'),
    statusOAuth: document.getElementById('status-oauth'),
    statusSync: document.getElementById('status-sync'),
    statusListings: document.getElementById('status-listings'),
    oauthForm: document.getElementById('oauth-form'),
    oauthConnected: document.getElementById('oauth-connected'),
    oauthStatusText: document.getElementById('oauth-status-text'),
    authTypeDisplay: document.getElementById('auth-type-display'),
    clientId: document.getElementById('client-id'),
    clientSecret: document.getElementById('client-secret'),
    apiKey: document.getElementById('api-key'),
    accessToken: document.getElementById('access-token'),
    refreshToken: document.getElementById('refresh-token'),
    refreshTokenBtn: document.getElementById('refresh-token-btn'),
    replaceCredentialsBtn: document.getElementById('replace-credentials-btn'),
    syncInterval: document.getElementById('sync-interval'),
    saveSyncBtn: document.getElementById('save-sync-btn'),
    listingsContainer: document.getElementById('listings-container'),
    syncPropertiesBtn: document.getElementById('sync-properties-btn'),
    bulkEnableBtn: document.getElementById('bulk-enable-btn'),
    bulkDisableBtn: document.getElementById('bulk-disable-btn'),
    selectedCount: document.getElementById('selected-count'),
    customFieldsModal: document.getElementById('custom-fields-modal'),
    customFieldsList: document.getElementById('custom-fields-list'),
    addFieldBtn: document.getElementById('add-field-btn'),
    saveFieldsBtn: document.getElementById('save-fields-btn'),
};

let currentListingId = null;
let selectedListings = new Set();

// API Functions
async function fetchAPI(endpoint, options = {}) {
    const response = await fetch(`${API_BASE}${endpoint}`, {
        ...options,
        headers: {
            'Content-Type': 'application/json',
            ...options.headers,
        },
    });

    if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.detail || `HTTP ${response.status}`);
    }

    return response.json();
}

// Status Functions
async function loadStatus() {
    try {
        const status = await fetchAPI('/api/status');

        elements.statusOverall.textContent = status.status;
        elements.statusOverall.className = `badge ${status.status}`;

        elements.statusOAuth.textContent = status.oauth.connected ? 'Connected' : 'Not Connected';
        elements.statusOAuth.className = `badge ${status.oauth.connected ? 'healthy' : 'warning'}`;

        elements.statusSync.textContent = status.sync.last_sync
            ? new Date(status.sync.last_sync).toLocaleString()
            : 'Never';

        elements.statusListings.textContent = `${status.listings.enabled}/${status.listings.total}`;
    } catch (error) {
        console.error('Failed to load status:', error);
        elements.statusOverall.textContent = 'Error';
        elements.statusOverall.className = 'badge error';
    }
}

// OAuth Functions
async function loadOAuthStatus() {
    try {
        const status = await fetchAPI('/api/oauth/status');

        if (status.connected) {
            elements.oauthForm.classList.add('hidden');
            elements.oauthConnected.classList.remove('hidden');
            elements.oauthStatusText.textContent = 'Connected to Cloudbeds';
            if (status.auth_type === 'api_key') {
                elements.authTypeDisplay.textContent = 'Using API Key authentication';
                elements.refreshTokenBtn.classList.add('hidden');
            } else {
                elements.authTypeDisplay.textContent = 'Using OAuth authentication';
                elements.refreshTokenBtn.classList.remove('hidden');
            }
        } else if (status.configured) {
            elements.oauthForm.classList.add('hidden');
            elements.oauthStatusText.textContent = 'Configured but not connected. Please refresh token.';
            elements.oauthConnected.classList.remove('hidden');
        } else {
            elements.oauthForm.classList.remove('hidden');
            elements.oauthConnected.classList.add('hidden');
            elements.oauthStatusText.textContent = 'Enter your Cloudbeds credentials.';
        }
    } catch (error) {
        console.error('Failed to load OAuth status:', error);
        elements.oauthStatusText.textContent = 'Failed to load authentication status';
    }
}

async function saveOAuthCredentials(event) {
    event.preventDefault();

    const apiKey = elements.apiKey.value.trim();
    const accessToken = elements.accessToken.value.trim();

    // Validate that either API key or access token is provided
    if (!apiKey && !accessToken) {
        alert('Please provide either an API Key or OAuth Access Token');
        return;
    }

    try {
        const payload = {
            client_id: elements.clientId.value,
            client_secret: elements.clientSecret.value,
        };

        if (apiKey) {
            payload.api_key = apiKey;
        }
        if (accessToken) {
            payload.access_token = accessToken;
            payload.refresh_token = elements.refreshToken.value.trim() || null;
        }

        await fetchAPI('/api/oauth/configure', {
            method: 'POST',
            body: JSON.stringify(payload),
        });

        await loadOAuthStatus();
        await loadStatus();
    } catch (error) {
        alert(`Failed to save credentials: ${error.message}`);
    }
}

async function refreshToken() {
    try {
        elements.refreshTokenBtn.disabled = true;
        elements.refreshTokenBtn.textContent = 'Refreshing...';

        await fetchAPI('/api/oauth/refresh', { method: 'POST' });

        await loadOAuthStatus();
        await loadStatus();
    } catch (error) {
        alert(`Failed to refresh token: ${error.message}`);
    } finally {
        elements.refreshTokenBtn.disabled = false;
        elements.refreshTokenBtn.textContent = 'Refresh Token';
    }
}

/**
 * Show the credentials form to allow replacing existing credentials.
 */
function showCredentialsForm() {
    // Clear the form fields
    elements.clientId.value = '';
    elements.clientSecret.value = '';
    elements.apiKey.value = '';
    elements.accessToken.value = '';
    elements.refreshToken.value = '';

    // Show the form and hide the connected state
    elements.oauthForm.classList.remove('hidden');
    elements.oauthConnected.classList.add('hidden');
}

// Listings Functions

/**
 * Format sync status for display.
 * @param {Object} listing - The listing object
 * @returns {string} HTML for sync status display
 */
function formatSyncStatus(listing) {
    if (!listing.enabled) {
        return '';
    }

    let status = '';
    if (listing.last_sync_at) {
        const syncDate = new Date(listing.last_sync_at);
        const formattedDate = formatDateTime(syncDate);
        if (listing.last_sync_error) {
            status = `<span class="sync-status error" title="${escapeHtml(listing.last_sync_error)}">⚠ Sync failed ${formattedDate}</span>`;
        } else {
            status = `<span class="sync-status success">✓ Synced ${formattedDate}</span>`;
        }
    } else {
        status = '<span class="sync-status pending">Never synced</span>';
    }

    // Add config update timestamp for concurrent update awareness (T103)
    if (listing.updated_at) {
        const updateDate = new Date(listing.updated_at);
        const formattedUpdate = formatDateTime(updateDate);
        status += `<span class="config-status" title="Last configuration change">⏱ Updated ${formattedUpdate}</span>`;
    }

    return status;
}

/**
 * Format a date as localized date/time string in local timezone.
 * @param {Date} date - The date to format
 * @returns {string} Formatted date/time string in local timezone
 */
function formatDateTime(date) {
    // Ensure we're working with a valid date
    if (isNaN(date.getTime())) {
        return 'Invalid date';
    }
    return date.toLocaleString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: true
    });
}

// Room Management Functions

/**
 * Render rooms HTML for a listing.
 * @param {number} listingId - The listing ID
 * @param {string} listingSlug - The listing slug
 * @param {Array} rooms - Array of room objects
 * @returns {string} HTML for rooms section
 */
function renderRoomsSection(listingId, listingSlug, rooms) {
    if (!rooms || rooms.length === 0) {
        return '';
    }

    const roomsHtml = rooms.map(room => {
        const roomUrl = `/ical/${escapeHtml(listingSlug)}/${escapeHtml(room.ical_url_slug)}.ics`;
        const safeRoomId = escapeHtml(String(room.id));
        return `
            <div class="room-item" data-room-id="${safeRoomId}">
                <div class="room-info">
                    <h5>${escapeHtml(room.room_name)}</h5>
                    ${room.room_type_name ? `<div class="room-type">${escapeHtml(room.room_type_name)}</div>` : ''}
                    <div class="room-ical-url">
                        <code>${escapeHtml(roomUrl)}</code>
                        <button class="copy-btn" data-action="copy-room-url" data-url="${escapeHtml(roomUrl)}">Copy</button>
                    </div>
                    <div class="room-slug-display" data-slug="${escapeHtml(room.ical_url_slug)}">
                        <code>Slug: ${escapeHtml(room.ical_url_slug)}</code>
                        <button class="edit-btn" data-action="edit-room-slug">Edit</button>
                    </div>
                </div>
                <div class="room-actions">
                    <label class="toggle-switch">
                        <input type="checkbox" data-action="toggle-room" ${room.enabled ? 'checked' : ''}>
                        <span class="toggle-slider"></span>
                    </label>
                </div>
            </div>
        `;
    }).join('');

    return `
        <div class="rooms-section">
            <div class="rooms-header" data-action="toggle-rooms">
                <span class="rooms-toggle">▶</span>
                <h4>${rooms.length} Room${rooms.length !== 1 ? 's' : ''}</h4>
            </div>
            <div class="rooms-list">
                ${roomsHtml}
            </div>
        </div>
    `;
}

/**
 * Toggle room list expansion.
 * @param {HTMLElement} listingItem - The listing item element
 */
function toggleRoomsList(listingItem) {
    const toggle = listingItem.querySelector('.rooms-toggle');
    const roomsList = listingItem.querySelector('.rooms-list');

    if (toggle && roomsList) {
        toggle.classList.toggle('expanded');
        roomsList.classList.toggle('expanded');
    }
}

/**
 * Copy room iCal URL to clipboard.
 * @param {string} url - The URL to copy
 * @param {HTMLButtonElement} button - The button element
 */
async function copyRoomUrl(url, button) {
    try {
        // Get the full URL including origin
        const fullUrl = window.location.origin + url;

        // Check if clipboard API is available (requires HTTPS or localhost)
        if (!navigator.clipboard) {
            throw new Error('Clipboard API requires HTTPS');
        }

        await navigator.clipboard.writeText(fullUrl);

        const originalText = button.textContent;
        button.textContent = 'Copied!';
        button.classList.add('copied');

        setTimeout(() => {
            button.textContent = originalText;
            button.classList.remove('copied');
        }, 2000);
    } catch (error) {
        console.error('Failed to copy URL:', error);
        const message = error.message.includes('HTTPS')
            ? 'Copy failed: Clipboard requires HTTPS'
            : 'Failed to copy URL to clipboard';
        alert(message);
    }
}

/**
 * Toggle room enable/disable.
 * @param {number} roomId - The room ID
 * @param {boolean} enabled - Whether to enable or disable
 */
async function toggleRoom(roomId, enabled) {
    // Validate roomId to prevent selector injection and path traversal
    const validRoomId = validatePositiveInt(roomId);
    if (!validRoomId) {
        console.error('Invalid roomId:', roomId);
        return;
    }

    // Find and disable toggle to prevent concurrent operations
    const toggle = document.querySelector(
        `.room-item[data-room-id="${validRoomId}"] input[data-action="toggle-room"]`
    );
    if (toggle) toggle.disabled = true;

    try {
        await fetchAPI(`/api/rooms/${validRoomId}`, {
            method: 'PATCH',
            body: JSON.stringify({ enabled }),
        });

        // No need to reload - the toggle is already updated
        console.log(`Room ${validRoomId} ${enabled ? 'enabled' : 'disabled'}`);
    } catch (error) {
        console.error('Failed to toggle room:', error);
        alert(`Failed to ${enabled ? 'enable' : 'disable'} room: ${error.message}`);
        // Reload to restore correct state
        await loadListings();
    } finally {
        if (toggle) toggle.disabled = false;
    }
}

/**
 * Enter edit mode for room slug.
 * @param {HTMLElement} roomItem - The room item element
 */
function enterRoomSlugEdit(roomItem) {
    const slugDisplay = roomItem.querySelector('.room-slug-display');
    if (!slugDisplay) return;

    const currentSlug = slugDisplay.dataset.slug;
    const roomId = roomItem.dataset.roomId;

    // Validate roomId before using in HTML
    if (!validatePositiveInt(roomId)) {
        console.error('Invalid roomId:', roomId);
        return;
    }

    const editorHtml = `
        <div class="room-slug-editor" data-original-slug="${escapeHtml(currentSlug)}">
            <input type="text" value="${escapeHtml(currentSlug)}" data-room-id="${escapeHtml(roomId)}">
            <button class="save-btn" data-action="save-room-slug">Save</button>
            <button class="cancel-btn" data-action="cancel-room-slug">Cancel</button>
        </div>
    `;

    slugDisplay.innerHTML = editorHtml;

    // Focus the input
    const input = slugDisplay.querySelector('input');
    if (input) {
        input.focus();
        input.select();
    }

    // Attach event handlers
    attachRoomSlugEditorHandlers(slugDisplay);
}

/**
 * Attach event handlers to room slug editor.
 * @param {HTMLElement} slugDisplay - The slug display element
 */
function attachRoomSlugEditorHandlers(slugDisplay) {
    const saveBtn = slugDisplay.querySelector('[data-action="save-room-slug"]');
    const cancelBtn = slugDisplay.querySelector('[data-action="cancel-room-slug"]');
    const input = slugDisplay.querySelector('input');

    if (saveBtn) {
        saveBtn.addEventListener('click', async () => {
            const roomId = input.dataset.roomId;
            const newSlug = input.value.trim();
            await saveRoomSlug(roomId, newSlug);
        });
    }

    if (cancelBtn) {
        cancelBtn.addEventListener('click', () => {
            cancelRoomSlugEdit(slugDisplay);
        });
    }

    if (input) {
        input.addEventListener('keydown', async (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                const roomId = input.dataset.roomId;
                const newSlug = input.value.trim();
                await saveRoomSlug(roomId, newSlug);
            } else if (e.key === 'Escape') {
                e.preventDefault();
                cancelRoomSlugEdit(slugDisplay);
            }
        });
    }
}

/**
 * Save room slug changes.
 * @param {number} roomId - The room ID
 * @param {string} newSlug - The new slug value
 */
async function saveRoomSlug(roomId, newSlug) {
    // Validate roomId to prevent selector injection and path traversal
    const validRoomId = validatePositiveInt(roomId);
    if (!validRoomId) {
        console.error('Invalid roomId:', roomId);
        return;
    }

    // Validate slug format - must start/end with alphanumeric, no consecutive hyphens
    // NOTE: This pattern must match SLUG_PATTERN in src/api/rooms.py
    if (!/^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/.test(newSlug)) {
        alert('Slug must start and end with a letter or number, and contain only lowercase letters, numbers, and hyphens');
        return;
    }

    if (/--/.test(newSlug)) {
        alert('Slug cannot contain consecutive hyphens');
        return;
    }

    // Validate slug length
    if (newSlug.length > 100) {
        alert('Slug must be 100 characters or less');
        return;
    }

    // Find and disable buttons to prevent concurrent operations
    const editor = document.querySelector(
        `.room-item[data-room-id="${validRoomId}"] .room-slug-editor`
    );
    const saveBtn = editor?.querySelector('[data-action="save-room-slug"]');
    const cancelBtn = editor?.querySelector('[data-action="cancel-room-slug"]');
    const input = editor?.querySelector('input');

    if (saveBtn) saveBtn.disabled = true;
    if (cancelBtn) cancelBtn.disabled = true;
    if (input) input.disabled = true;

    try {
        await fetchAPI(`/api/rooms/${validRoomId}`, {
            method: 'PATCH',
            body: JSON.stringify({ ical_url_slug: newSlug }),
        });

        // Reload listings to show updated slug in all places
        await loadListings();
    } catch (error) {
        console.error('Failed to update room slug:', error);
        alert(`Failed to update slug: ${error.message}`);
        // Re-enable on error (success reloads the page anyway)
        if (saveBtn) saveBtn.disabled = false;
        if (cancelBtn) cancelBtn.disabled = false;
        if (input) input.disabled = false;
    }
}

/**
 * Cancel room slug edit mode.
 * @param {HTMLElement} slugDisplay - The slug display element
 */
function cancelRoomSlugEdit(slugDisplay) {
    const editor = slugDisplay.querySelector('.room-slug-editor');
    if (!editor) return;

    const originalSlug = editor.dataset.originalSlug;
    const inputEl = editor.querySelector('input');
    if (!inputEl) return;
    const roomId = inputEl.dataset.roomId;

    slugDisplay.innerHTML = `
        <code>Slug: ${escapeHtml(originalSlug)}</code>
        <button class="edit-btn" data-action="edit-room-slug">Edit</button>
    `;

    // Re-attach event handler
    const editBtn = slugDisplay.querySelector('[data-action="edit-room-slug"]');
    if (editBtn) {
        editBtn.addEventListener('click', () => {
            const roomItem = slugDisplay.closest('.room-item');
            enterRoomSlugEdit(roomItem);
        });
    }
}

async function loadListings() {
    try {
        const data = await fetchAPI('/api/listings');
        // Load rooms for all listings
        const listingsWithRooms = await Promise.all(
            data.listings.map(async (listing) => {
                try {
                    const roomsData = await fetchAPI(`/api/listings/${listing.id}/rooms`);
                    return { ...listing, rooms: roomsData.rooms || [] };
                } catch (error) {
                    console.error(`Failed to load rooms for listing ${listing.id}:`, error);
                    return { ...listing, rooms: [] };
                }
            })
        );
        renderListings(listingsWithRooms);
    } catch (error) {
        console.error('Failed to load listings:', error);
        elements.listingsContainer.innerHTML = '<p class="error">Failed to load listings</p>';
    }
}

/**
 * Sync properties from Cloudbeds to the local database.
 */
async function syncPropertiesFromCloudbeds() {
    const btn = elements.syncPropertiesBtn;
    const originalText = btn.textContent;

    try {
        btn.disabled = true;
        btn.textContent = 'Syncing...';

        const result = await fetchAPI('/api/listings/sync-properties', { method: 'POST' });

        btn.textContent = '✓ Done';
        setTimeout(() => {
            btn.textContent = originalText;
            btn.disabled = false;
        }, 2000);

        // Show result and refresh listings
        const roomsMsg = result.rooms_created !== undefined ? `\nRooms Created: ${result.rooms_created}, Rooms Updated: ${result.rooms_updated}` : '';
        alert(`${result.message}\nProperties Created: ${result.created}, Updated: ${result.updated}${roomsMsg}`);
        await loadListings();
    } catch (error) {
        btn.textContent = originalText;
        btn.disabled = false;
        alert(`Failed to sync: ${error.message}`);
    }
}

function renderListings(listings) {
    if (listings.length === 0) {
        elements.listingsContainer.innerHTML = '<p class="loading">No listings found. Click "Sync Rooms from Cloudbeds" to populate.</p>';
        updateBulkButtons();
        return;
    }

    const html = listings.map(listing => {
        // Coerce to number for consistent Set operations and data-attribute comparisons
        const id = Number(listing.id);
        // Coerce to boolean for safe attribute interpolation
        const enabled = Boolean(listing.enabled);
        const syncStatus = formatSyncStatus(listing);
        const roomsSection = renderRoomsSection(id, listing.ical_url_slug || '', listing.rooms || []);

        return `
        <div class="listing-item${selectedListings.has(id) ? ' selected' : ''}" data-id="${id}" data-enabled="${enabled}">
            <input type="checkbox" class="listing-checkbox" data-action="select"
                   ${selectedListings.has(id) ? 'checked' : ''}>
            <div class="listing-info">
                <h3>${escapeHtml(listing.name)}</h3>
                ${enabled && listing.ical_url_slug
                    ? `<span class="ical-url">/ical/${escapeHtml(listing.ical_url_slug)}.ics</span>`
                    : '<span class="ical-url">Not enabled</span>'}
                ${syncStatus}
                ${roomsSection}
            </div>
            <div class="listing-actions">
                ${enabled ? '<button class="btn secondary" data-action="sync">Sync Now</button>' : ''}
                <button class="btn secondary" data-action="fields">Fields</button>
                <label class="toggle-switch">
                    <input type="checkbox" data-action="toggle" ${listing.enabled ? 'checked' : ''}>
                    <span class="toggle-slider"></span>
                </label>
            </div>
        </div>
    `}).join('');

    elements.listingsContainer.innerHTML = html;
    attachListingEventHandlers();
    updateBulkButtons();
}

function attachListingEventHandlers() {
    // Use event delegation for listing actions
    elements.listingsContainer.querySelectorAll('.listing-item').forEach(item => {
        const id = Number(item.dataset.id);

        // Selection checkbox
        const selectCheckbox = item.querySelector('[data-action="select"]');
        if (selectCheckbox) {
            selectCheckbox.addEventListener('change', (e) => {
                toggleSelection(id, e.target.checked);
            });
        }

        // Fields button
        const fieldsBtn = item.querySelector('[data-action="fields"]');
        if (fieldsBtn) {
            fieldsBtn.addEventListener('click', () => {
                openCustomFields(id);
            });
        }

        // Sync Now button
        const syncBtn = item.querySelector('[data-action="sync"]');
        if (syncBtn) {
            syncBtn.addEventListener('click', () => {
                syncListing(id, syncBtn);
            });
        }

        // Toggle switch
        const toggleCheckbox = item.querySelector('[data-action="toggle"]');
        if (toggleCheckbox) {
            toggleCheckbox.addEventListener('change', (e) => {
                toggleListing(id, e.target.checked);
            });
        }

        // Room list toggle
        const roomsHeader = item.querySelector('[data-action="toggle-rooms"]');
        if (roomsHeader) {
            roomsHeader.addEventListener('click', () => {
                toggleRoomsList(item);
            });
        }

        // Room-specific actions
        item.querySelectorAll('.room-item').forEach(roomItem => {
            const roomId = Number(roomItem.dataset.roomId);

            // Copy room URL button
            const copyBtn = roomItem.querySelector('[data-action="copy-room-url"]');
            if (copyBtn) {
                copyBtn.addEventListener('click', () => {
                    const url = copyBtn.dataset.url;
                    copyRoomUrl(url, copyBtn);
                });
            }

            // Edit room slug button
            const editSlugBtn = roomItem.querySelector('[data-action="edit-room-slug"]');
            if (editSlugBtn) {
                editSlugBtn.addEventListener('click', () => {
                    enterRoomSlugEdit(roomItem);
                });
            }

            // Toggle room
            const roomToggle = roomItem.querySelector('[data-action="toggle-room"]');
            if (roomToggle) {
                roomToggle.addEventListener('change', (e) => {
                    toggleRoom(roomId, e.target.checked);
                });
            }
        });
    });
}

function toggleSelection(id, selected) {
    if (selected) {
        selectedListings.add(id);
    } else {
        selectedListings.delete(id);
    }
    updateBulkButtons();

    // Update visual state
    const item = document.querySelector(`.listing-item[data-id="${id}"]`);
    if (item) {
        item.classList.toggle('selected', selected);
    }
}

function updateBulkButtons() {
    const count = selectedListings.size;
    elements.selectedCount.textContent = `${count} selected`;
    elements.bulkEnableBtn.disabled = count === 0;
    elements.bulkDisableBtn.disabled = count === 0;
}

function handleBulkResult(result, action) {
    // Handle partial failures - keep failed items selected
    if (result.failed > 0) {
        const failedIds = new Set(
            result.details
                .filter(d => !d.success)
                .map(d => Number(d.id))
        );
        // Keep only failed items selected for retry
        selectedListings.forEach(id => {
            if (!failedIds.has(id)) {
                selectedListings.delete(id);
            }
        });
        const failedNames = result.details
            .filter(d => !d.success)
            .map(d => d.error || `Listing ${d.id}`)
            .join(', ');
        alert(`${action} ${result.updated} listing(s). ${result.failed} failed: ${failedNames}`);
    } else {
        selectedListings.clear();
    }
}

async function bulkEnable() {
    if (selectedListings.size === 0) return;

    try {
        const result = await fetchAPI('/api/listings/bulk', {
            method: 'POST',
            body: JSON.stringify({
                listing_ids: Array.from(selectedListings),
                enabled: true
            })
        });

        handleBulkResult(result, 'Enabled');
        await loadListings();
        await loadStatus();
    } catch (error) {
        alert(`Bulk enable failed: ${error.message}`);
    }
}

async function bulkDisable() {
    if (selectedListings.size === 0) return;

    try {
        const result = await fetchAPI('/api/listings/bulk', {
            method: 'POST',
            body: JSON.stringify({
                listing_ids: Array.from(selectedListings),
                enabled: false
            })
        });

        handleBulkResult(result, 'Disabled');
        await loadListings();
        await loadStatus();
    } catch (error) {
        alert(`Bulk disable failed: ${error.message}`);
    }
}

async function toggleListing(id, enabled) {
    try {
        if (enabled) {
            await fetchAPI(`/api/listings/${id}/enable`, { method: 'POST' });
        } else {
            await fetchAPI(`/api/listings/${id}`, {
                method: 'PUT',
                body: JSON.stringify({ enabled: false }),
            });
        }

        await loadListings();
        await loadStatus();
    } catch (error) {
        alert(`Failed to update listing: ${error.message}`);
        await loadListings();
    }
}

/**
 * Trigger manual sync for a listing.
 * @param {number} id - The listing ID
 * @param {HTMLButtonElement} button - The sync button element
 */
async function syncListing(id, button) {
    const originalText = button.textContent;
    try {
        button.disabled = true;
        button.textContent = 'Syncing...';

        const result = await fetchAPI(`/api/listings/${id}/sync`, { method: 'POST' });

        button.textContent = '✓ Done';
        setTimeout(() => {
            button.textContent = originalText;
            button.disabled = false;
        }, 2000);

        // Refresh to show updated sync status
        await loadListings();
    } catch (error) {
        button.textContent = originalText;
        button.disabled = false;
        alert(`Sync failed: ${error.message}`);
    }
}

// Custom Fields Functions
// Modal state is encapsulated to prevent issues with concurrent modal operations.
// Note: availableFields is used for dropdown population and label suggestions.
// The DOM is the authoritative source of truth for configured fields - saveCustomFields
// reads directly from DOM elements rather than maintaining a separate data structure.
const customFieldsModal = {
    availableFields: {},
    reset() {
        this.availableFields = {};
    }
};

async function openCustomFields(listingId) {
    currentListingId = listingId;
    customFieldsModal.reset();

    try {
        // Fetch both configured fields and available fields
        const [fieldsData, availableData] = await Promise.all([
            fetchAPI(`/api/listings/${listingId}/custom-fields`),
            fetchAPI(`/api/listings/${listingId}/available-custom-fields`)
        ]);

        customFieldsModal.availableFields = availableData.available_fields;
        renderCustomFields(fieldsData.fields);
        elements.customFieldsModal.classList.remove('hidden');
    } catch (error) {
        alert(`Failed to load custom fields: ${error.message}`);
    }
}

function renderCustomFields(fields) {
    if (fields.length === 0) {
        elements.customFieldsList.innerHTML = '<p>No custom fields configured.</p>';
        return;
    }

    const html = fields.map((field, index) => `
        <div class="field-item" data-index="${index}">
            <input type="text" readonly value="${escapeHtml(field.field_name)}" data-field="field_name" class="readonly-field">
            <input type="text" placeholder="Display label" value="${escapeHtml(field.display_label)}" data-field="display_label">
            <label class="toggle-switch">
                <input type="checkbox" ${field.enabled ? 'checked' : ''} data-field="enabled">
                <span class="toggle-slider"></span>
            </label>
            <button class="remove-btn" data-action="remove-field">&times;</button>
        </div>
    `).join('');

    elements.customFieldsList.innerHTML = html;
}

/**
 * Initialize event delegation for custom fields list.
 * Called once during page initialization.
 */
function initCustomFieldsEventDelegation() {
    elements.customFieldsList.addEventListener('click', (e) => {
        // Handle remove button clicks
        const removeBtn = e.target.closest('[data-action="remove-field"]');
        if (removeBtn) {
            removeBtn.closest('.field-item').remove();
        }
    });

    elements.customFieldsList.addEventListener('change', (e) => {
        // Handle field selection changes
        const select = e.target.closest('select[data-field="field_name"]');
        if (select) {
            handleFieldSelection(select);
        }
    });
}

/**
 * Handle field selection from dropdown.
 * @param {HTMLSelectElement} selectElement - The select element
 */
function handleFieldSelection(selectElement) {
    const fieldName = selectElement.value;
    if (!fieldName) return;

    const displayLabel = customFieldsModal.availableFields[fieldName];
    if (displayLabel) {
        const fieldItem = selectElement.closest('.field-item');
        const displayLabelInput = fieldItem.querySelector('[data-field="display_label"]');
        if (displayLabelInput && !displayLabelInput.value) {
            displayLabelInput.value = displayLabel;
        }
    }
}

function addField() {
    const fieldItem = document.createElement('div');
    fieldItem.className = 'field-item';

    // Get list of already configured field names
    const configuredFieldNames = Array.from(
        elements.customFieldsList.querySelectorAll('[data-field="field_name"]')
    ).map(input => input.value).filter(Boolean);

    // Filter available fields to only show unconfigured ones
    const unconfiguredFields = Object.entries(customFieldsModal.availableFields).filter(
        ([fieldName, /* displayLabel - unused in filter */]) => !configuredFieldNames.includes(fieldName)
    );

    // Create dropdown options
    const options = unconfiguredFields.map(
        ([fieldName, displayLabel]) =>
            `<option value="${escapeHtml(fieldName)}">${escapeHtml(displayLabel)}</option>`
    ).join('');

    fieldItem.innerHTML = `
        <select data-field="field_name">
            <option value="">Select field...</option>
            ${options}
        </select>
        <input type="text" placeholder="Display label" data-field="display_label">
        <label class="toggle-switch">
            <input type="checkbox" checked data-field="enabled">
            <span class="toggle-slider"></span>
        </label>
        <button class="remove-btn" data-action="remove-field">&times;</button>
    `;
    elements.customFieldsList.appendChild(fieldItem);
}

async function saveCustomFields() {
    const fieldItems = elements.customFieldsList.querySelectorAll('.field-item');
    const fields = Array.from(fieldItems).map((item, index) => {
        const fieldName = item.querySelector('[data-field="field_name"]').value;
        const displayLabel = item.querySelector('[data-field="display_label"]').value;
        const enabled = item.querySelector('[data-field="enabled"]').checked;

        return {
            field_name: fieldName,
            display_label: displayLabel,
            enabled: enabled,
            sort_order: index,
        };
    }).filter(f => f.field_name && f.display_label);

    try {
        await fetchAPI(`/api/listings/${currentListingId}/custom-fields`, {
            method: 'PUT',
            body: JSON.stringify({ fields }),
        });

        closeModal();
    } catch (error) {
        alert(`Failed to save custom fields: ${error.message}`);
    }
}

function closeModal() {
    elements.customFieldsModal.classList.add('hidden');
    currentListingId = null;
}

// Sync Settings
async function loadSyncSettings() {
    try {
        const settings = await fetchAPI('/api/settings');
        if (elements.syncInterval) {
            elements.syncInterval.value = settings.sync_interval_minutes;
        }
    } catch (error) {
        console.error('Failed to load sync settings:', error);
    }
}

async function saveSyncSettings() {
    const interval = parseInt(elements.syncInterval.value, 10);

    if (isNaN(interval) || interval < 1 || interval > 60) {
        alert('Sync interval must be between 1 and 60 minutes');
        return;
    }

    try {
        const result = await fetchAPI('/api/settings/sync-interval', {
            method: 'PUT',
            body: JSON.stringify({ interval_minutes: interval }),
        });
        alert(result.message);
    } catch (error) {
        alert(`Failed to save sync settings: ${error.message}`);
    }
}

// Utility Functions
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Validate that a value is a positive integer.
 * @param {*} value - The value to validate
 * @returns {number|null} - The validated integer or null if invalid
 */
function validatePositiveInt(value) {
    const num = Number(value);
    if (Number.isInteger(num) && num > 0) {
        return num;
    }
    return null;
}

// Event Listeners
function initEventListeners() {
    elements.oauthForm.addEventListener('submit', saveOAuthCredentials);
    elements.refreshTokenBtn.addEventListener('click', refreshToken);
    elements.replaceCredentialsBtn.addEventListener('click', showCredentialsForm);
    elements.saveSyncBtn.addEventListener('click', saveSyncSettings);
    elements.syncPropertiesBtn.addEventListener('click', syncPropertiesFromCloudbeds);
    elements.addFieldBtn.addEventListener('click', addField);
    elements.saveFieldsBtn.addEventListener('click', saveCustomFields);
    elements.bulkEnableBtn.addEventListener('click', bulkEnable);
    elements.bulkDisableBtn.addEventListener('click', bulkDisable);

    // Modal close handlers
    elements.customFieldsModal.querySelector('.close-btn').addEventListener('click', closeModal);
    elements.customFieldsModal.querySelector('.cancel-btn').addEventListener('click', closeModal);
    elements.customFieldsModal.addEventListener('click', (e) => {
        if (e.target === elements.customFieldsModal) {
            closeModal();
        }
    });
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    initEventListeners();
    initCustomFieldsEventDelegation();
    loadStatus();
    loadOAuthStatus();
    loadListings();
    loadSyncSettings();

    // Refresh status periodically (every 30 seconds)
    setInterval(loadStatus, 30000);

    // Refresh listings periodically to update timestamps (every 60 seconds)
    setInterval(loadListings, 60000);
});

// Export functions for inline handlers
window.openCustomFields = openCustomFields;
window.toggleListing = toggleListing;
