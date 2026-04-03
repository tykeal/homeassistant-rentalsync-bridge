<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->
# Data Model: Guesty PMS Provider Integration

**Feature Branch**: `003-guesty-pms-provider`
**Date**: 2025-07-15

## Overview

This document describes the data model changes required to support multiple PMS providers. The core change is renaming Cloudbeds-specific columns to provider-agnostic `pms_*` names and adding a `pms_type` discriminator to the credential table.

## Entity Relationship Diagram

```text
┌─────────────────────────┐
│   OAuthCredential       │
├─────────────────────────┤
│ id              INT PK  │
│ pms_type        STR(20) │ ← NEW: "cloudbeds" | "guesty"
│ client_id       STR     │
│ client_secret   STR enc │
│ api_key         TXT enc │
│ access_token    TXT enc │
│ refresh_token   TXT enc │
│ token_expires   DT      │
│ token_req_count INT     │ ← NEW: Guesty rate tracking
│ token_req_start DT      │ ← NEW: 24h window start
│ created_at      DT      │
│ updated_at      DT      │
└─────────────────────────┘

┌──────────────────────────┐       ┌──────────────────────────┐
│   Listing                │       │   Room                   │
├──────────────────────────┤       ├──────────────────────────┤
│ id              INT PK   │──┐    │ id              INT PK   │
│ pms_id          STR UQ   │  │    │ listing_id      INT FK   │──→ Listing.id
│ name            STR      │  │    │ pms_room_id     STR      │ ← RENAMED
│ enabled         BOOL     │  │    │ room_name       STR      │
│ ical_url_slug   STR UQ   │  │    │ room_type_name  STR      │
│ timezone        STR      │  │    │ ical_url_slug   STR      │
│ sync_enabled    BOOL     │  │    │ enabled         BOOL     │
│ last_sync_at    DT       │  │    │ created_at      DT       │
│ last_sync_error TXT      │  │    │ updated_at      DT       │
│ created_at      DT       │  │    └──────────────────────────┘
│ updated_at      DT       │  │           │
└──────────────────────────┘  │           │ (FK: room_id, SET NULL)
         │                    │           ▼
         │ (FK: listing_id,   │    ┌──────────────────────────┐
         │  CASCADE)          │    │   Booking                │
         ▼                    │    ├──────────────────────────┤
┌──────────────────────────┐  │    │ id              INT PK   │
│   CustomField            │  ├───→│ listing_id      INT FK   │
├──────────────────────────┤  │    │ room_id         INT FK   │
│ id              INT PK   │  │    │ pms_booking_id  STR      │ ← RENAMED
│ listing_id      INT FK   │──┘    │ guest_name      STR      │
│ field_name      STR      │       │ guest_phone_l4  STR(4)   │
│ display_label   STR      │       │ check_in_date   DT       │
│ enabled         BOOL     │       │ check_out_date  DT       │
│ sort_order      INT      │       │ status          STR      │
│ created_at      DT       │       │ custom_data     JSON     │
│ updated_at      DT       │       │ last_fetched_at DT       │
└──────────────────────────┘       │ created_at      DT       │
                                   │ updated_at      DT       │
┌──────────────────────────┐       └──────────────────────────┘
│   AvailableField         │
├──────────────────────────┤       ┌──────────────────────────┐
│ id              INT PK   │       │   SystemSettings         │
│ listing_id      INT FK   │       ├──────────────────────────┤
│ field_key       STR      │       │ id              INT PK   │
│ display_name    STR      │       │ sync_interval   INT      │
│ sample_value    STR      │       │ settings_key    STR UQ   │
│ discovered_at   DT       │       │ updated_at      DT       │
│ last_seen_at    DT       │       └──────────────────────────┘
└──────────────────────────┘
```

## Detailed Entity Specifications

### OAuthCredential (oauth_credentials table)

#### New Columns

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `pms_type` | String(20) | NOT NULL | `"cloudbeds"` | Provider discriminator |
| `token_request_count` | Integer | NOT NULL | `0` | Token requests in current 24h window |
| `token_request_window_start` | DateTime | YES | `NULL` | Start of current 24h tracking window |

#### Column Usage by Provider

| Column | Cloudbeds | Guesty |
|--------|-----------|--------|
| `pms_type` | `"cloudbeds"` | `"guesty"` |
| `client_id` | OAuth app client ID | Guesty API client ID |
| `client_secret` | OAuth app client secret | Guesty API client secret |
| `api_key` | Alternative auth (optional) | Not used (NULL) |
| `access_token` | OAuth access token | Bearer token from client_credentials |
| `refresh_token` | OAuth refresh token | Not used (NULL) |
| `token_expires_at` | Token expiry from OAuth flow | `now() + 86400s` from token response |
| `token_request_count` | Not used (stays 0) | Incremented per token request |
| `token_request_window_start` | Not used (stays NULL) | Set on first token request in window |

#### Validation Rules

- `pms_type` MUST be a registered provider type string
- When `pms_type = "guesty"`: `api_key` and `refresh_token` MUST be NULL
- When `pms_type = "cloudbeds"`: either (`access_token` + `refresh_token`) OR `api_key` MUST be set
- `token_request_count` resets to 0 when `now() - token_request_window_start > 24 hours`

### Listing (listings table)

#### Column Rename

| Old Name | New Name | Type | Notes |
|----------|----------|------|-------|
| `cloudbeds_id` | `pms_id` | String(100) | UNIQUE, indexed; values preserved as-is |

#### Index Changes

| Old Index Name | New Index Name |
|---------------|----------------|
| `ix_listings_cloudbeds_id` | `ix_listings_pms_id` |

### Booking (bookings table)

#### Column Rename

| Old Name | New Name | Type | Notes |
|----------|----------|------|-------|
| `cloudbeds_booking_id` | `pms_booking_id` | String(255) | Part of unique constraint |

#### Constraint Changes

| Old Name | New Name | Columns |
|----------|----------|---------|
| `uq_booking_listing_cloudbeds` | `uq_booking_listing_pms` | (`listing_id`, `pms_booking_id`) |

### Room (rooms table)

#### Column Rename

| Old Name | New Name | Type | Notes |
|----------|----------|------|-------|
| `cloudbeds_room_id` | `pms_room_id` | String(100) | Part of unique constraint |

#### Constraint Changes

| Old Name | New Name | Columns |
|----------|----------|---------|
| `uq_room_listing_cloudbeds` | `uq_room_listing_pms` | (`listing_id`, `pms_room_id`) |

## Alembic Migration Specification

### Migration: `generalize_pms_columns`

**Direction**: One-way (upgrade only)

**Downgrade**: `raise NotImplementedError("One-way migration: cloudbeds_* → pms_*")`

#### Upgrade Steps (in order)

```python
# Step 1: oauth_credentials — add new columns
with op.batch_alter_table("oauth_credentials") as batch_op:
    batch_op.add_column(
        sa.Column("pms_type", sa.String(20), nullable=False, server_default="cloudbeds")
    )
    batch_op.add_column(
        sa.Column("token_request_count", sa.Integer, nullable=False, server_default="0")
    )
    batch_op.add_column(
        sa.Column("token_request_window_start", sa.DateTime, nullable=True)
    )

# Step 2: listings — rename cloudbeds_id → pms_id
with op.batch_alter_table("listings") as batch_op:
    batch_op.alter_column("cloudbeds_id", new_column_name="pms_id")
    # Indexes and unique constraints are recreated by batch mode

# Step 3: bookings — rename cloudbeds_booking_id → pms_booking_id
with op.batch_alter_table("bookings") as batch_op:
    batch_op.alter_column("cloudbeds_booking_id", new_column_name="pms_booking_id")

# Step 4: rooms — rename cloudbeds_room_id → pms_room_id
with op.batch_alter_table("rooms") as batch_op:
    batch_op.alter_column("cloudbeds_room_id", new_column_name="pms_room_id")
```

#### Data Preservation

- All column values are preserved exactly as-is during rename
- Existing credential records get `pms_type = "cloudbeds"` via server_default
- `token_request_count` defaults to 0
- `token_request_window_start` defaults to NULL
- All foreign key relationships preserved (batch mode handles this)
- `ical_url_slug` values unchanged — feed URLs remain stable

## Provider DTO Dataclasses

These are not persisted — they serve as the interface boundary between providers and the sync service.

```python
@dataclass(frozen=True)
class PMSListing:
    """Normalized listing from any PMS provider."""
    pms_id: str
    name: str
    timezone: str
    address: str | None = None
    rooms: list[PMSRoom] = field(default_factory=list)

@dataclass(frozen=True)
class PMSRoom:
    """Normalized room/unit from any PMS provider."""
    pms_room_id: str
    name: str
    room_type: str | None = None

@dataclass(frozen=True)
class PMSReservation:
    """Normalized reservation from any PMS provider."""
    pms_booking_id: str
    listing_pms_id: str
    guest_name: str | None
    guest_id: str | None
    check_in: datetime
    check_out: datetime
    status: str
    room_ids: list[str]
    custom_data: dict[str, Any]

@dataclass(frozen=True)
class PMSGuest:
    """Normalized guest from any PMS provider."""
    guest_id: str
    full_name: str
    phone: str | None = None
    email: str | None = None

@dataclass(frozen=True)
class TokenResult:
    """Result of a token refresh/acquire operation."""
    access_token: str
    refresh_token: str | None
    expires_at: datetime
```

## State Transitions

### Guesty Token Lifecycle

```text
                  ┌──────────────┐
                  │  No Token    │
                  │  (initial)   │
                  └──────┬───────┘
                         │ Request token (count=1)
                         ▼
                  ┌──────────────┐
                  │ Token Valid  │←──────────────────┐
                  │ (cached)     │                    │
                  └──────┬───────┘                    │
                         │ Token expires              │
                         │ (24h elapsed)              │
                         ▼                            │
                  ┌──────────────┐                    │
                  │ Token        │  count < 5         │
                  │ Expired      │──────→ Request ────┘
                  └──────┬───────┘   new token
                         │ count = 5
                         ▼
                  ┌──────────────┐
                  │ Rate Limited │  Wait for window
                  │ (deferred)   │──→ reset (24h)
                  └──────────────┘       │
                         ↑               │ Window resets
                         └───────────────┘ (count=0)
```

### Booking Sync State Machine

```text
┌────────────┐     ┌─────────────┐     ┌──────────────┐
│ Not in DB  │────→│  Inserted   │────→│   Updated    │
│            │     │  (new)      │     │  (changed)   │
└────────────┘     └─────────────┘     └──────────────┘
                         │                    │
                         │                    │
                         ▼                    ▼
                   ┌─────────────┐     ┌──────────────┐
                   │ Not in API  │────→│  Cancelled   │
                   │ response    │     │  (marked)    │
                   └─────────────┘     └──────────────┘
                                             │
                                             │ 30 days
                                             ▼
                                       ┌──────────────┐
                                       │   Purged     │
                                       │  (deleted)   │
                                       └──────────────┘
```

This state machine is identical for both Cloudbeds and Guesty providers — the sync service handles it generically using `pms_booking_id` as the identifier.
