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

**Response includes rooms:**
```json
{
  "id": 1,
  "cloudbeds_id": "203249",
  "name": "Beach House",
  "enabled": true,
  "ical_url_slug": "beach-house",
  "timezone": "America/New_York",
  "sync_enabled": true,
  "last_sync_at": "2026-01-31T12:00:00Z",
  "last_sync_error": null,
  "rooms": [
    {
      "id": 1,
      "listing_id": 1,
      "cloudbeds_room_id": "12345",
      "room_name": "Master Bedroom",
      "room_type_name": "Deluxe King",
      "ical_url_slug": "master-bedroom",
      "enabled": true,
      "created_at": "2026-01-31T10:00:00Z",
      "updated_at": "2026-01-31T10:00:00Z"
    }
  ]
}
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

Fetches all properties and their rooms from Cloudbeds and creates/updates listings and rooms.

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

### Rooms

#### Get Rooms for Listing

```http
GET /api/listings/{id}/rooms
```

Returns all rooms for a specific listing.

**Response:**
```json
{
  "rooms": [
    {
      "id": 1,
      "listing_id": 1,
      "cloudbeds_room_id": "12345",
      "room_name": "Master Bedroom",
      "room_type_name": "Deluxe King",
      "ical_url_slug": "master-bedroom",
      "enabled": true,
      "created_at": "2026-01-31T10:00:00Z",
      "updated_at": "2026-01-31T10:00:00Z"
    },
    {
      "id": 2,
      "listing_id": 1,
      "cloudbeds_room_id": "12346",
      "room_name": "Guest Room",
      "room_type_name": "Queen",
      "ical_url_slug": "guest-room",
      "enabled": true,
      "created_at": "2026-01-31T10:00:00Z",
      "updated_at": "2026-01-31T10:00:00Z"
    }
  ]
}
```

#### Get Single Room

```http
GET /api/rooms/{id}
```

**Response:**
```json
{
  "id": 1,
  "listing_id": 1,
  "cloudbeds_room_id": "12345",
  "room_name": "Master Bedroom",
  "room_type_name": "Deluxe King",
  "ical_url_slug": "master-bedroom",
  "enabled": true,
  "created_at": "2026-01-31T10:00:00Z",
  "updated_at": "2026-01-31T10:00:00Z"
}
```

#### Update Room

```http
PATCH /api/rooms/{id}
Content-Type: application/json

{
  "enabled": false,
  "ical_url_slug": "master-suite"
}
```

**Fields:**
- `enabled` (optional): Enable or disable room for iCal export
- `ical_url_slug` (optional): Custom URL slug (lowercase letters, numbers, hyphens only)

**Response:**
```json
{
  "id": 1,
  "listing_id": 1,
  "cloudbeds_room_id": "12345",
  "room_name": "Master Bedroom",
  "room_type_name": "Deluxe King",
  "ical_url_slug": "master-suite",
  "enabled": false,
  "created_at": "2026-01-31T10:00:00Z",
  "updated_at": "2026-01-31T12:30:00Z"
}
```

### iCal Feeds

#### Get Room iCal Calendar

```http
GET /ical/{listing-slug}/{room-slug}.ics
```

Returns RFC 5545 compliant iCal calendar for a specific room.

**Example:**
```
GET /ical/beach-house/master-bedroom.ics
GET /ical/beach-house/guest-room.ics
```

**Headers:**
```
Content-Type: text/calendar
Content-Disposition: attachment; filename="master-bedroom.ics"
```

**Response Example:**
```ical
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//RentalSync Bridge//rentalsync-bridge//EN
CALSCALE:GREGORIAN
METHOD:PUBLISH
X-WR-CALNAME:Beach House - Master Bedroom
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

**Note**: Property-level iCal URLs (`/ical/{slug}.ics`) are no longer supported and will return HTTP 410 Gone. Use room-level URLs for all calendar exports.

### Custom Fields

#### Get Available Custom Fields

```http
GET /api/listings/{id}/available-custom-fields
```

Returns list of available custom fields that can be configured for a listing.

**Response:**
```json
{
  "available_fields": [
    {
      "field_name": "guest_name",
      "default_label": "Guest Name",
      "description": "Primary guest name"
    },
    {
      "field_name": "guest_phone_last4",
      "default_label": "Phone Number (Last 4 Digits)",
      "description": "Last 4 digits of guest phone number"
    },
    {
      "field_name": "num_guests",
      "default_label": "Number of Guests",
      "description": "Total guest count"
    },
    {
      "field_name": "arrival_time",
      "default_label": "Arrival Time",
      "description": "Expected check-in time"
    },
    {
      "field_name": "booking_notes",
      "default_label": "Special Requests",
      "description": "Guest notes and special requests"
    }
  ]
}
```

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
