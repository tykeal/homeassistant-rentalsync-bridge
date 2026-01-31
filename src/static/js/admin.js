// SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
// SPDX-License-Identifier: Apache-2.0

/**
 * RentalSync Bridge Admin UI JavaScript
 */

const API_BASE = '';

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
    syncInterval: document.getElementById('sync-interval'),
    saveSyncBtn: document.getElementById('save-sync-btn'),
    listingsContainer: document.getElementById('listings-container'),
    customFieldsModal: document.getElementById('custom-fields-modal'),
    customFieldsList: document.getElementById('custom-fields-list'),
    addFieldBtn: document.getElementById('add-field-btn'),
    saveFieldsBtn: document.getElementById('save-fields-btn'),
};

let currentListingId = null;

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

// Listings Functions
async function loadListings() {
    try {
        const data = await fetchAPI('/api/listings');
        renderListings(data.listings);
    } catch (error) {
        console.error('Failed to load listings:', error);
        elements.listingsContainer.innerHTML = '<p class="error">Failed to load listings</p>';
    }
}

function renderListings(listings) {
    if (listings.length === 0) {
        elements.listingsContainer.innerHTML = '<p class="loading">No listings found. Sync with Cloudbeds to populate.</p>';
        return;
    }

    const html = listings.map(listing => `
        <div class="listing-item" data-id="${listing.id}">
            <div class="listing-info">
                <h3>${escapeHtml(listing.name)}</h3>
                ${listing.enabled && listing.ical_url_slug
                    ? `<span class="ical-url">/ical/${escapeHtml(listing.ical_url_slug)}.ics</span>`
                    : '<span class="ical-url">Not enabled</span>'}
            </div>
            <div class="listing-actions">
                <button class="btn secondary" onclick="openCustomFields(${listing.id})">Fields</button>
                <label class="toggle-switch">
                    <input type="checkbox" ${listing.enabled ? 'checked' : ''} onchange="toggleListing(${listing.id}, this.checked)">
                    <span class="toggle-slider"></span>
                </label>
            </div>
        </div>
    `).join('');

    elements.listingsContainer.innerHTML = html;
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

// Custom Fields Functions
async function openCustomFields(listingId) {
    currentListingId = listingId;

    try {
        const data = await fetchAPI(`/api/listings/${listingId}/custom-fields`);
        renderCustomFields(data.fields);
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
            <input type="text" placeholder="Field name" value="${escapeHtml(field.field_name)}" data-field="field_name">
            <input type="text" placeholder="Display label" value="${escapeHtml(field.display_label)}" data-field="display_label">
            <label class="toggle-switch" style="flex-shrink: 0;">
                <input type="checkbox" ${field.enabled ? 'checked' : ''} data-field="enabled">
                <span class="toggle-slider"></span>
            </label>
            <button class="remove-btn" onclick="removeField(${index})">&times;</button>
        </div>
    `).join('');

    elements.customFieldsList.innerHTML = html;
}

function addField() {
    const fieldItem = document.createElement('div');
    fieldItem.className = 'field-item';
    fieldItem.innerHTML = `
        <input type="text" placeholder="Field name" data-field="field_name">
        <input type="text" placeholder="Display label" data-field="display_label">
        <label class="toggle-switch" style="flex-shrink: 0;">
            <input type="checkbox" checked data-field="enabled">
            <span class="toggle-slider"></span>
        </label>
        <button class="remove-btn" onclick="this.parentElement.remove()">&times;</button>
    `;
    elements.customFieldsList.appendChild(fieldItem);
}

function removeField(index) {
    const fieldItems = elements.customFieldsList.querySelectorAll('.field-item');
    if (fieldItems[index]) {
        fieldItems[index].remove();
    }
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
async function saveSyncSettings() {
    const interval = elements.syncInterval.value;
    // Note: Sync interval is typically set via environment variable
    // This is a placeholder for future API implementation
    alert(`Sync interval would be set to ${interval} minutes. (Requires restart)`);
}

// Utility Functions
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Event Listeners
function initEventListeners() {
    elements.oauthForm.addEventListener('submit', saveOAuthCredentials);
    elements.refreshTokenBtn.addEventListener('click', refreshToken);
    elements.saveSyncBtn.addEventListener('click', saveSyncSettings);
    elements.addFieldBtn.addEventListener('click', addField);
    elements.saveFieldsBtn.addEventListener('click', saveCustomFields);

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
    loadStatus();
    loadOAuthStatus();
    loadListings();

    // Refresh status periodically
    setInterval(loadStatus, 30000);
});

// Export functions for inline handlers
window.openCustomFields = openCustomFields;
window.toggleListing = toggleListing;
window.removeField = removeField;
