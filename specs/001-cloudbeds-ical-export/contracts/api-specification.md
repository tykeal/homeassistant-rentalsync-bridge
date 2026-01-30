<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# API Contracts: RentalSync Bridge

**Feature**: 001-cloudbeds-ical-export
**Date**: 2025-01-24
**Version**: 1.0.0

This document defines the HTTP API contracts for the RentalSync Bridge application. The API follows REST principles with JSON request/response bodies (except iCal endpoints which return text/calendar).

---

## Base URL

- **Production (Home Assistant Addon)**: `http://homeassistant.local:8099/`
- **Standalone**: `http://localhost:8000/`

---

## Authentication

### Admin API Endpoints
- **Home Assistant Addon Mode**: Authenticated via Ingress proxy (automatic)
  - Ingress adds `X-Ingress-User` header with authenticated username
  - No explicit authentication required from client
- **Standalone Mode**: Authentication disabled when `STANDALONE_MODE=true`
  - All admin endpoints accessible without authentication (for testing only)

### iCal Feed Endpoints
- **Public Access**: No authentication required (intentional for calendar subscription)
- **Obscurity**: iCal URLs use unpredictable slugs for basic access control

---

## API Endpoints

### Health & Status

#### `GET /health`

Health check endpoint for container orchestration.

**Response**: `200 OK`
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "timestamp": "2025-01-24T12:34:56Z"
}
```

**Error Response**: `503 Service Unavailable`
```json
{
  "status": "unhealthy",
  "error": "Database connection failed"
}
```

---

#### `GET /api/status`

System status including sync information (requires authentication in addon mode).

**Response**: `200 OK`
```json
{
  "oauth_configured": true,
  "oauth_valid": true,
  "last_global_sync": "2025-01-24T12:30:00Z",
  "listings_enabled": 5,
  "total_bookings_cached": 1234,
  "sync_interval_minutes": 5,
  "next_sync_at": "2025-01-24T12:35:00Z"
}
```

---

### OAuth Configuration

#### `GET /api/oauth/status`

Check OAuth credential status (admin only).

**Response**: `200 OK`
```json
{
  "configured": true,
  "valid": true,
  "expires_at": "2025-01-24T18:00:00Z",
  "client_id": "abc123..."
}
```

---

#### `POST /api/oauth/configure`

Set or update OAuth credentials (admin only).

**Request Body**:
```json
{
  "client_id": "your_cloudbeds_client_id",
  "client_secret": "your_cloudbeds_client_secret"
}
```

**Response**: `200 OK`
```json
{
  "message": "OAuth credentials updated successfully",
  "status": "pending_token"
}
```

**Error Response**: `400 Bad Request`
```json
{
  "error": "Invalid client_id or client_secret"
}
```

---

#### `POST /api/oauth/refresh`

Manually trigger OAuth token refresh (admin only).

**Request Body**: None

**Response**: `200 OK`
```json
{
  "message": "Token refreshed successfully",
  "expires_at": "2025-01-24T18:00:00Z"
}
```

**Error Response**: `401 Unauthorized`
```json
{
  "error": "Invalid refresh token, re-authentication required"
}
```

---

### Listing Management

#### `GET /api/listings`

List all Cloudbeds properties available for export (admin only).

**Query Parameters**:
- `enabled` (optional): Filter by enabled status (`true`, `false`, or omit for all)

**Response**: `200 OK`
```json
{
  "listings": [
    {
      "id": 1,
      "cloudbeds_id": "12345",
      "name": "Downtown Loft",
      "enabled": true,
      "ical_url": "https://homeassistant.local:8099/ical/downtown-loft.ics",
      "ical_url_slug": "downtown-loft",
      "timezone": "America/New_York",
      "sync_enabled": true,
      "last_sync_at": "2025-01-24T12:30:00Z",
      "last_sync_error": null,
      "bookings_count": 45
    },
    {
      "id": 2,
      "cloudbeds_id": "67890",
      "name": "Beach House",
      "enabled": false,
      "ical_url": null,
      "ical_url_slug": "beach-house",
      "timezone": "America/Los_Angeles",
      "sync_enabled": false,
      "last_sync_at": null,
      "last_sync_error": null,
      "bookings_count": 0
    }
  ],
  "total": 2
}
```

---

#### `GET /api/listings/{id}`

Get details for a specific listing (admin only).

**Path Parameters**:
- `id` (integer): Listing ID

**Response**: `200 OK`
```json
{
  "id": 1,
  "cloudbeds_id": "12345",
  "name": "Downtown Loft",
  "enabled": true,
  "ical_url": "https://homeassistant.local:8099/ical/downtown-loft.ics",
  "ical_url_slug": "downtown-loft",
  "timezone": "America/New_York",
  "sync_enabled": true,
  "last_sync_at": "2025-01-24T12:30:00Z",
  "last_sync_error": null,
  "custom_fields": [
    {
      "id": 1,
      "field_name": "guest_phone_last4",
      "display_label": "Phone (Last 4)",
      "enabled": true,
      "sort_order": 0
    },
    {
      "id": 2,
      "field_name": "booking_notes",
      "display_label": "Special Requests",
      "enabled": true,
      "sort_order": 1
    }
  ],
  "bookings_count": 45
}
```

**Error Response**: `404 Not Found`
```json
{
  "error": "Listing not found"
}
```

---

#### `POST /api/listings/{id}/enable`

Enable iCal export for a listing (admin only).

**Path Parameters**:
- `id` (integer): Listing ID

**Request Body**:
```json
{
  "enabled": true,
  "sync_enabled": true,
  "custom_fields": [
    {
      "field_name": "guest_phone_last4",
      "display_label": "Phone (Last 4)",
      "enabled": true,
      "sort_order": 0
    },
    {
      "field_name": "booking_notes",
      "display_label": "Special Requests",
      "enabled": true,
      "sort_order": 1
    }
  ]
}
```

**Response**: `200 OK`
```json
{
  "message": "Listing enabled successfully",
  "ical_url": "https://homeassistant.local:8099/ical/downtown-loft.ics",
  "sync_triggered": true
}
```

**Error Response**: `400 Bad Request`
```json
{
  "error": "Maximum of 50 listings can be enabled"
}
```

---

#### `PUT /api/listings/{id}`

Update listing configuration (admin only).

**Path Parameters**:
- `id` (integer): Listing ID

**Request Body**:
```json
{
  "enabled": true,
  "sync_enabled": true,
  "timezone": "America/New_York"
}
```

**Response**: `200 OK`
```json
{
  "message": "Listing updated successfully",
  "listing": {
    "id": 1,
    "enabled": true,
    "sync_enabled": true,
    "timezone": "America/New_York"
  }
}
```

---

#### `POST /api/listings/{id}/sync`

Manually trigger sync for a specific listing (admin only).

**Path Parameters**:
- `id` (integer): Listing ID

**Request Body**: None

**Response**: `202 Accepted`
```json
{
  "message": "Sync triggered for listing 'Downtown Loft'",
  "sync_status": "in_progress"
}
```

**Error Response**: `404 Not Found`
```json
{
  "error": "Listing not found or not enabled"
}
```

---

### Custom Fields Management

#### `GET /api/listings/{id}/custom-fields`

Get custom field configuration for a listing (admin only).

**Path Parameters**:
- `id` (integer): Listing ID

**Response**: `200 OK`
```json
{
  "custom_fields": [
    {
      "id": 1,
      "field_name": "guest_phone_last4",
      "display_label": "Phone (Last 4)",
      "enabled": true,
      "sort_order": 0
    },
    {
      "id": 2,
      "field_name": "booking_notes",
      "display_label": "Special Requests",
      "enabled": false,
      "sort_order": 1
    }
  ],
  "available_fields": [
    {
      "field_name": "guest_phone_last4",
      "display_label": "Phone (Last 4)",
      "description": "Last 4 digits of guest phone number"
    },
    {
      "field_name": "booking_notes",
      "display_label": "Special Requests",
      "description": "Guest notes and special requests"
    },
    {
      "field_name": "guest_email_domain",
      "display_label": "Email Domain",
      "description": "Domain part of guest email (e.g., gmail.com)"
    },
    {
      "field_name": "arrival_time",
      "display_label": "Arrival Time",
      "description": "Expected arrival time"
    }
  ]
}
```

---

#### `PUT /api/listings/{id}/custom-fields`

Update custom field configuration for a listing (admin only).

**Path Parameters**:
- `id` (integer): Listing ID

**Request Body**:
```json
{
  "custom_fields": [
    {
      "field_name": "guest_phone_last4",
      "display_label": "Phone (Last 4)",
      "enabled": true,
      "sort_order": 0
    },
    {
      "field_name": "booking_notes",
      "display_label": "Notes",
      "enabled": true,
      "sort_order": 1
    }
  ]
}
```

**Response**: `200 OK`
```json
{
  "message": "Custom fields updated successfully",
  "custom_fields_count": 2
}
```

---

### Bookings (Read-Only)

#### `GET /api/listings/{id}/bookings`

Get cached bookings for a listing (admin only, for debugging).

**Path Parameters**:
- `id` (integer): Listing ID

**Query Parameters**:
- `status` (optional): Filter by status (`confirmed`, `cancelled`, `pending`, `no_show`)
- `from_date` (optional): Filter bookings from date (ISO 8601)
- `to_date` (optional): Filter bookings to date (ISO 8601)

**Response**: `200 OK`
```json
{
  "bookings": [
    {
      "id": 1,
      "cloudbeds_booking_id": "RES123456",
      "guest_name": "John Doe",
      "guest_phone_last4": "5678",
      "check_in_date": "2025-02-01T15:00:00-05:00",
      "check_out_date": "2025-02-05T11:00:00-05:00",
      "status": "confirmed",
      "custom_data": {
        "booking_notes": "Late arrival expected"
      },
      "last_fetched_at": "2025-01-24T12:30:00Z"
    }
  ],
  "total": 1
}
```

---

### iCal Feed Endpoints (Public)

#### `GET /ical/{slug}.ics`

Retrieve iCal feed for a listing (public, no authentication).

**Path Parameters**:
- `slug` (string): Listing URL slug (e.g., `downtown-loft`)

**Response**: `200 OK`
```
Content-Type: text/calendar; charset=utf-8
Content-Disposition: inline; filename="downtown-loft.ics"

BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//RentalSync Bridge//EN
CALSCALE:GREGORIAN
METHOD:PUBLISH
X-WR-CALNAME:Downtown Loft
X-WR-TIMEZONE:America/New_York

BEGIN:VTIMEZONE
TZID:America/New_York
BEGIN:STANDARD
DTSTART:20241103T020000
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:20250309T020000
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
END:DAYLIGHT
END:VTIMEZONE

BEGIN:VEVENT
UID:RES123456@rentalsync.local
DTSTAMP:20250124T123000Z
DTSTART;TZID=America/New_York:20250201T150000
DTEND;TZID=America/New_York:20250205T110000
SUMMARY:John Doe
DESCRIPTION:Phone (Last 4): 5678\nSpecial Requests: Late arrival expected
STATUS:CONFIRMED
TRANSP:OPAQUE
END:VEVENT

END:VCALENDAR
```

**Error Response**: `404 Not Found`
```
Content-Type: text/plain

Calendar not found or not enabled
```

---

## Data Models (Request/Response Schemas)

### ListingSchema
```json
{
  "id": "integer",
  "cloudbeds_id": "string",
  "name": "string",
  "enabled": "boolean",
  "ical_url": "string | null",
  "ical_url_slug": "string",
  "timezone": "string",
  "sync_enabled": "boolean",
  "last_sync_at": "datetime | null",
  "last_sync_error": "string | null",
  "bookings_count": "integer"
}
```

### CustomFieldSchema
```json
{
  "id": "integer (response only)",
  "field_name": "string",
  "display_label": "string",
  "enabled": "boolean",
  "sort_order": "integer"
}
```

### BookingSchema
```json
{
  "id": "integer",
  "cloudbeds_booking_id": "string",
  "guest_name": "string | null",
  "guest_phone_last4": "string (4 chars) | null",
  "check_in_date": "datetime (ISO 8601)",
  "check_out_date": "datetime (ISO 8601)",
  "status": "string (enum: confirmed, cancelled, pending, no_show)",
  "custom_data": "object",
  "last_fetched_at": "datetime"
}
```

### ErrorSchema
```json
{
  "error": "string (error message)",
  "detail": "string (optional, additional context)"
}
```

---

## HTTP Status Codes

- `200 OK`: Successful request
- `201 Created`: Resource created successfully
- `202 Accepted`: Request accepted for async processing
- `400 Bad Request`: Invalid request parameters or body
- `401 Unauthorized`: Authentication required or failed
- `403 Forbidden`: Authenticated but not authorized
- `404 Not Found`: Resource not found
- `409 Conflict`: Resource conflict (e.g., duplicate slug)
- `422 Unprocessable Entity`: Validation error
- `500 Internal Server Error`: Server error
- `503 Service Unavailable`: Service temporarily unavailable

---

## Rate Limiting

No rate limiting applied on API endpoints (single-tenant, trusted admin access). iCal endpoints rely on calendar client refresh intervals (typically 15-60 minutes).

---

## CORS Policy

- **Admin API**: CORS disabled (same-origin only, served by Ingress)
- **iCal Endpoints**: CORS enabled (allow all origins for calendar subscription)

---

## Webhook Support (Future)

Not implemented in v1.0. Future versions may support Cloudbeds webhooks if API becomes available.

---

## OpenAPI Specification

Full OpenAPI 3.0 specification available at:
- **Endpoint**: `GET /api/docs` (Swagger UI)
- **JSON Schema**: `GET /api/openapi.json`

---

## Example Usage

### Enable a listing with custom fields

```bash
curl -X POST http://localhost:8000/api/listings/1/enable \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "sync_enabled": true,
    "custom_fields": [
      {
        "field_name": "guest_phone_last4",
        "display_label": "Phone",
        "enabled": true,
        "sort_order": 0
      },
      {
        "field_name": "booking_notes",
        "display_label": "Notes",
        "enabled": true,
        "sort_order": 1
      }
    ]
  }'
```

### Subscribe to iCal feed in Google Calendar

1. Copy iCal URL from admin UI: `https://homeassistant.local:8099/ical/downtown-loft.ics`
2. In Google Calendar: Settings → Add Calendar → From URL
3. Paste URL and click "Add Calendar"
4. Calendar syncs automatically every ~24 hours

---

## Security Considerations

1. **Admin API Protection**: Relies on Home Assistant Ingress authentication in production
2. **iCal URL Obscurity**: Uses unpredictable slugs to prevent enumeration
3. **No PII in iCal**: Only last 4 phone digits, guest names, and booking IDs exposed
4. **HTTPS Required**: Enforce HTTPS in production (handled by Home Assistant or reverse proxy)

---

## Testing Endpoints

Use provided Postman collection or curl scripts in `tests/integration/` for API testing.

**Standalone Mode Environment Variables**:
```bash
STANDALONE_MODE=true
DATABASE_URL=sqlite:///./test.db
```

This allows testing without Home Assistant authentication.
