<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# API Usage Guide

RentalSync Bridge provides a REST API for managing listings, bookings, and
calendar generation.

## Base URL

- **Home Assistant Add-on**: `http://<homeassistant-ip>:8099`
- **Standalone Mode**: `http://localhost:8099`

## Authentication

Admin endpoints require Home Assistant Ingress authentication (automatic when
accessing via the HA sidebar). Public endpoints (health, iCal) are
unauthenticated.

In standalone mode, all endpoints are accessible without authentication.

## Endpoints

### Health Check

```http
GET /health
```

Returns service health status.

**Response:**
```json
{
  "status": "healthy",
  "version": "0.1.0"
}
```

### Listings

#### List All Listings

```http
GET /api/listings
```

**Response:**
```json
{
  "listings": [
    {
      "id": 1,
      "cloudbeds_id": "203249",
      "name": "Beach House",
      "enabled": true,
      "ical_url_slug": "beach-house",
      "timezone": "America/New_York",
      "sync_enabled": true,
      "last_sync_at": "2026-01-31T12:00:00Z",
      "last_sync_error": null
    }
  ],
  "total": 1
}
```

#### Get Single Listing

```http
GET /api/listings/{id}
```

#### Update Listing

```http
PUT /api/listings/{id}
Content-Type: application/json

{
  "enabled": true,
  "sync_enabled": true,
  "timezone": "America/Los_Angeles"
}
```

#### Sync Properties from Cloudbeds

```http
POST /api/listings/sync-properties
```

Fetches all properties from Cloudbeds and creates/updates listings.

**Response:**
```json
{
  "message": "Synced 3 properties from Cloudbeds",
  "synced_count": 3
}
```

#### Manual Sync for Listing

```http
POST /api/listings/{id}/sync
```

Triggers immediate booking sync from Cloudbeds.

**Response:**
```json
{
  "message": "Sync completed successfully",
  "inserted": 5,
  "updated": 2,
  "cancelled": 1
}
```

### iCal Feeds

#### Get iCal Calendar

```http
GET /ical/{slug}.ics
```

Returns RFC 5545 compliant iCal calendar for the listing.

**Headers:**
```
Content-Type: text/calendar
Content-Disposition: attachment; filename="beach-house.ics"
```

**Response Example:**
```ical
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//RentalSync Bridge//rentalsync-bridge//EN
CALSCALE:GREGORIAN
METHOD:PUBLISH
X-WR-CALNAME:Beach House
BEGIN:VEVENT
UID:abc123@rentalsync-bridge
DTSTART:20260201
DTEND:20260205
SUMMARY:John Smith
DESCRIPTION:Phone (last 4): 1234\nBooking ID: RES001
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
```

### Custom Fields

#### List Custom Fields for Listing

```http
GET /api/listings/{id}/custom-fields
```

**Response:**
```json
{
  "custom_fields": [
    {
      "id": 1,
      "listing_id": 1,
      "field_name": "arrival_time",
      "display_label": "Arrival Time",
      "enabled": true
    }
  ]
}
```

#### Add Custom Field

```http
POST /api/listings/{id}/custom-fields
Content-Type: application/json

{
  "field_name": "num_guests",
  "display_label": "Number of Guests",
  "enabled": true
}
```

#### Update Custom Field

```http
PUT /api/custom-fields/{id}
Content-Type: application/json

{
  "display_label": "Guest Count",
  "enabled": false
}
```

### OAuth Management

#### Get OAuth Status

```http
GET /api/oauth/status
```

**Response:**
```json
{
  "configured": true,
  "token_valid": true,
  "expires_at": "2026-02-01T12:00:00Z"
}
```

#### Configure OAuth

```http
POST /api/oauth/configure
Content-Type: application/json

{
  "client_id": "your-client-id",
  "client_secret": "your-client-secret",
  "api_key": "your-api-key"
}
```

### Status

#### Get Sync Status

```http
GET /api/status/sync
```

**Response:**
```json
{
  "scheduler_running": true,
  "last_global_sync": "2026-01-31T12:00:00Z",
  "listings_summary": {
    "total": 5,
    "enabled": 3,
    "sync_enabled": 3,
    "with_errors": 0
  }
}
```

## Error Responses

All errors return JSON with `detail` field:

```json
{
  "detail": "Listing not found"
}
```

Common HTTP status codes:
- `400` - Bad request (invalid parameters)
- `401` - Unauthorized (authentication required)
- `404` - Resource not found
- `422` - Validation error
- `500` - Internal server error

## Rate Limiting

The API implements rate limiting for Cloudbeds API calls:
- Maximum 30 requests per minute
- Automatic retry with exponential backoff
- Rate limit errors return HTTP 429
