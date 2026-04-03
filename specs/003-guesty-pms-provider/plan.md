<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->
# Implementation Plan: Guesty PMS Provider Integration

**Branch**: `003-guesty-pms-provider` | **Date**: 2025-07-15 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/003-guesty-pms-provider/spec.md`

## Summary

Add Guesty as a second PMS provider to rentalsync-bridge by introducing a pluggable PMS provider abstraction layer, generalizing the database schema from Cloudbeds-specific columns to provider-agnostic `pms_*` columns, implementing a GuestyService that communicates with the Guesty Open API using the appropriate endpoint versions, and updating the admin UI, configuration, and sync orchestration to be PMS-agnostic. The existing Cloudbeds integration is refactored to implement the same provider interface, ensuring backward compatibility with zero user intervention on upgrade.

## Technical Context

**Language/Version**: Python 3.13 (target-version in ruff/mypy config)
**Primary Dependencies**: FastAPI ≥0.115, SQLAlchemy[asyncio] ≥2.0, aiosqlite ≥0.21, httpx ≥0.28, APScheduler ≥3.11, icalendar ≥6.1, cryptography ≥44.0, Pydantic ≥2.10, pydantic-settings ≥2.7
**Storage**: SQLite via aiosqlite, WAL mode, Alembic migrations
**Testing**: pytest ≥8.3 with pytest-asyncio (asyncio_mode="auto"), pytest-cov; 30 existing test files across unit/integration/contract
**Target Platform**: Linux (Home Assistant add-on container, Alpine 3.21 base)
**Project Type**: Single project — FastAPI backend with embedded admin SPA (Jinja2 template + vanilla JS)
**Performance Goals**: Sync cycle completes within 60s for typical property sets (≤50 listings); ≤2 Guesty token requests per 24h under normal operations; iCal feed generation <500ms (cached: <5ms)
**Constraints**: Guesty token limit of 5 requests per key per 24h; Guesty API rate limits (429 with Retry-After); SQLite single-writer constraint; HA add-on memory budget ~128MB
**Scale/Scope**: Single-tenant (one PMS per installation), ≤50 listings, thousands of bookings, single SQLite database

## Constitution Check

*GATE: Must pass before Stage 0 research. Re-check after Stage 1 design.*

Verify feature design compliance with `.specify/memory/constitution.md`:

- [x] **Code Quality**: SPDX headers planned for all new source files; ruff, mypy strict, interrogate 100% enforced via pre-commit
- [x] **Testing Standards**: Test strategy defined — unit tests for provider abstraction + Guesty service, integration tests for API endpoints + sync flow, contract tests for iCal RFC 5545 compliance post-migration, migration tests for Alembic schema changes
- [x] **UX Consistency**: Admin UI follows existing Bootstrap SPA patterns; PMS selector uses consistent form elements; error messages follow existing standardized format
- [x] **Performance**: Measurable performance goals defined above; token caching strategy prevents exceeding 5-req/24h limit; pagination handles large datasets
- [x] **Commit Discipline**: Team aware of atomic commit and pre-commit requirements per AGENTS.md; DCO sign-off, Co-Authored-By trailers, 50-char subject limit, semantic types

*No violations — all principles satisfied.*

## Project Structure

### Documentation (this feature)

```text
specs/003-guesty-pms-provider/
├── plan.md              # This file
├── research.md          # Stage 0 output - Guesty API research, design decisions
├── data-model.md        # Stage 1 output - entity changes, migration design
├── quickstart.md        # Stage 1 output - developer setup guide
├── contracts/           # Stage 1 output - API contract changes
└── tasks.md             # Post-design output (/speckit.tasks command)
```

### Source Code (repository root)

```text
rentalsync-bridge/
├── src/
│   ├── config.py                          # Settings: add pms_type, guesty_* env vars
│   ├── database.py                        # Unchanged (async SQLite engine)
│   ├── models/
│   │   ├── oauth_credential.py            # Add pms_type column, token_request_count/window
│   │   ├── listing.py                     # Rename cloudbeds_id → pms_id
│   │   ├── booking.py                     # Rename cloudbeds_booking_id → pms_booking_id
│   │   ├── room.py                        # Rename cloudbeds_room_id → pms_room_id
│   │   ├── available_field.py             # Unchanged
│   │   ├── custom_field.py                # Unchanged
│   │   └── system_settings.py             # Unchanged
│   ├── providers/                         # NEW: PMS provider abstraction
│   │   ├── __init__.py
│   │   ├── base.py                        # PMSProvider ABC/Protocol definition
│   │   ├── registry.py                    # Provider registry + factory
│   │   ├── cloudbeds/                     # Refactored Cloudbeds provider
│   │   │   ├── __init__.py
│   │   │   └── service.py                 # CloudbedsProvider(PMSProvider)
│   │   └── guesty/                        # NEW: Guesty provider
│   │       ├── __init__.py
│   │       ├── service.py                 # GuestyProvider(PMSProvider)
│   │       └── auth.py                    # Guesty token manager (caching, rate tracking)
│   ├── repositories/
│   │   ├── listing_repository.py          # Update cloudbeds_id refs → pms_id
│   │   ├── booking_repository.py          # Update cloudbeds_booking_id refs → pms_booking_id
│   │   ├── room_repository.py             # Update cloudbeds_room_id refs → pms_room_id
│   │   ├── available_field_repository.py  # Unchanged
│   │   └── credential_repository.py       # NEW: credential CRUD with pms_type awareness
│   ├── services/
│   │   ├── calendar_service.py            # Update UID generation (cloudbeds_booking_id → pms_booking_id)
│   │   ├── cloudbeds_service.py           # Thin wrapper → delegates to providers/cloudbeds/
│   │   ├── oauth_service.py               # Generalize for multi-provider token refresh
│   │   ├── sync_service.py                # Decouple from CloudbedsService → use PMSProvider
│   │   └── scheduler.py                   # Use provider registry instead of direct CloudbedsService
│   ├── api/
│   │   ├── oauth.py                       # Add pms_type to configure/status endpoints
│   │   ├── listings.py                    # Replace Cloudbeds-specific sync with provider-agnostic
│   │   ├── rooms.py                       # Unchanged
│   │   ├── settings.py                    # Unchanged
│   │   ├── ical.py                        # Unchanged (already PMS-agnostic via Booking model)
│   │   └── custom_fields.py               # Unchanged
│   ├── middleware/                         # Unchanged
│   ├── templates/
│   │   └── admin.html                     # Add PMS type selector UI
│   └── static/
│       ├── js/admin.js                    # Add PMS-specific credential forms, provider selection
│       └── css/admin.css                  # Minor styling for PMS selector
├── alembic/
│   └── versions/
│       └── xxxx_generalize_pms_columns.py # NEW: Migration cloudbeds_* → pms_*, add pms_type
└── tests/
    ├── unit/
    │   ├── test_pms_provider_base.py      # NEW: Provider ABC contract tests
    │   ├── test_guesty_service.py         # NEW: GuestyProvider unit tests
    │   ├── test_guesty_auth.py            # NEW: Token manager tests
    │   ├── test_provider_registry.py      # NEW: Registry/factory tests
    │   ├── test_cloudbeds_service.py       # Update for refactored provider
    │   ├── test_sync_service.py           # Update for provider-agnostic sync
    │   └── ... (existing tests updated for pms_* renames)
    ├── integration/
    │   ├── test_guesty_sync.py            # NEW: Guesty sync integration
    │   ├── test_pms_migration.py          # NEW: Alembic migration tests
    │   ├── test_oauth_api.py              # Update for multi-provider
    │   ├── test_listings_api.py           # Update for provider-agnostic
    │   └── ... (existing tests updated)
    └── contract/
        └── test_ical_rfc5545.py           # Verify iCal still valid post-migration
```

**Structure Decision**: Single project layout preserved. New `src/providers/` package houses the PMS abstraction layer, with each provider in its own sub-package. This isolates provider-specific logic while the existing `src/services/` layer becomes a thin orchestration layer delegating to the active provider.

## Complexity Tracking

> No Constitution violations — no justification entries needed.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |

---

## Stage 0: Research

### Research Tasks

#### R1: Guesty Open API Authentication & Token Management

**Decision**: Use Guesty's OAuth 2.0 client_credentials grant via `POST https://open-api.guesty.com/oauth2/token`.

**Rationale**: This is the only supported authentication method for server-to-server Guesty integrations. The token endpoint accepts `client_id`, `client_secret`, and `grant_type=client_credentials` as form-encoded body parameters, returning a bearer token valid for 24 hours (86400 seconds). Guesty enforces a hard limit of 5 token requests per client ID per 24-hour window.

**Token Management Strategy**:
- Cache token in the OAuthCredential record with `token_expires_at`
- Add `token_request_count` (int) and `token_request_window_start` (datetime) columns to track daily usage
- Refresh only when expired (check `token_expires_at` before any API call)
- Log warning at 4th request; allow the 5th (final) request with a warning that the 24h limit is exhausted; log error and defer starting with the 6th request in the window
- Under normal operations, expect ≤2 token requests per 24h (startup + one expiry-driven refresh)

**Alternatives Considered**:
- Per-request token generation: Rejected — would exhaust 5-request limit in seconds
- External token cache (Redis): Rejected — overkill for single-tenant SQLite deployment

#### R2: Guesty API Data Retrieval Patterns

**Decision**: Use Guesty Open API v1 endpoints with limit/skip pagination.

**Rationale**: Guesty's REST API follows consistent patterns across all endpoints.

**Endpoint Mapping**:

| Operation | Guesty Endpoint | Pagination | Notes |
|-----------|----------------|------------|-------|
| List properties | `GET /v1/listings` | limit/skip (max 100) | Returns listing objects with `_id`, `title`, `address`, `timezone` |
| Get reservations | `GET /v1/reservations` | limit/skip (max 100) | Filter by `listingId`, `checkIn`/`checkOut` date ranges, status |
| Get guest details | `GET /v1/guests/{guestId}` | N/A (single record) | Separate call per unique guest; linked via `guestId` on reservation |
| Get custom fields | `GET /v1/reservations-v3/{id}/custom-fields` | N/A (per reservation) | V3 endpoint only — V2 deprecated April 2026 |
| Get listing details | `GET /v1/listings/{id}` | N/A | For sub-unit info on multi-unit listings |

**Pagination Strategy**:
- Default `limit=100` (maximum allowed), `skip` increments by 100
- Continue until response returns fewer results than `limit`
- Sort by `_id` for consistent ordering across pages

**Rate Limit Handling**:
- HTTP 429 responses include `Retry-After` header
- Implement exponential backoff: 1s → 2s → 4s (max 30s), matching existing Cloudbeds pattern
- Max 3 retries per request before failing the operation

**Alternatives Considered**:
- Cursor-based pagination: Not supported by Guesty API
- Bulk/webhook-based sync: Guesty webhooks exist but add complexity; polling is simpler for v1

#### R3: Guesty Data Model Mapping to Internal Schema

**Decision**: Map Guesty entities to existing internal models with normalization in the provider layer.

**Rationale**: The internal data model (Listing, Booking, Room) is already well-designed for multi-PMS use. The key differences between Cloudbeds and Guesty are in ID formats, field names, and data assembly patterns.

**Entity Mapping**:

| Internal Model | Guesty Source | Key Mappings |
|---------------|---------------|-------------- |
| Listing | `/v1/listings` | `_id` → `pms_id`, `title` → `name`, `timezone` → `timezone` |
| Room | Sub-units within multi-unit listing | `_id` → `pms_room_id`, `title` → `room_name`; single-unit → one implicit Room |
| Booking | `/v1/reservations` | `_id` → `pms_booking_id`, `checkIn` → `check_in_date`, `checkOut` → `check_out_date`, status mapped to internal enum |
| Guest | `/v1/guests/{id}` | `fullName` → `guest_name` on Booking; fallback to `"Guest [guestId]"` on 404 |
| Custom Fields | `/v1/reservations-v3/{id}/custom-fields` | `fieldId` + `value` → JSON in `custom_data` column |

**Status Mapping**:
| Guesty Status | Internal Status |
|--------------|----------------|
| `confirmed` | `confirmed` |
| `checked_in` | `checked_in` |
| `checked_out` | `checked_out` |
| `canceled` | `cancelled` |
| `inquiry`, `reserved` | Excluded from sync |

**ID Format Differences**:
- Cloudbeds: Short numeric strings (e.g., `"12345"`)
- Guesty: MongoDB ObjectID strings (e.g., `"507f1f77bcf86cd799439011"`, 24 hex chars)
- Solution: All `pms_*` columns use `String` type (already the case for existing `cloudbeds_*` columns)

**Multi-Unit Listing Handling**:
- Guesty multi-unit listings contain `childListings` or sub-units
- Each sub-unit maps to a Room record with its own `pms_room_id`
- Single-unit listings: Create one implicit Room (matching Cloudbeds behavior where each room is explicit)
- Multi-room booking composite IDs: `{reservationID}::{subUnitID}` (same pattern as Cloudbeds)

**Alternatives Considered**:
- Separate tables per PMS: Rejected — would require duplicating all downstream logic
- Polymorphic models: Rejected — adds ORM complexity without clear benefit for 2-3 providers

#### R4: PMS Provider Abstraction Design

**Decision**: Use Python ABC (Abstract Base Class) with a provider registry pattern.

**Rationale**: ABC provides clear contract enforcement at class definition time (vs Protocol which only checks at call site). The codebase already uses concrete class patterns that align well with ABC inheritance.

**Provider Interface** (`PMSProvider` ABC):

```python
class PMSProvider(ABC):
    """Abstract base class for PMS provider implementations."""

    @abstractmethod
    async def get_listings(self) -> list[PMSListing]: ...

    @abstractmethod
    async def get_reservations(
        self, listing_pms_id: str,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[PMSReservation]: ...

    @abstractmethod
    async def get_rooms(self, listing_pms_id: str) -> list[PMSRoom]: ...

    @abstractmethod
    async def get_guest(self, guest_id: str) -> PMSGuest | None: ...

    @abstractmethod
    async def get_custom_fields(self, reservation_id: str) -> dict[str, Any]: ...

    @abstractmethod
    async def refresh_token(self, credential: OAuthCredential) -> TokenResult: ...

    @abstractmethod
    async def test_connection(self) -> bool: ...

    @property
    @abstractmethod
    def provider_type(self) -> str: ...
```

**Intermediate DTOs** (dataclasses for provider → sync service boundary):
- `PMSListing(pms_id, name, timezone, address, rooms: list[PMSRoom])`
- `PMSReservation(pms_booking_id, listing_pms_id, guest_name, guest_id, check_in, check_out, status, room_ids, custom_data)`
- `PMSRoom(pms_room_id, name, room_type)`
- `PMSGuest(guest_id, full_name, phone, email)`
- `TokenResult(access_token, refresh_token, expires_at)`

**Registry Pattern**:
```python
_providers: dict[str, type[PMSProvider]] = {}

def register_provider(pms_type: str, cls: type[PMSProvider]) -> None: ...
def get_provider(pms_type: str, credential: OAuthCredential) -> PMSProvider: ...
```

**Alternatives Considered**:
- Protocol (structural typing): Rejected — ABC gives clearer errors when a provider forgets to implement a method
- Strategy pattern with functions: Rejected — too many methods; class-based grouping is cleaner
- Plugin system with entry_points: Rejected — overkill for 2-3 built-in providers

#### R5: Database Migration Strategy

**Decision**: Single Alembic migration that renames columns, adds `pms_type`, and updates constraints. One-way (no downgrade).

**Rationale**: The migration must be atomic and safe for SQLite. SQLite doesn't support `ALTER COLUMN RENAME` directly, so Alembic's batch mode (which recreates tables) is required — this is already the established pattern in the existing migrations.

**Migration Steps** (in order):

1. **Add `pms_type` to `oauth_credentials`**: Default `"cloudbeds"` for existing records
2. **Add token tracking columns to `oauth_credentials`**: `token_request_count` (Integer, default 0), `token_request_window_start` (DateTime, nullable)
3. **Rename in `listings`**: `cloudbeds_id` → `pms_id` (preserve index, unique constraint)
4. **Rename in `bookings`**: `cloudbeds_booking_id` → `pms_booking_id` (preserve unique constraint)
5. **Rename in `rooms`**: `cloudbeds_room_id` → `pms_room_id` (preserve unique constraint)
6. **Update unique constraint names** to use `pms_` prefix

**Data Preservation**: All existing values copied as-is during table recreation. `pms_type` defaults to `"cloudbeds"` for existing credential records.

**Downgrade**: `raise NotImplementedError("One-way migration")` — documented in spec as non-reversible.

**Testing**: Dedicated migration test that creates pre-migration schema, inserts test data, runs migration, and verifies all data preserved with new column names.

**Alternatives Considered**:
- Add new columns + copy data + drop old: More complex, same end result for SQLite batch mode
- Two separate migrations (rename first, add pms_type second): Unnecessary complexity; atomic is safer

#### R6: Configuration & Environment Variable Design

**Decision**: Add `PMS_TYPE` env var and `GUESTY_CLIENT_ID`/`GUESTY_CLIENT_SECRET` env vars while preserving existing `CLOUDBEDS_*` vars.

**Rationale**: Backward compatibility requires keeping `CLOUDBEDS_*` vars working. The new `PMS_TYPE` env var determines which provider is active.

**New Settings**:

| Variable | Default | Description |
|----------|---------|-------------|
| `PMS_TYPE` | `"cloudbeds"` | Active PMS provider (`"cloudbeds"` or `"guesty"`) |
| `GUESTY_CLIENT_ID` | `""` | Guesty OAuth client ID |
| `GUESTY_CLIENT_SECRET` | `""` | Guesty OAuth client secret |

**Existing (preserved)**:
| Variable | Behavior |
|----------|----------|
| `CLOUDBEDS_CLIENT_ID` | Still used when `PMS_TYPE=cloudbeds` |
| `CLOUDBEDS_CLIENT_SECRET` | Still used when `PMS_TYPE=cloudbeds` |

**Auto-Detection Logic**: If `PMS_TYPE` is not set explicitly but `GUESTY_CLIENT_ID` is set, infer `PMS_TYPE=guesty`. If `CLOUDBEDS_CLIENT_ID` is set (existing installs), infer `PMS_TYPE=cloudbeds`. Explicit `PMS_TYPE` always wins.

**Alternatives Considered**:
- Single `PMS_CLIENT_ID`/`PMS_CLIENT_SECRET` pair: Rejected — different providers may need different credential shapes
- YAML config file: Rejected — env vars are the established pattern for HA add-ons

---

## Stage 1: Design & Contracts

### Data Model

#### Entity: OAuthCredential (modified)

| Field | Type | Change | Notes |
|-------|------|--------|-------|
| `id` | Integer PK | Unchanged | |
| `pms_type` | String(20) | **NEW** | `"cloudbeds"` or `"guesty"`, NOT NULL, default `"cloudbeds"` |
| `client_id` | String(255) | Unchanged | UNIQUE, NOT NULL |
| `_client_secret` | String(255) | Unchanged | Fernet encrypted |
| `_api_key` | Text | Unchanged | Fernet encrypted, Cloudbeds only |
| `_access_token` | Text | Unchanged | Fernet encrypted |
| `_refresh_token` | Text | Unchanged | Fernet encrypted, Cloudbeds only (Guesty uses client_credentials, no refresh token) |
| `token_expires_at` | DateTime | Unchanged | |
| `token_request_count` | Integer | **NEW** | Default 0, tracks Guesty 24h token requests |
| `token_request_window_start` | DateTime | **NEW** | Nullable, start of current 24h tracking window |
| `created_at` | DateTime | Unchanged | |
| `updated_at` | DateTime | Unchanged | |

**Validation Rules**:
- `pms_type` must be one of the registered provider types
- Guesty credentials require: `client_id`, `client_secret` (no `api_key`, no `refresh_token`)
- Cloudbeds credentials require: `client_id`, `client_secret`, and either `access_token`+`refresh_token` or `api_key`

#### Entity: Listing (modified)

| Field | Type | Change | Notes |
|-------|------|--------|-------|
| `id` | Integer PK | Unchanged | |
| `pms_id` | String(100) | **RENAMED** from `cloudbeds_id` | UNIQUE, indexed |
| `name` | String(255) | Unchanged | |
| `enabled` | Boolean | Unchanged | |
| `ical_url_slug` | String(100) | Unchanged | UNIQUE, indexed — feed URLs preserved |
| `timezone` | String(50) | Unchanged | |
| `sync_enabled` | Boolean | Unchanged | |
| `last_sync_at` | DateTime | Unchanged | |
| `last_sync_error` | Text | Unchanged | |
| `created_at` | DateTime | Unchanged | |
| `updated_at` | DateTime | Unchanged | |

**Relationships**: Unchanged (bookings, rooms, custom_fields, available_fields cascade)

#### Entity: Booking (modified)

| Field | Type | Change | Notes |
|-------|------|--------|-------|
| `id` | Integer PK | Unchanged | |
| `listing_id` | Integer FK | Unchanged | |
| `room_id` | Integer FK | Unchanged | |
| `pms_booking_id` | String(255) | **RENAMED** from `cloudbeds_booking_id` | Composite IDs: `{resID}::{roomID}` |
| `guest_name` | String(255) | Unchanged | |
| `guest_phone_last4` | String(4) | Unchanged | |
| `check_in_date` | DateTime | Unchanged | |
| `check_out_date` | DateTime | Unchanged | |
| `status` | String(50) | Unchanged | |
| `custom_data` | JSON | Unchanged | |
| `last_fetched_at` | DateTime | Unchanged | |
| `created_at` | DateTime | Unchanged | |
| `updated_at` | DateTime | Unchanged | |

**Unique Constraint**: `uq_booking_listing_pms` on (`listing_id`, `pms_booking_id`) — renamed from `uq_booking_listing_cloudbeds`

#### Entity: Room (modified)

| Field | Type | Change | Notes |
|-------|------|--------|-------|
| `id` | Integer PK | Unchanged | |
| `listing_id` | Integer FK | Unchanged | |
| `pms_room_id` | String(100) | **RENAMED** from `cloudbeds_room_id` | |
| `room_name` | String(255) | Unchanged | |
| `room_type_name` | String(255) | Unchanged | |
| `ical_url_slug` | String(100) | Unchanged | Feed URLs preserved |
| `enabled` | Boolean | Unchanged | |
| `created_at` | DateTime | Unchanged | |
| `updated_at` | DateTime | Unchanged | |

**Unique Constraints**:
- `uq_room_listing_slug` on (`listing_id`, `ical_url_slug`) — unchanged
- `uq_room_listing_pms` on (`listing_id`, `pms_room_id`) — renamed from `uq_room_listing_cloudbeds`

### API Contracts

#### Modified Endpoint: `POST /api/oauth/configure`

**Request** (updated):
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

New required field `pms_type` (enum: `"cloudbeds"`, `"guesty"`). Defaults to `"cloudbeds"` if omitted for backward compatibility.

**Response** (unchanged structure):
```json
{
  "status": "connected",
  "pms_type": "guesty",
  "message": "Guesty credentials configured successfully"
}
```

#### Modified Endpoint: `GET /api/oauth/status`

**Response** (updated):
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

New fields: `pms_type`, `token_requests_remaining` (Guesty-specific, null for Cloudbeds).

#### Modified Endpoint: `POST /api/listings/sync-properties`

No request body change. Behavior change: Uses the active provider
(determined by stored `pms_type` on credential) to fetch listings instead
of hardcoded Cloudbeds. Response shape is updated to use provider-agnostic
fields; specifically, `cloudbeds_id` is renamed to `pms_id`. See
`contracts/api-contracts.md` for the canonical response schema.

#### New Endpoint: `GET /api/providers`

See `contracts/api-contracts.md` for the canonical response schema. The response includes provider metadata with rich `credential_fields` objects (containing `name`, `label`, `type`, `required`) used by the admin UI to dynamically render provider-specific forms.

#### Unchanged Endpoints

All iCal endpoints (`GET /ical/{listing_slug}/{room_slug}.ics`) remain unchanged — they operate on the Booking model which is already PMS-agnostic in its content. Only the ID column name changes internally.

All room endpoints (`GET/PATCH /api/rooms/{room_id}`) remain unchanged.

All custom field endpoints remain unchanged.

All settings endpoints remain unchanged.

### Provider Interface Contract

```python
# src/providers/base.py

@dataclass(frozen=True)
class PMSListing:
    pms_id: str
    name: str
    timezone: str
    address: str | None = None
    rooms: list[PMSRoom] = field(default_factory=list)

@dataclass(frozen=True)
class PMSRoom:
    pms_room_id: str
    name: str
    room_type: str | None = None

@dataclass(frozen=True)
class PMSReservation:
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
    guest_id: str
    full_name: str
    phone: str | None = None
    email: str | None = None

@dataclass(frozen=True)
class TokenResult:
    access_token: str
    refresh_token: str | None
    expires_at: datetime

class PMSProvider(ABC):
    @abstractmethod
    async def get_listings(self) -> list[PMSListing]: ...

    @abstractmethod
    async def get_reservations(
        self, listing_pms_id: str, *,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[PMSReservation]: ...

    @abstractmethod
    async def get_rooms(self, listing_pms_id: str) -> list[PMSRoom]: ...

    @abstractmethod
    async def get_guest(self, guest_id: str) -> PMSGuest | None: ...

    @abstractmethod
    async def get_custom_fields(self, reservation_id: str) -> dict[str, Any]: ...

    @abstractmethod
    async def refresh_token(self, credential: OAuthCredential) -> TokenResult: ...

    @abstractmethod
    async def test_connection(self) -> bool: ...

    @property
    @abstractmethod
    def provider_type(self) -> str: ...
```

### Quickstart (Developer Guide)

See `quickstart.md` for developer setup instructions including:
- Environment variable configuration for Guesty development
- Test data setup with mock Guesty API responses
- Running the migration on a development database
- Testing both Cloudbeds and Guesty paths

---

## Implementation Phases

### Phase 1: PMS Provider Abstraction Layer

**Goal**: Create the provider interface and refactor Cloudbeds to implement it.

**Tasks**:
1. Create `src/providers/` package with `base.py` (ABC + DTOs), `registry.py` (factory)
2. Create `src/providers/cloudbeds/service.py` — `CloudbedsProvider(PMSProvider)` wrapping existing `CloudbedsService`
3. Write unit tests for provider ABC contract and registry
4. Verify existing Cloudbeds tests still pass through the provider layer

**Dependencies**: None — this is the foundation.

### Phase 2: Database Schema Generalization

**Goal**: Migrate from `cloudbeds_*` columns to `pms_*` columns with Alembic.

**Tasks**:
1. Create Alembic migration: rename columns, add `pms_type`, add token tracking columns
2. Update all SQLAlchemy models (`Listing`, `Booking`, `Room`, `OAuthCredential`)
3. Update all repositories (`ListingRepository`, `BookingRepository`, `RoomRepository`)
4. Update `CalendarService` UID generation
5. Update all existing tests for new column names
6. Write migration tests (pre-migration data → post-migration verification)

**Dependencies**: Phase 1 (provider types needed for `pms_type` column values).

### Phase 3: Configuration & Credential Updates

**Goal**: Support multi-provider configuration through env vars and admin API.

**Tasks**:
1. Add `PMS_TYPE`, `GUESTY_CLIENT_ID`, `GUESTY_CLIENT_SECRET` to `Settings`
2. Add auto-detection logic for `PMS_TYPE`
3. Update `POST /api/oauth/configure` to accept `pms_type`
4. Update `GET /api/oauth/status` to return `pms_type`
5. Add `GET /api/providers` endpoint
6. Create `CredentialRepository` with provider-aware CRUD
7. Update `OAuthService` for multi-provider token refresh routing
8. Write tests for new config, endpoints, and credential logic

**Dependencies**: Phase 2 (models must have `pms_type` column).

### Phase 4: Guesty Service Implementation

**Goal**: Implement the Guesty provider with full API integration.

**Tasks**:
1. Create `src/providers/guesty/auth.py` — token manager with caching and rate tracking
2. Create `src/providers/guesty/service.py` — `GuestyProvider(PMSProvider)` with all endpoints
3. Implement pagination for listings and reservations
4. Implement guest detail assembly (separate API call per unique guest)
5. Implement custom fields retrieval (v3 endpoint per reservation)
6. Implement 429 rate limit handling with exponential backoff
7. Register Guesty provider in registry
8. Write comprehensive unit tests with mocked HTTP responses

**Dependencies**: Phase 1 (provider interface to implement), Phase 3 (credential storage).

### Phase 5: Sync Service Refactoring

**Goal**: Make SyncService and SyncScheduler PMS-agnostic.

**Tasks**:
1. Refactor `SyncService.sync_listing()` to accept `PMSProvider` instead of creating `CloudbedsService`
2. Refactor `SyncScheduler._sync_all_listings()` to use provider registry
3. Refactor `SyncScheduler._refresh_token()` to delegate to provider
4. Update `_process_reservations()` to work with `PMSReservation` DTOs
5. Update `_extract_booking_data()` to accept normalized DTOs (or remove if redundant)
6. Ensure guest name assembly works for both providers (Cloudbeds inline, Guesty separate call)
7. Update all sync-related tests

**Dependencies**: Phase 2 (model renames), Phase 4 (Guesty provider available).

### Phase 6: Admin UI Updates

**Goal**: Add PMS provider selection and provider-specific credential forms.

**Tasks**:
1. Update `admin.html` — add PMS type selector dropdown
2. Update `admin.js` — fetch providers from `/api/providers`, render dynamic forms
3. Show provider-specific fields (Cloudbeds: OAuth + API key; Guesty: client ID + secret only)
4. Display `pms_type` in status section
5. Show Guesty-specific status info (token requests remaining)
6. Update connection test to use provider-specific validation
7. Write integration tests for UI-facing endpoints

**Dependencies**: Phase 3 (API endpoints), Phase 4 (Guesty provider for connection test).

### Phase 7: Testing & Integration

**Goal**: Comprehensive test coverage for the full multi-PMS flow.

**Tasks**:
1. End-to-end integration test: Guesty setup → sync → iCal feed verification
2. Migration test: simulate upgrade from Cloudbeds-only install
3. Backward compatibility test: existing Cloudbeds flow works after all changes
4. Edge case tests: Guesty 404 guest, empty rooms, token limit exhaustion, mid-sync restart
5. Contract test: iCal RFC 5545 compliance with Guesty-sourced data
6. Update test fixtures (conftest.py) for multi-provider support

**Dependencies**: All previous phases.

---

## Cross-Cutting Concerns

### Performance & Observability

**Deferred from spec clarification** — these are design considerations, not implementation blockers.

**Logging Strategy**:
- All provider API calls logged at DEBUG with timing
- Token lifecycle events (request, cache hit, expiry, rate limit warning) logged at INFO/WARNING
- Sync summary (inserted/updated/cancelled per listing) logged at INFO (existing pattern)
- Rate limit encounters logged at WARNING with retry-after details

**Metrics** (future — not in v1 scope):
- Sync duration per listing
- API call count per sync cycle
- Token request count per 24h window
- Cache hit rate for iCal feeds

**Error Handling Patterns**:
- Provider errors wrapped in provider-specific exceptions that inherit from a common `PMSProviderError`
- SyncService catches `PMSProviderError` and persists error state (existing pattern)
- Admin UI displays provider-specific error messages

### Security Considerations

- All Guesty credentials (client_id, client_secret, access_token) Fernet-encrypted at rest — same as existing Cloudbeds pattern
- No credentials in logs (existing practice)
- Token caching prevents unnecessary network round-trips
- API key authentication remains Cloudbeds-only

### Backward Compatibility Guarantees

1. **iCal URLs**: `ical_url_slug` on Listing and Room models is unchanged — all existing feed URLs work
2. **Environment Variables**: `CLOUDBEDS_CLIENT_ID`, `CLOUDBEDS_CLIENT_SECRET` continue to work without any `PMS_TYPE` setting
3. **Database**: Migration auto-sets `pms_type="cloudbeds"` on existing credential records
4. **API**: All existing API endpoints accept the same request formats; `pms_type` is optional in `/api/oauth/configure` (defaults to `"cloudbeds"`)
5. **Admin UI**: Existing Cloudbeds-configured installations show "Cloudbeds" as active provider immediately after upgrade
