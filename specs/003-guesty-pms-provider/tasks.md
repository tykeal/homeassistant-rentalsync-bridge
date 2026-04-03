<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->
# Tasks: Guesty PMS Provider Integration

**Input**: Design documents from `/specs/003-guesty-pms-provider/`
**Prerequisites**: plan.md ✓, spec.md ✓, research.md ✓, data-model.md ✓, contracts/ ✓, quickstart.md ✓

**Tests**: Included per constitution mandate (test-first discipline) and plan requirements.

**Note**: Per constitution Principle II, test tasks marked with [P] (parallelizable) that correspond to implementation tasks in the same phase SHOULD be executed first (test-first discipline). Write tests, verify they fail, then implement.

**Organization**: Tasks follow the 7-phase implementation plan structure. Phases 1–2 are foundational (no user story labels). Phases 3–6 implement user stories (labeled). Phase 7 is polish/integration (no labels).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[US1–US5]**: Maps to user stories from spec.md:
  - **US1** (P1): Guesty Property Owner Sets Up Booking Sync
  - **US2** (P2): Existing Cloudbeds User Upgrades Without Disruption
  - **US3** (P3): Admin Manages PMS Provider Selection
  - **US4** (P4): System Handles Guesty API Constraints Gracefully
  - **US5** (P5): Guesty Guest Details and Multi-Endpoint Data Assembly
- Include exact file paths in descriptions

## Path Conventions

> **Note**: All paths in this task list are relative to the `rentalsync-bridge/` directory unless otherwise stated.

- **Single project**: `src/`, `tests/` at repository root
- Provider modules: `src/providers/{provider}/`
- Migrations: `alembic/versions/`

---

## Phase 1: PMS Provider Abstraction Layer (Foundational)

**Purpose**: Define the provider interface, DTOs, exception hierarchy, and registry. Refactor existing Cloudbeds integration to implement the new abstraction.

**Dependencies**: None — this is the foundation.

- [ ] T001 Create src/providers/ package with PMSProvider ABC, DTO dataclasses (PMSListing, PMSRoom, PMSReservation, PMSGuest, TokenResult), and exception hierarchy (PMSProviderError, PMSAuthenticationError, PMSRateLimitError, TokenRateLimitError, PMSConnectionError) defined in src/providers/base.py and optionally re-exported from src/providers/__init__.py
- [ ] T002 Create provider registry with register_provider, get_provider_class, create_provider, and list_providers functions in src/providers/registry.py
- [ ] T003 [P] Write unit tests for PMSProvider ABC contract enforcement and DTO frozen dataclass behavior in tests/unit/test_pms_provider_base.py
- [ ] T004 [P] Write unit tests for provider registry: registration, lookup, factory creation, duplicate-registration error, and unknown-type error in tests/unit/test_provider_registry.py
- [ ] T005 Implement CloudbedsProvider(PMSProvider) wrapping existing CloudbedsService with DTO normalization for all abstract methods in src/providers/cloudbeds/__init__.py and src/providers/cloudbeds/service.py
- [ ] T006 Write unit tests for CloudbedsProvider DTO mapping and CloudbedsService delegation in tests/unit/test_cloudbeds_provider.py

**Checkpoint**: Provider abstraction layer complete. CloudbedsProvider passes all interface contract tests. Existing Cloudbeds test scenarios work through the new provider layer.

---

## Phase 2: Database Schema Generalization (Foundational)

**Purpose**: Migrate from cloudbeds_* columns to provider-agnostic pms_* columns, add pms_type discriminator and token tracking, and update all data access code.

**Dependencies**: Phase 1 (provider type strings needed for pms_type column values).

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T007 [P] Add pms_type (String(20), NOT NULL, default "cloudbeds"), token_request_count (Integer, default 0), and token_request_window_start (DateTime, nullable) columns to OAuthCredential model in src/models/oauth_credential.py
- [ ] T008 [P] Rename cloudbeds_id to pms_id (String(100), UNIQUE, indexed) on Listing model and update ListingRepository queries in src/models/listing.py and src/repositories/listing_repository.py
- [ ] T009 [P] Rename cloudbeds_booking_id to pms_booking_id (String(255)) on Booking model, update unique constraint to uq_booking_listing_pms, and update BookingRepository queries in src/models/booking.py and src/repositories/booking_repository.py
- [ ] T010 [P] Rename cloudbeds_room_id to pms_room_id (String(100)) on Room model, update unique constraint to uq_room_listing_pms, and update RoomRepository queries in src/models/room.py and src/repositories/room_repository.py
- [ ] T011 Create Alembic migration generalize_pms_columns: batch-mode column renames on listings/bookings/rooms, add 3 new columns to oauth_credentials, update index and constraint names, raise NotImplementedError on downgrade in alembic/versions/xxxx_generalize_pms_columns.py
- [ ] T012 Update CalendarService iCal UID generation from cloudbeds_booking_id to pms_booking_id in src/services/calendar_service.py
- [ ] T013 Update existing test fixtures and assertions for pms_* column renames across tests/conftest.py, tests/unit/, and tests/integration/
- [ ] T014 Write Alembic migration data-preservation tests: insert pre-migration data with cloudbeds_* columns, run upgrade, verify all values preserved with pms_* names and correct defaults in tests/integration/test_pms_migration.py

**Checkpoint**: Database schema generalized. All existing tests pass with new column names. Migration preserves 100% of existing data. Downgrade raises NotImplementedError.

---

## Phase 3: Configuration & Credential Updates

**Purpose**: Support multi-provider configuration through environment variables, admin API endpoints, and provider-aware credential management.

**Dependencies**: Phase 2 (models must have pms_type column).

- [ ] T015 [US1] Add pms_type, guesty_client_id, and guesty_client_secret fields with PMS_TYPE auto-detection validator (explicit PMS_TYPE wins → GUESTY_CLIENT_ID set implies guesty → default cloudbeds) to Settings in src/config.py
- [ ] T016 [US1] Create CredentialRepository with provider-aware CRUD operations (get_credential, save_credential, update_token, get_token_request_count) in src/repositories/credential_repository.py
- [ ] T017 [P] [US1] Write unit tests for new config settings fields and PMS_TYPE auto-detection logic (explicit, inferred-guesty, default-cloudbeds) in tests/unit/test_config.py
- [ ] T018 [P] [US1] Write unit tests for CredentialRepository CRUD operations with both cloudbeds and guesty pms_type records in tests/unit/test_credential_repository.py
- [ ] T019 [US1] Update POST /api/oauth/configure to accept optional pms_type parameter with provider-specific validation (Guesty: require client_id + client_secret, reject api_key/refresh_token; Cloudbeds: existing flow preserved) in src/api/oauth.py
- [ ] T020 [US1] Update GET /api/oauth/status to return pms_type, auth_type, and token_requests_remaining (Guesty only, null for Cloudbeds) fields in src/api/oauth.py
- [ ] T021 [US3] Add GET /api/providers endpoint returning registered provider metadata with credential field definitions for dynamic form rendering in src/api/oauth.py
- [ ] T022 [US1] Update OAuthService to route token refresh through provider registry based on credential pms_type instead of hardcoded CloudbedsService in src/services/oauth_service.py
- [ ] T023 [US1] Write integration tests for updated POST /api/oauth/configure (both providers), GET /api/oauth/status (both providers), and new GET /api/providers endpoints in tests/integration/test_oauth_api.py

**Checkpoint**: Multi-provider configuration working. Guesty credentials can be stored and validated via API. Existing Cloudbeds configuration flow unchanged. Provider metadata endpoint serves admin UI.

---

## Phase 4: Guesty Service Implementation

**Purpose**: Implement the complete Guesty provider with full Guesty Open API integration, token management, pagination, rate-limit handling, and data normalization.

**Dependencies**: Phase 1 (PMSProvider interface to implement), Phase 3 (credential storage and config).

- [ ] T024 [US1] Create Guesty provider package with __init__.py exporting GuestyProvider class in src/providers/guesty/__init__.py
- [ ] T025 [US4] Implement GuestyTokenManager with token caching, 24h validity check, request counting per rolling window, warn-at-4th, allow-5th-with-warning, defer-at-6th enforcement, and window reset logic in src/providers/guesty/auth.py
- [ ] T026 [US1] Implement GuestyProvider(PMSProvider) core with httpx async client, base URL config, auth header injection, and get_listings using paginated GET /v1/listings (limit=100, skip-based, stop when results < limit) in src/providers/guesty/service.py
- [ ] T027 [US4] Implement HTTP 429 rate-limit response handler with exponential backoff (1s → 2s → 4s, max 30s), Retry-After header parsing, and max 3 retries as reusable request wrapper in src/providers/guesty/service.py
- [ ] T028 [US1] Implement get_reservations with listingId filter, status mapping (confirmed/checked_in/checked_out/canceled → internal statuses; exclude inquiry/reserved), date range filters, and skip-based pagination in src/providers/guesty/service.py
- [ ] T029 [US1] Implement get_rooms: for multi-unit listings fetch GET /v1/listings/{id} and map childListings to PMSRoom; for single-unit create one implicit PMSRoom record in src/providers/guesty/service.py
- [ ] T030 [US5] Implement get_guest via GET /v1/guests/{id} returning PMSGuest with fullName, phone, email; return None on 404 for "Guest [guestId]" fallback in src/providers/guesty/service.py
- [ ] T031 [US5] Implement get_custom_fields via GET /v1/reservations-v3/{id}/custom-fields (v3 endpoint only — v2 is deprecated) returning fieldId → value dict in src/providers/guesty/service.py
- [ ] T032 [US1] Register GuestyProvider in provider registry and implement test_connection via get_listings() success check (verify it returns at least one listing) in src/providers/guesty/__init__.py and src/providers/registry.py
- [ ] T033 [P] [US4] Write unit tests for GuestyTokenManager: cache hit, cache miss, rate tracking, window reset, warn-at-4th, allow-5th-with-warning, and defer-at-6th scenarios in tests/unit/test_guesty_auth.py
- [ ] T034 [P] [US1] Write unit tests for GuestyProvider with mocked httpx responses: get_listings pagination, get_reservations filtering, get_rooms multi/single-unit, get_guest with 404, get_custom_fields v3, and 429 retry behavior in tests/unit/test_guesty_service.py

**Checkpoint**: Guesty provider fully implemented and unit-tested. All PMSProvider abstract methods operational against mocked Guesty API responses. Token management handles all rate-limit scenarios.

---

## Phase 5: Sync Service Refactoring

**Purpose**: Make SyncService and SyncScheduler PMS-agnostic by delegating all PMS data operations through the provider abstraction layer.

**Dependencies**: Phase 2 (model renames in effect), Phase 4 (GuestyProvider available for testing).

- [ ] T035 [US1] Refactor SyncService.sync_listing to accept a PMSProvider instance parameter instead of creating CloudbedsService directly in src/services/sync_service.py
- [ ] T036 [US2] Refactor SyncScheduler._sync_all_listings to look up provider from stored credential pms_type via provider registry in src/services/scheduler.py
- [ ] T037 [US2] Refactor SyncScheduler._refresh_token to delegate to active provider's refresh_token method based on credential pms_type in src/services/scheduler.py
- [ ] T038 [US1] Update _process_reservations and booking data extraction to work with PMSReservation DTOs using pms_booking_id and normalized fields in src/services/sync_service.py
- [ ] T039 [US5] Implement provider-agnostic guest name resolution in sync: Cloudbeds uses inline guest data, Guesty batches unique guestIds and calls provider.get_guest per unique ID. Also extract last 4 digits of guest phone from PMSGuest.phone for the guest_phone_last4 field in src/services/sync_service.py
- [ ] T040 [US1] Refactor POST /api/listings/sync-properties endpoint to use provider registry instead of direct CloudbedsService instantiation in src/api/listings.py
- [ ] T041 [US2] Update sync service and scheduler tests for provider-agnostic flow with both CloudbedsProvider and GuestyProvider test fixtures in tests/unit/test_sync_service.py

**Checkpoint**: Sync pipeline fully PMS-agnostic. Both Cloudbeds and Guesty data flows through the same SyncService → provider → DTO → database path. Existing Cloudbeds sync behavior unchanged.

---

## Phase 6: Admin UI Updates

**Purpose**: Add PMS provider selection, dynamic credential forms, and provider-specific status display to the admin interface.

**Dependencies**: Phase 3 (API endpoints for /api/providers and updated oauth endpoints), Phase 4 (Guesty provider for connection testing).

- [ ] T042 [US3] Add PMS type selector dropdown and conditional credential form containers (Cloudbeds fields vs Guesty fields) to admin template in src/templates/admin.html
- [ ] T043 [US3] Update admin.js to fetch GET /api/providers on load and dynamically render provider-specific credential input fields based on selected PMS type in src/static/js/admin.js
- [ ] T044 [US3] Display active pms_type label and Guesty-specific token_requests_remaining counter in the connection status section of src/static/js/admin.js
- [ ] T045 [US3] Update connection test button to include pms_type in POST /api/oauth/configure request and display provider-specific success/error messages in src/static/js/admin.js
- [ ] T046 [US3] Add CSS styling for PMS provider selector dropdown, conditional form visibility toggles, and provider status badge in src/static/css/admin.css
- [ ] T047 [US3]: Implement PMS type switch confirmation warning
  - **Files**: `src/static/js/admin.js`
  - **Acceptance**: When user changes PMS type on an installation with existing synced data, a confirmation dialog warns that existing data won't be deleted but new syncs will use the new provider
  - **Depends on**: T042
  - **Traces to**: EC-006

**Checkpoint**: Admin UI fully supports multi-PMS workflow. Users can select provider, enter provider-specific credentials, test connection, and see provider-aware status. Existing Cloudbeds UI flow unchanged.

---

## Phase 7: Testing & Integration (Polish)

**Purpose**: End-to-end validation, backward compatibility verification, edge case coverage, and cross-cutting quality checks.

**Dependencies**: All previous phases (1–6).

- [ ] T048 Write end-to-end integration test: Guesty credential configuration → property sync → reservation sync with guest resolution → iCal feed generation and content verification in tests/integration/test_guesty_sync.py
- [ ] T049 Write backward compatibility integration test: pre-existing Cloudbeds installation with data → upgrade (migration) → sync succeeds → iCal feeds return identical content with stable URLs in tests/integration/test_cloudbeds_compat.py
- [ ] T050 [P] Write edge case tests: guest 404 fallback name, listing with no rooms (implicit room), token limit exhaustion and deferral, paginated multi-page results, canceled reservation handling in tests/integration/test_guesty_edge_cases.py
- [ ] T051 [P] Write iCal RFC 5545 contract test verifying Guesty-sourced calendar events produce valid iCalendar output with correct VEVENT properties in tests/contract/test_ical_rfc5545.py
- [ ] T052 Update shared test fixtures with multi-provider factory helpers (Guesty + Cloudbeds credential factories, listing/booking/room factories with pms_* fields) in tests/conftest.py
- [ ] T053 Validate quickstart.md developer setup steps by running full environment setup, migration, and end-to-end workflow on clean state
- [ ] T054: Write benchmark/optional performance validation coverage
  - **Files**: `tests/integration/test_performance.py`
  - **Acceptance**: Default test suite asserts deterministic behavior
    (token request count ≤2 per simulated 24h cycle). Sync-cycle
    duration and iCal generation latency are measured and reported as
    benchmarks under a dedicated `@pytest.mark.benchmark` marker (or
    equivalent optional CI job with environment-controlled baselines),
    rather than strict default-suite wall-clock gates.
  - **Depends on**: T048
  - **Traces to**: SC-001, SC-005, Constitution Principle IV

**Checkpoint**: All tests pass. Feature complete with full backward compatibility verified. Edge cases covered. iCal output validated against RFC 5545.

---

## Dependencies & Execution Order

### Phase Dependencies

```text
Phase 1: PMS Provider Abstraction
    │
    ▼
Phase 2: Database Schema Generalization
    │
    ├──────────────────────┐
    ▼                      ▼
Phase 3: Config &      (Phase 2 data
Credential Updates      model available)
    │                      │
    ├──────────────────────┘
    ▼
Phase 4: Guesty Service Implementation
    │
    ▼
Phase 5: Sync Service Refactoring
    │
    ├──────────────────────┐
    ▼                      ▼
Phase 6: Admin UI      (Phase 5 sync
Updates                 must work)
    │                      │
    ├──────────────────────┘
    ▼
Phase 7: Testing & Integration
```

- **Phase 1**: No dependencies — start immediately
- **Phase 2**: Depends on Phase 1 (provider type strings for pms_type values)
- **Phase 3**: Depends on Phase 2 (models must have pms_type column)
- **Phase 4**: Depends on Phase 1 (PMSProvider ABC) + Phase 3 (credential storage)
- **Phase 5**: Depends on Phase 2 (model renames) + Phase 4 (GuestyProvider ready)
- **Phase 6**: Depends on Phase 3 (API endpoints) + Phase 4 (Guesty for connection test)
- **Phase 7**: Depends on all previous phases (1–6)

### User Story Dependencies

- **US1** (P1 — Guesty Setup & Sync): Phases 1 → 2 → 3 → 4 → 5. Core path — all other stories depend on this infrastructure.
- **US2** (P2 — Cloudbeds Backward Compat): Phase 2 (migration) + Phase 5 (provider-agnostic sync). Can be validated independently after Phase 5.
- **US3** (P3 — Admin PMS Selection): Phase 3 (providers endpoint) + Phase 6 (admin UI). Independent of US1 sync flow.
- **US4** (P4 — Guesty API Constraints): Phase 4 (token manager + rate limiter). Independent unit; tested in isolation.
- **US5** (P5 — Guest Details & Data Assembly): Phase 4 (get_guest, get_custom_fields) + Phase 5 (guest resolution in sync). Extends US1.

### Within Each Phase

- Tasks are listed in execution order (sequential by default)
- Tasks marked **[P]** within a phase can run in parallel
- Models/interfaces before implementations
- For implementation tasks with corresponding test tasks in the same phase, write tests first, verify they fail, then implement; otherwise follow the listed task order
- Core logic before integration points

### Key Task Dependencies (Cross-Phase)

| Task | Depends On | Reason |
|------|-----------|--------|
| T005 (CloudbedsProvider) | T001, T002 | Needs ABC + registry to implement/register |
| T011 (Alembic migration) | T007–T010 | Migration must match updated model definitions |
| T014 (Migration tests) | T011 | Tests need migration file to exercise |
| T019 (Configure endpoint) | T015, T016 | Needs config settings + credential repo |
| T025 (Token manager) | T007 | Needs token_request_count/window columns on model |
| T026 (GuestyProvider) | T001, T024, T025 | Needs ABC, package, token manager |
| T035 (SyncService refactor) | T005, T026 | Needs both providers available |
| T040 (Listings API refactor) | T002, T035 | Needs registry + refactored sync service |
| T042 (Admin UI) | T021 | Needs /api/providers endpoint |
| T048 (E2E test) | T032, T035–T040 | Needs complete Guesty + sync pipeline |

### Parallel Opportunities

**Within Phase 1** (after T001, T002):
- T003 and T004 can run in parallel (different test files, test already-defined interfaces)

**Within Phase 2** (immediately):
- T007, T008, T009, T010 can all run in parallel (different model/repo files)

**Within Phase 3** (after T015, T016):
- T017 and T018 can run in parallel (different test files)

**Within Phase 4** (after T026):
- T033 and T034 can run in parallel (different test files)

**Within Phase 7**:
- T050 and T051 can run in parallel (different test files)

**Cross-Phase** (with sufficient team capacity):
- Phase 4 can overlap with Phase 5 once Phase 4 core (T024–T032) is done
- Phase 6 can overlap with Phase 5 once Phase 3 is done

---

## Parallel Example: Phase 2 (Database Generalization)

```bash
# Launch all model/repo renames together (different files):
Task T007: "Add pms_type + token tracking to OAuthCredential in src/models/oauth_credential.py"
Task T008: "Rename cloudbeds_id → pms_id in src/models/listing.py + src/repositories/listing_repository.py"
Task T009: "Rename cloudbeds_booking_id → pms_booking_id in src/models/booking.py + src/repositories/booking_repository.py"
Task T010: "Rename cloudbeds_room_id → pms_room_id in src/models/room.py + src/repositories/room_repository.py"

# Then sequentially (depends on above):
Task T011: "Create Alembic migration (needs all model changes in place)"
Task T012: "Update CalendarService UID generation"
Task T013: "Update existing test fixtures"
Task T014: "Write migration tests"
```

## Parallel Example: Phase 4 (Guesty Implementation)

```bash
# Sequential core implementation (same file: src/providers/guesty/service.py):
Task T024 → T025 → T026 → T027 → T028 → T029 → T030 → T031 → T032

# Then launch tests in parallel (different test files):
Task T033: "GuestyTokenManager tests in tests/unit/test_guesty_auth.py"
Task T034: "GuestyProvider tests in tests/unit/test_guesty_service.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 — Guesty Setup & Sync)

1. Complete Phase 1: PMS Provider Abstraction Layer
2. Complete Phase 2: Database Schema Generalization
3. Complete Phase 3: Configuration & Credential Updates
4. Complete Phase 4: Guesty Service Implementation
5. Complete Phase 5: Sync Service Refactoring
6. **STOP and VALIDATE**: Test Guesty end-to-end — credential entry → sync → iCal feed
7. Existing Cloudbeds installations should also continue working (US2 covered by Phase 2 + 5)

### Incremental Delivery

1. Phases 1–2 → Foundation ready (schema generalized, provider abstraction in place)
2. Phase 3 → Configuration accepts multi-provider credentials
3. Phase 4 → Guesty provider can talk to Guesty API
4. Phase 5 → Sync pipeline works with both providers → **MVP complete** (US1 + US2)
5. Phase 6 → Admin UI updated → **US3 complete**
6. Phase 7 → Full test coverage → **Feature complete**

### Risk Mitigation

- **Highest risk**: Phase 2 (migration) — test thoroughly before proceeding
- **External dependency**: Phase 4 (Guesty API) — use mocked responses for unit tests
- **Backward compat**: Validate after Phase 2 AND after Phase 5 that Cloudbeds still works

---

## Summary

| Metric | Value |
|--------|-------|
| **Total tasks** | 54 |
| **Phase 1 (Abstraction)** | 6 tasks |
| **Phase 2 (Database)** | 8 tasks |
| **Phase 3 (Config)** | 9 tasks |
| **Phase 4 (Guesty)** | 11 tasks |
| **Phase 5 (Sync)** | 7 tasks |
| **Phase 6 (Admin UI)** | 6 tasks |
| **Phase 7 (Testing)** | 7 tasks |
| **Parallel opportunities** | 6 parallel groups across phases |
| **User stories covered** | US1 (17 tasks), US2 (4), US3 (7), US4 (3), US5 (3), Foundational (14), Polish (7) |
| **MVP scope** | Phases 1–5 (41 tasks) — Guesty sync working + Cloudbeds backward compat |
| **New files created** | ~15 (providers package, Guesty modules, tests, migration) |
| **Existing files modified** | ~20 (models, repos, services, API, admin UI, test fixtures) |

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks in same phase
- [US?] label maps task to specific user story for traceability
- Each phase has a checkpoint for independent validation
- Atomic commits required: one logical change per commit (see AGENTS.md and constitution)
- Pre-commit hooks (reuse, ruff, mypy, interrogate, yamllint, gitlint) MUST pass on every commit
- DCO sign-off (`git commit -s`) and Co-Authored-By trailer required for AI-assisted commits
- Never bypass pre-commit with `--no-verify`
- All new source files must include SPDX license headers
- All new functions/classes must have docstrings (interrogate enforces 100% coverage)
