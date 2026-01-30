<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Cloudbeds to Airbnb iCal Export Bridge

**Feature**: 001-cloudbeds-ical-export
**Date**: 2025-01-24
**Status**: Design

## Overview

This document defines the data entities, relationships, and state management for the Cloudbeds iCal export bridge. The model supports multi-listing configuration, custom field selection, and persistent storage of booking data cache.

---

## Entity Relationship Diagram

```
┌─────────────────────┐
│   OAuthCredential   │
│─────────────────────│
│ id (PK)             │
│ client_id           │
│ client_secret       │
│ access_token        │
│ refresh_token       │
│ token_expires_at    │
│ created_at          │
│ updated_at          │
└─────────────────────┘
           │
           │ 1:N
           ▼
┌─────────────────────┐         ┌──────────────────────┐
│      Listing        │ 1:N     │    CustomField       │
│─────────────────────│◄────────┤──────────────────────┤
│ id (PK)             │         │ id (PK)              │
│ cloudbeds_id        │         │ listing_id (FK)      │
│ name                │         │ field_name           │
│ enabled             │         │ display_label        │
│ ical_url_slug       │         │ enabled              │
│ timezone            │         │ sort_order           │
│ sync_enabled        │         │ created_at           │
│ last_sync_at        │         │ updated_at           │
│ last_sync_error     │         └──────────────────────┘
│ created_at          │
│ updated_at          │
└─────────────────────┘
           │
           │ 1:N
           ▼
┌─────────────────────┐
│      Booking        │
│─────────────────────│
│ id (PK)             │
│ listing_id (FK)     │
│ cloudbeds_booking_id│
│ guest_name          │
│ guest_phone_last4   │
│ check_in_date       │
│ check_out_date      │
│ status              │
│ custom_data (JSON)  │
│ last_fetched_at     │
│ created_at          │
│ updated_at          │
└─────────────────────┘
```

---

## Entity Definitions

### 1. OAuthCredential

Stores Cloudbeds OAuth 2.0 credentials and manages token lifecycle.

**Fields**:

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `id` | Integer | PK, Auto-increment | Unique identifier |
| `client_id` | String(255) | NOT NULL, Unique | OAuth client ID from Cloudbeds |
| `client_secret` | String(255) | NOT NULL, Encrypted | OAuth client secret (encrypted at rest) |
| `access_token` | Text | Nullable, Encrypted | Current OAuth access token |
| `refresh_token` | Text | Nullable, Encrypted | OAuth refresh token |
| `token_expires_at` | DateTime | Nullable | Access token expiration timestamp (UTC) |
| `created_at` | DateTime | NOT NULL, Default: now() | Record creation timestamp |
| `updated_at` | DateTime | NOT NULL, Default: now() | Last update timestamp |

**Validation Rules**:
- `client_id` and `client_secret` must be provided during initial setup
- `token_expires_at` must be validated before API calls; trigger refresh if expired
- Tokens encrypted using SQLAlchemy encryption (Fernet symmetric key from environment)

**State Transitions**:
1. `INITIAL`: Credentials set, no tokens yet
2. `ACTIVE`: Access token valid and not expired
3. `EXPIRED`: Access token expired, refresh needed
4. `INVALID`: Refresh failed, requires re-authentication

**Business Rules**:
- Only one OAuth credential record should exist (singleton pattern)
- Token refresh triggered automatically when `token_expires_at < now() + 5 minutes`
- Failed refresh attempts logged and require admin intervention

---

### 2. Listing

Represents a Cloudbeds property configured for iCal export.

**Fields**:

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `id` | Integer | PK, Auto-increment | Unique identifier |
| `cloudbeds_id` | String(100) | NOT NULL, Unique | Cloudbeds property ID |
| `name` | String(255) | NOT NULL | Display name for the listing |
| `enabled` | Boolean | NOT NULL, Default: false | Whether iCal export is enabled |
| `ical_url_slug` | String(100) | Unique, NOT NULL | URL-safe slug for iCal endpoint |
| `timezone` | String(50) | NOT NULL, Default: 'UTC' | IANA timezone (e.g., 'America/New_York') |
| `sync_enabled` | Boolean | NOT NULL, Default: true | Whether background sync is active |
| `last_sync_at` | DateTime | Nullable | Last successful sync timestamp (UTC) |
| `last_sync_error` | Text | Nullable | Last sync error message (if any) |
| `created_at` | DateTime | NOT NULL, Default: now() | Record creation timestamp |
| `updated_at` | DateTime | NOT NULL, Default: now() | Last update timestamp |

**Validation Rules**:
- `ical_url_slug` must be URL-safe (lowercase, alphanumeric, hyphens only)
- `ical_url_slug` automatically generated from `name` if not provided (slugify)
- `timezone` must be valid IANA timezone identifier
- `cloudbeds_id` verified against Cloudbeds API on creation

**Business Rules**:
- iCal URL format: `https://{domain}/ical/{ical_url_slug}.ics`
- Disabled listings (`enabled=false`) do not serve iCal feeds (return 404)
- Sync disabled listings (`sync_enabled=false`) serve cached data only
- Maximum 50 listings per deployment (enforced at creation)

**Indexes**:
- `idx_listing_cloudbeds_id` on `cloudbeds_id` (unique)
- `idx_listing_slug` on `ical_url_slug` (unique)
- `idx_listing_enabled` on `enabled` (for efficient filtering)

---

### 3. CustomField

Defines optional data fields to include in iCal event descriptions per listing.

**Fields**:

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `id` | Integer | PK, Auto-increment | Unique identifier |
| `listing_id` | Integer | FK (Listing.id), NOT NULL | Associated listing |
| `field_name` | String(100) | NOT NULL | Cloudbeds API field name (e.g., 'booking_notes') |
| `display_label` | String(255) | NOT NULL | Label for display in iCal (e.g., 'Special Requests') |
| `enabled` | Boolean | NOT NULL, Default: true | Whether field is included in iCal output |
| `sort_order` | Integer | NOT NULL, Default: 0 | Display order in event description |
| `created_at` | DateTime | NOT NULL, Default: now() | Record creation timestamp |
| `updated_at` | DateTime | NOT NULL, Default: now() | Last update timestamp |

**Validation Rules**:
- `field_name` must correspond to available Cloudbeds booking fields
- `listing_id` + `field_name` combination must be unique (composite unique constraint)
- `sort_order` determines field order in iCal description (ascending)

**Business Rules**:
- Default custom fields created automatically when listing is enabled:
  - `guest_phone_last4` (always included, not configurable)
  - Additional fields selectable from predefined list (booking_notes, guest_email_domain, arrival_time)
- Admin UI allows enabling/disabling and reordering custom fields
- Disabled fields (`enabled=false`) excluded from iCal output but preserved in config

**Indexes**:
- `idx_customfield_listing` on `listing_id` (foreign key)
- `idx_customfield_unique` on (`listing_id`, `field_name`) (unique composite)

---

### 4. Booking

Cached booking data from Cloudbeds API for iCal generation.

**Fields**:

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `id` | Integer | PK, Auto-increment | Unique identifier |
| `listing_id` | Integer | FK (Listing.id), NOT NULL | Associated listing |
| `cloudbeds_booking_id` | String(100) | NOT NULL | Cloudbeds reservation ID |
| `guest_name` | String(255) | Nullable | Guest full name (fallback to booking ID) |
| `guest_phone_last4` | String(4) | Nullable | Last 4 digits of phone number |
| `check_in_date` | DateTime | NOT NULL | Check-in date/time (in listing's timezone) |
| `check_out_date` | DateTime | NOT NULL | Check-out date/time (in listing's timezone) |
| `status` | String(50) | NOT NULL | Booking status (confirmed, cancelled, etc.) |
| `custom_data` | JSON | Nullable | Additional fields as JSON (e.g., {"booking_notes": "...") |
| `last_fetched_at` | DateTime | NOT NULL, Default: now() | Last API fetch timestamp (UTC) |
| `created_at` | DateTime | NOT NULL, Default: now() | Record creation timestamp |
| `updated_at` | DateTime | NOT NULL, Default: now() | Last update timestamp |

**Validation Rules**:
- `check_out_date` must be after `check_in_date`
- `listing_id` + `cloudbeds_booking_id` must be unique (composite unique constraint)
- `status` must be one of: 'confirmed', 'cancelled', 'pending', 'no_show'
- `guest_phone_last4` extracted from full phone number (never store full number)

**Business Rules**:
- Only 'confirmed' bookings appear in iCal feeds
- Cancelled bookings removed from iCal (soft delete in DB for audit trail)
- If `guest_name` is NULL, use `cloudbeds_booking_id` as event title
- `custom_data` JSON populated based on enabled `CustomField` configurations
- Records older than 90 days from check-out date purged automatically

**State Machine**:
```
[New Booking] → PENDING → CONFIRMED → [Appears in iCal]
                   ↓          ↓
              CANCELLED   NO_SHOW → [Removed from iCal]
```

**Indexes**:
- `idx_booking_listing` on `listing_id` (foreign key, frequently filtered)
- `idx_booking_cloudbeds_id` on (`listing_id`, `cloudbeds_booking_id`) (unique composite)
- `idx_booking_dates` on (`listing_id`, `check_in_date`, `check_out_date`) (for date range queries)
- `idx_booking_status` on `status` (for filtering confirmed bookings)

---

## Relationships

### OAuthCredential → Listing (Implicit 1:N)
- Single OAuth credential used for all Cloudbeds API calls
- No explicit foreign key (singleton pattern)
- All listings share same Cloudbeds account credentials

### Listing → CustomField (1:N)
- One listing can have multiple custom field configurations
- Cascade delete: Deleting a listing removes all associated custom fields
- Orphan cleanup: Not applicable (fields always belong to a listing)

### Listing → Booking (1:N)
- One listing can have many bookings
- Cascade delete: Deleting a listing removes all cached bookings (hard delete for GDPR compliance)
- Soft delete consideration: Bookings not physically deleted, marked as `status='deleted'` for 90-day retention

---

## Data Lifecycle & Caching Strategy

### Booking Data Synchronization

**Fetch Strategy**:
1. Background sync runs every 5 minutes (configurable via `SYNC_INTERVAL_MINUTES`)
2. For each enabled listing:
   - Fetch bookings from Cloudbeds API: `check_in >= today - 24h AND check_out <= today + 365d`
   - Compare with cached bookings by `cloudbeds_booking_id`
   - INSERT new bookings
   - UPDATE changed bookings (guest name, dates, status)
   - Mark missing bookings as 'cancelled'

**Cache Invalidation**:
- In-memory cache: 5-minute TTL for generated iCal strings per listing
- Database cache: Refreshed every sync interval
- Manual refresh: Admin UI "Sync Now" button clears cache and triggers immediate sync

**Purge Policy**:
- Bookings with `check_out_date < today - 90 days` deleted automatically
- Cancelled bookings older than 30 days deleted
- Purge runs daily at 02:00 UTC

---

## Configuration Storage

**Environment Variables** (deployment-specific):
```bash
DATABASE_URL=sqlite:////data/rentalsync.db
ENCRYPTION_KEY=<fernet-key>  # For OAuth token encryption
CLOUDBEDS_CLIENT_ID=<from-setup>
CLOUDBEDS_CLIENT_SECRET=<from-setup>
SYNC_INTERVAL_MINUTES=5
STANDALONE_MODE=false  # true for testing without HA auth
```

**Database Tables** (user-configured via admin UI):
- `oauth_credentials`: Cloudbeds API credentials
- `listings`: Which properties to export
- `custom_fields`: Which booking fields to include
- `bookings`: Cached booking data

---

## Migration Strategy

**Initial Schema** (Alembic migration `001_initial_schema`):
1. Create `oauth_credentials` table
2. Create `listings` table
3. Create `custom_fields` table
4. Create `bookings` table
5. Create indexes
6. Insert default OAuth credential record (values from env vars)

**Future Migrations**:
- `002_add_sync_statistics`: Add sync success/failure counters
- `003_add_listing_groups`: Support grouping listings (future feature)

---

## Example Data Flow

### Scenario: User enables a listing for export

1. **Admin UI POST** `/api/listings/{id}/enable`
   ```json
   {
     "enabled": true,
     "custom_fields": ["guest_phone_last4", "booking_notes"]
   }
   ```

2. **Database Updates**:
   - Update `listings` table: `enabled=true`, generate `ical_url_slug`
   - Insert `custom_fields` records for selected fields
   - Trigger immediate sync for this listing

3. **Background Sync**:
   - Fetch bookings from Cloudbeds API
   - Insert booking records into `bookings` table
   - Update `listings.last_sync_at`

4. **iCal Generation**:
   - User requests `GET /ical/{slug}.ics`
   - Check in-memory cache (5-min TTL)
   - If cache miss:
     - Query bookings: `SELECT * FROM bookings WHERE listing_id=? AND status='confirmed'`
     - For each booking:
       - Create iCal event with `DTSTART`, `DTEND`, `SUMMARY` (guest name or booking ID)
       - Add `DESCRIPTION` with phone last 4 and enabled custom fields
     - Generate iCal string, cache in memory
   - Return iCal content with `Content-Type: text/calendar`

---

## Performance Considerations

**Query Optimization**:
- Use SQLAlchemy eager loading for `listing.custom_fields` joins
- Index on `bookings.listing_id` + `status` for fast confirmed booking queries
- Limit booking queries to date range: `check_out >= today - 7 days`

**Concurrency**:
- SQLite WAL mode for concurrent reads during iCal generation
- Background sync uses separate database connection
- Row-level locking for OAuth token refresh

**Memory Usage**:
- In-memory cache size: ~50 listings × ~10KB/iCal = 500KB max
- Booking table: ~50 listings × 365 bookings × 1KB/row = ~18MB
- Total footprint target: < 100MB RAM

---

## Security & Privacy

**Data Minimization**:
- Store only last 4 digits of phone numbers (NEVER full number)
- Do not store guest email addresses, payment info, or full addresses
- `custom_data` JSON limited to non-PII fields only

**Encryption**:
- OAuth tokens encrypted at rest using Fernet (symmetric key from env var)
- SQLite database file permissions: 600 (owner read/write only)

**Access Control**:
- Admin UI requires Home Assistant authentication (in addon mode)
- iCal URLs intentionally public (standard calendar subscription model)
- No authentication on iCal endpoints (by design, per Airbnb/Google Calendar expectations)

---

## Open Questions

1. **Multi-Property Management**: If a user has 100+ properties in Cloudbeds, how to handle listing selection UI? (Future: add search/filter)
2. **Booking Conflicts**: How to handle overlapping bookings on same listing? (Display all, calendar apps handle conflicts)
3. **Historical Data**: Should system pre-fetch historical bookings beyond 24h on first enable? (No, start fresh to respect rate limits)

---

## Next Steps

1. ✅ Data model defined
2. → Generate API contracts in `contracts/` (OpenAPI specification)
3. → Generate `quickstart.md` for standalone setup
4. → Implement SQLAlchemy models matching this schema
5. → Create Alembic migrations
