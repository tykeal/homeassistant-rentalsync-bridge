<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->
# Research: Guesty PMS Provider Integration

**Feature Branch**: `003-guesty-pms-provider`
**Date**: 2025-07-15
**Status**: Complete

## R1: Guesty Open API Authentication & Token Management

### Context
Guesty uses OAuth 2.0 client credentials flow for server-to-server authentication. This differs from Cloudbeds which uses authorization_code flow with refresh tokens.

### Findings

**Token Endpoint**: `POST https://open-api.guesty.com/oauth2/token`

**Request**:
- Content-Type: `application/x-www-form-urlencoded`
- Body: `grant_type=client_credentials`, `client_id`, `client_secret`

**Response**:
```json
{
  "token_type": "Bearer",
  "expires_in": 86400,
  "access_token": "...",
  "scope": "api"
}
```

**Constraints**:
- Token validity: 24 hours (86400 seconds)
- **Hard limit**: 5 token requests per client ID per 24-hour rolling window
- No refresh_token — client_credentials grant issues new tokens each time
- Exceeding the 5-request limit results in HTTP 429 from the token endpoint itself

**Sources**:
- [Guesty Authentication Docs](https://open-api-docs.guesty.com/docs/authentication)
- [Guesty Quick Start Guide](https://open-api-docs.guesty.com/docs/quick-start-guide)

### Decision: Token Caching Strategy

**Chosen approach**: Persist token in OAuthCredential with tracking columns.

- Store `access_token` and `token_expires_at` in the existing credential record
- Add `token_request_count` (Integer) and `token_request_window_start` (DateTime) columns
- Before requesting a new token: check if cached token is still valid
- On token request: increment counter, set window start if first request
- At 4th request in window: log WARNING about approaching limit
- At 5th request: allow and log WARNING that the final allowed token request has been used
- At 6th+ request: log ERROR and defer until window resets
- Window reset: when `now - token_request_window_start > 24 hours`, reset counter to 0

**Why not alternatives**:
- Redis cache: Overkill — single-tenant SQLite app, no concurrent token issuance
- In-memory only: Token would be lost on add-on restart, forcing unnecessary new request
- Per-request token: Would exhaust limit within seconds

---

## R2: Guesty API Data Endpoints & Pagination

### Context
Need to map Guesty API endpoints to the existing sync workflow: list properties → get rooms → get reservations → assemble guest data.

### Findings

**Base URL**: `https://open-api.guesty.com`

**Authorization**: `Authorization: Bearer {access_token}` on all requests.

#### Listings Endpoint
```
GET /v1/listings?limit=100&skip=0&fields=_id,title,address,timezone
```
- Pagination: `limit` (max 100), `skip` (offset)
- Response: Paginated object wrapper `{results: [...], count, limit, skip}` with `results` array of listing objects plus pagination metadata
- Key fields: `_id`, `title`, `address` (nested object), `timezone`, `type` ("single"/"multi"), `childListings` (for multi-unit)

#### Reservations Endpoint
```
GET /v1/reservations?listingId={id}&limit=100&skip=0&sort=_id
```
- Filter parameters: `listingId`, `checkIn` (date range), `checkOut` (date range), `status`
- Status values: `confirmed`, `checked_in`, `checked_out`, `canceled`, `inquiry`, `reserved`
- Pagination: same limit/skip pattern
- Key fields: `_id`, `listingId`, `checkIn`, `checkOut`, `status`, `guestId`, `money`, `nightsCount`
- Guest info NOT embedded — requires separate call via `guestId`

#### Guests Endpoint
```
GET /v1/guests/{guestId}
```
- Single guest by ID
- Key fields: `_id`, `fullName`, `firstName`, `lastName`, `phone`, `email`
- May return 404 if guest record deleted

#### Custom Fields Endpoint (V3 only)
```
GET /v1/reservations-v3/{reservationId}/custom-fields
```
- Returns array of `{ fieldId, value, _id }` per reservation
- V2 endpoint deprecated as of April 2026 — MUST NOT be used
- One request per reservation (no bulk endpoint)

#### Listing Details (for multi-unit)
```
GET /v1/listings/{listingId}
```
- Returns full listing object including `childListings` for multi-unit
- Each child listing has its own `_id`, `title`, used to create Room records

**Sources**:
- [Guesty Reservations Search](https://open-api-docs.guesty.com/docs/how-to-search-for-reservations)
- [Guesty Custom Fields Migration](https://open-api-docs.guesty.com/docs/custom-reservation-fields-migration)

### Decision: Pagination Strategy

**Chosen approach**: Sequential limit/skip with `limit=100`.

- Fetch with `skip=0, limit=100`, then `skip=100, limit=100`, etc.
- Stop when response length < limit (indicates last page)
- Sort by `_id` for consistent ordering
- For reservations: filter by `listingId` to reduce result sets

**Why not alternatives**:
- Cursor-based: Not supported by Guesty API
- Parallel fetching: Risk of rate limiting; sequential is safer for 5-token/day constraint

### Decision: Guest Data Assembly

**Chosen approach**: Batch unique guest IDs, fetch once per sync cycle.

- During reservation processing, collect unique `guestId` values
- Fetch each guest once (deduplicate across reservations sharing same guest)
- Cache guest lookups in memory for the duration of sync cycle
- On 404: store `"Guest [guestId]"` as fallback name, log WARNING
- Apply guest name to all reservations referencing that guest

**Why not alternatives**:
- Fetch per reservation: Many reservations share guests — excessive API calls
- Skip guest details: Spec requires guest name in iCal feeds for feature parity

---

## R3: Guesty Data Model Mapping

### Context
Guesty's data model differs from Cloudbeds in ID formats, field names, nested structures, and how guest information is associated with reservations.

### Findings

#### ID Format Differences
| Entity | Cloudbeds ID | Guesty ID |
|--------|-------------|-----------|
| Listing | Short numeric string: `"12345"` | MongoDB ObjectID: `"507f1f77bcf86cd799439011"` (24 hex chars) |
| Room | Short numeric string: `"67890"` | MongoDB ObjectID: same format |
| Booking/Reservation | Short numeric string: `"11111"` | MongoDB ObjectID: same format |
| Guest | Integer embedded in reservation | MongoDB ObjectID: separate endpoint |

All existing `cloudbeds_*` columns already use `String` type, so Guesty's longer IDs fit without schema changes beyond the rename.

#### Multi-Unit Listing Mapping
- Guesty `type="single"`: One listing → one implicit Room record
- Guesty `type="multi"`: Parent listing with `childListings` array → each child maps to a Room
- Room `pms_room_id` = child listing `_id`
- Room `room_name` = child listing `title`
- This matches the existing Cloudbeds pattern where each room in a property has its own ID

#### Status Mapping
| Guesty | Internal | Sync Behavior |
|--------|----------|--------------|
| `confirmed` | `confirmed` | Synced |
| `checked_in` | `checked_in` | Synced |
| `checked_out` | `checked_out` | Synced |
| `canceled` | `cancelled` | Marks existing bookings as cancelled |
| `inquiry` | — | Excluded (not a confirmed booking) |
| `reserved` | — | Excluded (pre-confirmation hold) |

#### Date Format Normalization
- Guesty dates: ISO 8601 strings (e.g., `"2025-07-15T14:00:00.000Z"`)
- Cloudbeds dates: `"YYYY-MM-DD"` format
- Internal model: Python `datetime` objects
- The existing `_parse_date()` in SyncService already handles both formats

### Decision: Use Provider-Layer Normalization

Each provider implementation normalizes its API response into the `PMS*` DTO dataclasses before passing to the sync service. This means:
- SyncService never sees raw API responses
- Date parsing, ID extraction, status mapping all happen in the provider
- Provider DTOs use Python native types (datetime, str, list)

---

## R4: PMS Provider Abstraction Pattern

### Context
Need to abstract the Cloudbeds-specific service into a pluggable interface that Guesty (and future providers) can implement.

### Findings

#### Current Coupling Points (Cloudbeds-specific)
1. `SyncService.__init__` → no direct coupling (receives credential)
2. `SyncService.sync_listing()` → creates `CloudbedsService()` directly
3. `SyncService._extract_booking_data()` → parses Cloudbeds-specific field names
4. `SyncScheduler._sync_all_listings()` → creates `SyncService` and uses it
5. `SyncScheduler._refresh_token()` → creates `CloudbedsService` for token refresh
6. API endpoints (`listings.py`) → `CloudbedsService` for property sync
7. Admin JS → hardcoded "Cloudbeds" references

#### Refactoring Approach

**Step 1**: Define `PMSProvider` ABC with method signatures matching current `CloudbedsService` public methods but using normalized DTOs.

**Step 2**: Create `CloudbedsProvider(PMSProvider)` that wraps the existing `CloudbedsService` and maps raw API responses to DTOs.

**Step 3**: Create provider registry that maps `pms_type` string to provider class.

**Step 4**: Modify `SyncService.sync_listing()` to accept a `PMSProvider` instance instead of creating `CloudbedsService`.

**Step 5**: Modify `SyncScheduler` to look up the provider from the credential's `pms_type` and pass it to `SyncService`.

### Decision: ABC over Protocol

**Chosen**: `abc.ABC` with `@abstractmethod`

**Rationale**:
- ABC raises `TypeError` at instantiation if abstract methods not implemented — catches bugs earlier
- Protocol only checks at call site (or with `runtime_checkable` + `isinstance()` which has limitations)
- The codebase uses concrete classes throughout — ABC fits the existing style
- Only 2-3 providers expected — no need for structural typing flexibility

**Why not alternatives**:
- Protocol: Better for duck typing / third-party plugins — not needed here
- Function registry: Too fragmented for 7+ related methods
- Dependency injection framework: Overkill for this scale

---

## R5: Database Migration Strategy

### Context
Need to rename `cloudbeds_*` columns to `pms_*` across three tables and add new columns to `oauth_credentials`. SQLite requires table recreation for column renames.

### Findings

#### SQLite Limitations
- No `ALTER TABLE ... RENAME COLUMN` (added in SQLite 3.25.0, but Alembic batch mode is more reliable)
- Alembic's batch mode recreates the table: creates temp table → copies data → drops original → renames temp
- All existing migrations already use batch mode (e.g., `0eeb46d10f64_add_rooms_table`)

#### Migration Scope
1. `oauth_credentials` table: Add 3 new columns (`pms_type`, `token_request_count`, `token_request_window_start`)
2. `listings` table: Rename `cloudbeds_id` → `pms_id`, update index names
3. `bookings` table: Rename `cloudbeds_booking_id` → `pms_booking_id`, update constraint names
4. `rooms` table: Rename `cloudbeds_room_id` → `pms_room_id`, update constraint names

#### Data Preservation
- All column values copied as-is (types don't change, just names)
- `pms_type` defaults to `"cloudbeds"` for any existing credential records
- `token_request_count` defaults to 0
- `token_request_window_start` defaults to NULL

### Decision: Single Migration, No Downgrade

**Chosen**: One Alembic migration file handling all changes. `downgrade()` raises `NotImplementedError`.

**Rationale**:
- Spec explicitly states one-way migration
- Atomic migration reduces risk of partial state
- Batch mode handles all changes safely for SQLite

**Migration Testing Plan**:
1. Create test DB with pre-migration schema
2. Insert sample data (credentials, listings with cloudbeds_id, bookings, rooms)
3. Run migration
4. Verify: all data preserved, columns renamed, new columns have defaults
5. Verify: indexes and constraints functional with new names

---

## R6: Configuration Design

### Context
Need to support Guesty credentials alongside existing Cloudbeds configuration without breaking existing installations.

### Findings

#### Current Config (Settings class in config.py)
- `cloudbeds_client_id`, `cloudbeds_client_secret` — loaded from `CLOUDBEDS_CLIENT_ID`, `CLOUDBEDS_CLIENT_SECRET` env vars
- Pydantic BaseSettings with `.env` file support

#### New Settings Needed
- `pms_type` — from `PMS_TYPE` env var, default `"cloudbeds"`
- `guesty_client_id` — from `GUESTY_CLIENT_ID` env var, default `""`
- `guesty_client_secret` — from `GUESTY_CLIENT_SECRET` env var, default `""`

### Decision: Env Vars with Auto-Detection

**Chosen**: Add new env vars with intelligent defaults.

```python
pms_type: str = Field(default="", description="PMS provider type")
guesty_client_id: str = Field(default="", ...)
guesty_client_secret: str = Field(default="", ...)
```

**Auto-detection logic** (computed property or validator):
1. If `PMS_TYPE` is explicitly set → use it
2. Else if `GUESTY_CLIENT_ID` is set and non-empty → `"guesty"`
3. Else → `"cloudbeds"` (backward compatible default)

This ensures existing Cloudbeds installations that only set `CLOUDBEDS_*` vars continue to work without adding `PMS_TYPE=cloudbeds`.
