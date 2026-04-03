<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->
# API Contracts: Guesty PMS Provider Integration

**Feature Branch**: `003-guesty-pms-provider`
**Date**: 2025-07-15

## Overview

This document defines the REST API contract changes for multi-PMS provider support.
Existing endpoints are modified minimally for backward compatibility. One new endpoint
is added for provider discovery.

---

## New Endpoint: GET /api/providers

### Description
Returns the list of supported PMS providers and their credential requirements.
Used by the admin UI to dynamically render provider-specific forms.

### Request
```
GET /api/providers
```

No query parameters. No request body.

### Response: 200 OK
```json
{
  "providers": [
    {
      "type": "cloudbeds",
      "name": "Cloudbeds",
      "description": "Cloudbeds property management system",
      "auth_flow": "oauth2_authorization_code",
      "credential_fields": [
        {
          "name": "client_id",
          "label": "Client ID",
          "type": "text",
          "required": true
        },
        {
          "name": "client_secret",
          "label": "Client Secret",
          "type": "password",
          "required": true
        },
        {
          "name": "api_key",
          "label": "API Key (alternative to OAuth)",
          "type": "password",
          "required": false
        },
        {
          "name": "access_token",
          "label": "Access Token",
          "type": "password",
          "required": false
        },
        {
          "name": "refresh_token",
          "label": "Refresh Token",
          "type": "password",
          "required": false
        }
      ]
    },
    {
      "type": "guesty",
      "name": "Guesty",
      "description": "Guesty property management system",
      "auth_flow": "oauth2_client_credentials",
      "credential_fields": [
        {
          "name": "client_id",
          "label": "Client ID",
          "type": "text",
          "required": true
        },
        {
          "name": "client_secret",
          "label": "Client Secret",
          "type": "password",
          "required": true
        }
      ]
    }
  ]
}
```

---

## Modified Endpoint: POST /api/oauth/configure

### Description
Configure PMS credentials. Updated to accept `pms_type` parameter.

### Request
```json
{
  "pms_type": "guesty",
  "client_id": "string",
  "client_secret": "string",
  "api_key": null,
  "access_token": null,
  "refresh_token": null,
  "token_expires_at": null
}
```

#### Field Details

| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `pms_type` | string | No | `"cloudbeds"` | Provider type; omit for backward compat |
| `client_id` | string | Yes | — | Provider client ID |
| `client_secret` | string | Yes | — | Provider client secret |
| `api_key` | string | No | null | Cloudbeds only |
| `access_token` | string | No | null | Cloudbeds OAuth only |
| `refresh_token` | string | No | null | Cloudbeds OAuth only |
| `token_expires_at` | string (ISO 8601) | No | null | Cloudbeds OAuth only |

#### Provider-Specific Validation

**When `pms_type = "guesty"`**:
- `client_id` and `client_secret` are required
- `api_key`, `access_token`, `refresh_token` must be null or omitted
- System will automatically obtain a bearer token via client_credentials flow

**When `pms_type = "cloudbeds"` (default)**:
- `client_id` and `client_secret` are required
- Either `api_key` OR (`access_token` + `refresh_token`) must be provided
- Existing behavior preserved exactly

### Response: 200 OK
```json
{
  "success": true,
  "message": "Guesty credentials configured and connection verified",
  "pms_type": "guesty"
}
```

### Response: 400 Bad Request
```json
{
  "detail": "Invalid credentials: Guesty authentication failed"
}
```

### Response: 422 Validation Error
```json
{
  "detail": "api_key is not supported for Guesty provider"
}
```

---

## Modified Endpoint: GET /api/oauth/status

### Description
Get current OAuth/credential configuration and connection status.
Updated to include `pms_type` and Guesty-specific rate limit info.

### Response: 200 OK (Guesty configured)
```json
{
  "configured": true,
  "connected": true,
  "pms_type": "guesty",
  "auth_type": "client_credentials",
  "token_expires_at": "2025-07-16T12:00:00Z",
  "token_expired": false,
  "token_requests_remaining": 3
}
```

### Response: 200 OK (Cloudbeds configured — OAuth)
```json
{
  "configured": true,
  "connected": true,
  "pms_type": "cloudbeds",
  "auth_type": "oauth",
  "token_expires_at": "2025-07-15T18:00:00Z",
  "token_expired": false,
  "token_requests_remaining": null
}
```

### Response: 200 OK (Cloudbeds configured — API Key)
```json
{
  "configured": true,
  "connected": true,
  "pms_type": "cloudbeds",
  "auth_type": "api_key",
  "token_expires_at": null,
  "token_expired": false,
  "token_requests_remaining": null
}
```

### Response: 200 OK (not configured)
```json
{
  "configured": false,
  "connected": false,
  "pms_type": null,
  "auth_type": null,
  "token_expires_at": null,
  "token_expired": false,
  "token_requests_remaining": null
}
```

#### New Fields

| Field | Type | Notes |
|-------|------|-------|
| `pms_type` | string \| null | `"cloudbeds"`, `"guesty"`, or null if unconfigured |
| `token_requests_remaining` | int \| null | Guesty only: remaining token requests in 24h window; null for Cloudbeds |

---

## Modified Endpoint: POST /api/listings/sync-properties

### Description
Fetch properties from the active PMS provider and create/update local listings.
Previously hardcoded to Cloudbeds; now uses whichever provider is configured.

### Request
```
POST /api/listings/sync-properties
```
No request body (unchanged).

### Behavior Change
- Reads `pms_type` from stored credential
- Instantiates the matching provider via registry
- Calls `provider.get_listings()` to fetch properties
- Creates/updates Listing records using `pms_id` (was `cloudbeds_id`)
- For each listing, calls `provider.get_rooms()` to sync rooms

### Response: 200 OK (modified — field name change)
```json
{
  "success": true,
  "created": 1,
  "updated": 2,
  "rooms_created": 3,
  "rooms_updated": 1,
  "listings": [
    {
      "id": 1,
      "pms_id": "507f1f77bcf86cd799439011",
      "name": "Beach House Suite",
      "enabled": false,
      "ical_url_slug": "beach-house-suite"
    }
  ]
}
```

Note: Response uses the renamed `pms_id` field (previously `cloudbeds_id`).
This is a breaking change for any client relying on the old field name.

---

## Minimally Changed Endpoints

Existing endpoints are updated to support provider-specific configuration while
preserving backward compatibility where possible. Any endpoint response-shape changes
are considered breaking changes and must be explicitly documented as such. One new
endpoint is added for provider discovery.

| Endpoint | Reason |
|----------|--------|
| `GET /ical/{slug}/{room}.ics` | Operates on Booking model (PMS-agnostic) |
| `GET /api/listings` | Response field `cloudbeds_id` → `pms_id` (model rename) |
| `GET /api/listings/{id}` | Response field `cloudbeds_id` → `pms_id` |
| `PUT /api/listings/{id}` | Updates Listing fields (no PMS-specific fields) |
| `POST /api/listings/{id}/enable` | Toggles `enabled` flag |
| `POST /api/listings/{id}/sync` | Uses active provider (implementation change, not contract) |
| `POST /api/listings/bulk` | Toggles `enabled` flags |
| `GET /api/listings/{id}/bookings` | Returns Booking model |
| `GET /api/listings/{id}/rooms` | Returns Room model |
| `GET/PATCH /api/rooms/{id}` | Room operations (PMS-agnostic) |
| `GET/PUT /api/listings/{id}/custom-fields` | Custom field config (PMS-agnostic) |
| `GET /api/listings/{id}/available-custom-fields` | Field discovery (PMS-agnostic) |
| `GET/PUT /api/settings/*` | System settings (PMS-agnostic) |
| `GET /health` | Health check |
| `GET /api/status` | System status |
| `POST /api/oauth/refresh` | Token refresh (uses active provider internally) |
