<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Cloudbeds to Airbnb iCal Export Bridge

**Feature**: 001-cloudbeds-ical-export
**Generated**: 2025-01-24
**Input**: Design documents from `/specs/001-cloudbeds-ical-export/`

## Format: `- [ ] [ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4)
- All task descriptions include exact file paths

## Path Conventions

Project uses single-project structure:
- Source: `src/`
- Tests: `tests/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [ ] T001 Create project structure with src/, tests/, and deployment directories
- [ ] T002 Initialize Python project with uv and pyproject.toml including FastAPI, SQLAlchemy[asyncio], icalendar, cloudbeds-pms, APScheduler, Jinja2, cryptography dependencies
- [ ] T003 [P] Configure ruff for linting and formatting in pyproject.toml
- [ ] T004 [P] Configure mypy for type checking in pyproject.toml
- [ ] T005 [P] Setup pre-commit hooks for SPDX headers, ruff, and mypy
- [ ] T006 [P] Create Dockerfile for standalone/Home Assistant addon deployment
- [ ] T007 [P] Create Home Assistant addon config.json with ingress configuration
- [ ] T008 [P] Create .env.example with all required environment variables

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [ ] T009 Create database configuration in src/database.py with SQLAlchemy async engine setup
- [ ] T010 Setup Alembic for database migrations with initial migration script
- [ ] T011 [P] Create OAuthCredential model in src/models/oauth_credential.py with AES-256 encryption for access and refresh tokens
- [ ] T012 [P] Create Listing model in src/models/listing.py with all fields from data-model.md
- [ ] T013 [P] Create CustomField model in src/models/custom_field.py with listing relationship
- [ ] T014 [P] Create Booking model in src/models/booking.py with listing relationship
- [ ] T015 Create initial Alembic migration 001_initial_schema.py for all models
- [ ] T016 [P] Implement CloudbedsService wrapper in src/services/cloudbeds_service.py using cloudbeds-pms SDK
- [ ] T017 [P] Implement ConfigService in src/services/config_service.py for environment configuration management
- [ ] T018 [P] Create Pydantic settings configuration in src/config.py for environment variables
- [ ] T019 [P] Create authentication middleware in src/middleware/auth.py for Home Assistant Ingress header checking
- [ ] T020 [P] Setup FastAPI application structure in src/main.py with routers and middleware
- [ ] T021 [P] Create health check endpoint GET /health in src/api/health.py
- [ ] T022 [P] Implement error handling middleware in src/middleware/error_handler.py
- [ ] T023 [P] Setup logging configuration in src/utils/logging.py

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Basic iCal Export for Single Listing (Priority: P1) 🎯 MVP

**Goal**: Export a single Cloudbeds listing as an Airbnb-compatible iCal feed with proper dates, guest names/booking IDs, and phone number last 4 digits

**Independent Test**: Configure one listing via API, retrieve iCal URL, verify calendar import with proper event data (dates, titles, descriptions)

### Implementation for User Story 1

- [ ] T024 [P] [US1] Create repository pattern for Listing in src/repositories/listing_repository.py
- [ ] T025 [P] [US1] Create repository pattern for Booking in src/repositories/booking_repository.py
- [ ] T026 [P] [US1] Create repository pattern for CustomField in src/repositories/custom_field_repository.py
- [ ] T027 [US1] Implement CalendarService in src/services/calendar_service.py with iCal generation using icalendar library
- [ ] T028 [US1] Implement iCal event creation logic in src/services/calendar_service.py for bookings with guest name fallback to booking ID
- [ ] T029 [US1] Implement phone number last 4 extraction and event description formatting in src/services/calendar_service.py
- [ ] T030 [US1] Add timezone handling for DTSTART/DTEND in src/services/calendar_service.py
- [ ] T031 [P] [US1] Create GET /ical/{slug}.ics endpoint in src/api/ical.py for public iCal feed access
- [ ] T032 [P] [US1] Implement in-memory cache with 5-minute TTL for iCal strings in src/services/calendar_service.py
- [ ] T033 [US1] Add CORS configuration for iCal endpoints in src/main.py
- [ ] T034 [US1] Implement SyncService in src/services/sync_service.py for fetching bookings from Cloudbeds API
- [ ] T035 [US1] Add booking data caching logic in src/services/sync_service.py with INSERT/UPDATE/cancelled handling
- [ ] T036 [US1] Setup APScheduler background task for 5-minute sync interval in src/services/sync_service.py
- [ ] T037 [US1] Implement OAuth token refresh logic in src/services/cloudbeds_service.py with automatic trigger before expiry

**Checkpoint**: At this point, User Story 1 should be fully functional - single listing can be configured and iCal feed generated

---

## Phase 4: User Story 2 - Administrative Configuration Interface (Priority: P2)

**Goal**: Provide web-based admin portal to configure listings, customize iCal output fields, with Home Assistant authentication

**Independent Test**: Login via Home Assistant auth, add/remove listings, toggle custom fields, verify configuration persisted and reflected in iCal output

### Implementation for User Story 2

- [ ] T038 [P] [US2] Create GET /api/oauth/status endpoint in src/api/oauth.py for OAuth credential status
- [ ] T039 [P] [US2] Create POST /api/oauth/configure endpoint in src/api/oauth.py for setting credentials
- [ ] T040 [P] [US2] Create POST /api/oauth/refresh endpoint in src/api/oauth.py for manual token refresh
- [ ] T041 [P] [US2] Create GET /api/status endpoint in src/api/status.py for system status
- [ ] T042 [P] [US2] Create GET /api/listings endpoint in src/api/listings.py for listing all properties
- [ ] T043 [P] [US2] Create GET /api/listings/{id} endpoint in src/api/listings.py for listing details
- [ ] T044 [P] [US2] Create POST /api/listings/{id}/enable endpoint in src/api/listings.py for enabling export
- [ ] T045 [P] [US2] Create PUT /api/listings/{id} endpoint in src/api/listings.py for updating configuration
- [ ] T046 [P] [US2] Create GET /api/listings/{id}/custom-fields endpoint in src/api/custom_fields.py for field configuration
- [ ] T047 [P] [US2] Create PUT /api/listings/{id}/custom-fields endpoint in src/api/custom_fields.py for updating fields
- [ ] T048 [US2] Implement authentication check for all admin endpoints using auth middleware in src/api/* routers
- [ ] T049 [P] [US2] Create HTML admin UI template in src/templates/admin.html with Jinja2
- [ ] T050 [P] [US2] Create CSS stylesheet in src/static/css/admin.css for admin UI styling
- [ ] T051 [P] [US2] Create JavaScript for admin UI in src/static/js/admin.js for API interactions
- [ ] T052 [US2] Add OAuth configuration form and flow in admin UI
- [ ] T053 [US2] Add listing management interface in admin UI with enable/disable toggles
- [ ] T054 [US2] Add custom field selection interface in admin UI with add/remove functionality
- [ ] T055 [US2] Add sync settings interface in admin UI with polling interval dropdown (1-60 minutes, default 5)
- [ ] T056 [US2] Implement ical_url_slug generation (slugify) in src/repositories/listing_repository.py

**Checkpoint**: Admin portal fully functional - users can configure listings and customize fields via web UI

---

## Phase 5: User Story 3 - Multi-Listing Support (Priority: P3)

**Goal**: Support multiple listings with unique iCal URLs and independent configurations

**Independent Test**: Enable 3+ listings, verify each has unique URL, confirm bookings don't cross-contaminate, test different custom field configs per listing

### Implementation for User Story 3

- [ ] T057 [P] [US3] Add listing count validation (max 50) in src/api/listings.py POST /enable endpoint
- [ ] T058 [US3] Implement unique iCal URL generation with conflict detection in src/repositories/listing_repository.py
- [ ] T059 [US3] Add listing-specific custom field retrieval in src/services/calendar_service.py
- [ ] T060 [US3] Implement per-listing iCal generation with independent field configurations in src/services/calendar_service.py
- [ ] T061 [US3] Add listing isolation validation in src/services/sync_service.py to prevent booking cross-contamination
- [ ] T062 [US3] Update admin UI to display all listings with individual configuration controls
- [ ] T063 [US3] Add bulk listing operations (enable/disable multiple) to admin UI

**Checkpoint**: Multi-listing support complete - each listing independently configurable and isolated

---

## Phase 6: User Story 4 - Real-Time Calendar Sync (Priority: P4)

**Goal**: Near real-time iCal feed updates reflecting Cloudbeds booking changes within reasonable timeframe

**Independent Test**: Create/modify/cancel booking in Cloudbeds, access iCal URL within 5 minutes, verify change reflected

### Implementation for User Story 4

- [ ] T064 [P] [US4] Create POST /api/listings/{id}/sync endpoint in src/api/listings.py for manual sync trigger
- [ ] T065 [US4] Implement sync status tracking with last_sync_at updates in src/services/sync_service.py
- [ ] T066 [US4] Add sync error logging and last_sync_error field updates in src/services/sync_service.py
- [ ] T067 [US4] Implement booking change detection (new, modified, cancelled) in src/services/sync_service.py
- [ ] T068 [US4] Add cache invalidation on sync completion in src/services/sync_service.py
- [ ] T069 [US4] Implement rate limit handling with exponential backoff in src/services/cloudbeds_service.py
- [ ] T070 [US4] Add sync status display in admin UI showing last sync time and next sync time
- [ ] T071 [US4] Add manual "Sync Now" button per listing in admin UI
- [ ] T072 [P] [US4] Create GET /api/listings/{id}/bookings endpoint in src/api/bookings.py for debugging cached bookings

**Checkpoint**: Real-time sync complete - bookings reflect in iCal within 5-10 minutes

---

## Phase 7: Testing

**Purpose**: Ensure code quality and functionality through automated tests

- [ ] T073 [P] Create tests/unit/test_calendar_service.py with unit tests for iCal generation
- [ ] T074 [P] Create tests/unit/test_sync_service.py with unit tests for sync orchestration
- [ ] T075 [P] Create tests/unit/test_cloudbeds_service.py with mocked SDK tests
- [ ] T076 [P] Create tests/integration/test_api_oauth.py with OAuth endpoint integration tests
- [ ] T077 [P] Create tests/integration/test_api_listings.py with listing endpoint integration tests
- [ ] T078 [P] Create tests/integration/test_api_ical.py with iCal feed integration tests
- [ ] T079 [P] Create tests/contract/test_ical_rfc5545.py with RFC 5545 compliance validation
- [ ] T080 [P] Create tests/conftest.py with pytest fixtures for async database and test client

**Checkpoint**: All unit and integration tests passing

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories and production readiness

- [ ] T081 [P] Implement data purge job for old bookings (90 days) in src/services/sync_service.py
- [ ] T082 [P] Add cancelled booking purge (30 days) in src/services/sync_service.py
- [ ] T083 [P] Schedule daily purge task at 02:00 UTC using APScheduler
- [ ] T084 [P] Add comprehensive logging for all services with structured log format
- [ ] T085 [P] Implement RFC 5545 validation for generated iCal in src/services/calendar_service.py
- [ ] T086 [P] Add edge case handling for missing guest name (fallback to booking ID) in src/services/calendar_service.py
- [ ] T087 [P] Add edge case handling for missing phone number (omit field) in src/services/calendar_service.py
- [ ] T088 [P] Add edge case handling for invalid booking dates (skip and log) in src/services/sync_service.py
- [ ] T089 [P] Add Cloudbeds API unavailability handling with cached response fallback in src/services/sync_service.py
- [ ] T090 [P] Add guest name truncation for iCal summary field (255 char limit) in src/services/calendar_service.py
- [ ] T091 [P] Add Home Assistant authentication service down handling in src/middleware/auth.py
- [ ] T092 [P] Add timezone validation for IANA timezone identifiers in src/repositories/listing_repository.py with UTC fallback and warning log for invalid timezones
- [ ] T093 [P] Create README.md with project overview and installation instructions
- [ ] T094 [P] Create docs/homeassistant-addon-setup.md for addon installation guide
- [ ] T095 [P] Create docs/api-usage.md with API examples
- [ ] T096 [P] Add SPDX headers to all Python source files
- [ ] T097 [P] Add SQLite WAL mode configuration in src/database.py for concurrent reads
- [ ] T098 [P] Add database backup instructions in README.md
- [ ] T099 Run quickstart.md validation with standalone Podman deployment
- [ ] T100 Verify iCal import compatibility by importing generated feeds into Airbnb calendar sync, Google Calendar, AND Apple Calendar - document test results for each platform
- [ ] T101 Performance testing with 50 listings × 365 bookings load scenario
- [ ] T102 Security audit for PII leakage per SC-010 classification: verify no full phone numbers, email addresses, physical addresses, payment info, government IDs, or private booking notes appear in iCal feeds
- [ ] T103 [P] Implement last-write-wins with timestamp display in admin UI for concurrent configuration updates
- [ ] T104 [P] Document HTTPS configuration requirements in docs/deployment.md (production HTTPS required, HTTP only behind TLS proxy)
- [ ] T105 Final code review and cleanup

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup (Phase 1) completion - BLOCKS all user stories
- **User Story 1 (Phase 3)**: Depends on Foundational (Phase 2) completion - Core MVP
- **User Story 2 (Phase 4)**: Depends on Foundational (Phase 2) completion - Independent of US1 but enhances it
- **User Story 3 (Phase 5)**: Depends on US1 (Phase 3) and US2 (Phase 4) - Extends single listing to multiple
- **User Story 4 (Phase 6)**: Depends on US1 (Phase 3) - Enhances sync behavior
- **Polish (Phase 7)**: Depends on all user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Depends only on Foundational phase - **THIS IS THE MVP**
- **User Story 2 (P2)**: Depends only on Foundational phase - Independent but provides configuration UI
- **User Story 3 (P3)**: Depends on US1 and US2 - Scales to multiple listings
- **User Story 4 (P4)**: Depends on US1 - Improves sync frequency and manual control

### Within Each User Story

- Repository patterns before services that use them
- Services before API endpoints
- API endpoints before UI components
- Core functionality before edge case handling
- Validation before completion

### Parallel Opportunities Within Phases

**Phase 1 - Setup** (All can run in parallel):
- T003 (ruff config) + T004 (mypy config) + T005 (pre-commit)
- T006 (Dockerfile) + T007 (addon config) + T008 (.env.example)

**Phase 2 - Foundational** (Groups can run in parallel):
- Models (T011, T012, T013, T014) all parallel
- Services (T016, T017, T018) all parallel after models
- Middleware (T019, T022) parallel
- API setup (T020, T021, T023) parallel after middleware

**Phase 3 - User Story 1** (Parallel opportunities):
- T024, T025, T026 (repositories) all parallel
- T031 (iCal endpoint) and T032 (cache) parallel with T027-T030 (calendar service)

**Phase 4 - User Story 2** (Most endpoints parallel):
- T038, T039, T040, T041, T042, T043, T044, T045, T046, T047 all parallel (different files)
- T049, T050, T051 (UI assets) all parallel

**Phase 7 - Polish** (Most tasks parallel):
- T072-T083 all parallel (different concerns)
- T084-T089 documentation all parallel

### Parallel Example: Foundational Phase

```bash
# Launch all models together:
Task: "Create OAuthCredential model in src/models/oauth_credential.py"
Task: "Create Listing model in src/models/listing.py"
Task: "Create CustomField model in src/models/custom_field.py"
Task: "Create Booking model in src/models/booking.py"

# Then launch all services together:
Task: "Implement CloudbedsService wrapper in src/services/cloudbeds_service.py"
Task: "Implement ConfigService in src/services/config_service.py"
Task: "Create Pydantic settings configuration in src/config.py"
```

---

## Implementation Strategy

### MVP First (Recommended)

**Goal**: Deliver working iCal export for single listing as fast as possible

1. ✅ Complete Phase 1: Setup (T001-T008)
2. ✅ Complete Phase 2: Foundational (T009-T023) - **CRITICAL GATE**
3. ✅ Complete Phase 3: User Story 1 (T024-T037) - **MVP COMPLETE**
4. **STOP and VALIDATE**:
   - Deploy standalone container
   - Configure one listing via API
   - Generate iCal feed
   - Test import in Google Calendar
   - Verify booking events display correctly

**At this point you have a working product** that delivers core value.

### Incremental Delivery (After MVP)

1. Add Phase 4: User Story 2 (T038-T055) → Admin UI for configuration
   - **Value**: No more API calls, user-friendly configuration

2. Add Phase 5: User Story 3 (T056-T062) → Multiple listings support
   - **Value**: Scales to property managers with multiple properties

3. Add Phase 6: User Story 4 (T063-T071) → Real-time sync improvements
   - **Value**: Better calendar accuracy and manual sync control

4. Add Phase 7: Polish (T072-T094) → Production hardening
   - **Value**: Edge cases, security, performance, documentation

### Parallel Team Strategy

If you have 2-3 developers:

**After Foundational Phase completes:**
- Developer A: User Story 1 (T024-T037) - Core iCal generation
- Developer B: User Story 2 (T038-T055) - Admin UI (in parallel)
- Developer C: Can start on Phase 7 polish tasks that don't depend on US1/US2

**Benefits**: Faster delivery, independent work streams, earlier integration testing

---

## Testing Strategy

**Approach**: Tests are written alongside or shortly after implementation for each user story phase. Phase 7 establishes the test infrastructure and fixtures, while individual test files are created as features are implemented. This approach satisfies constitutional testing requirements while maintaining implementation velocity.

**Test Implementation by Phase**:
- **Phase 3 (US1)**: Unit tests for CalendarService, SyncService created after T037
- **Phase 4 (US2)**: Integration tests for API endpoints created after T056
- **Phase 5 (US3)**: Multi-listing isolation tests added
- **Phase 6 (US4)**: Sync behavior tests added
- **Phase 7**: Test infrastructure (conftest.py, fixtures), contract tests, and comprehensive test coverage validation

**Test Types**:

- Contract tests for API endpoints: `tests/contract/test_*.py`
- Integration tests for user journeys: `tests/integration/test_*.py`
- Unit tests for services/utilities: `tests/unit/test_*.py`

**Manual Testing Checkpoints**:
- After US1: iCal generation and calendar import
- After US2: Admin UI configuration workflow
- After US3: Multi-listing isolation and unique URLs
- After US4: Booking change detection and sync
- After Polish: Edge cases and production scenarios from quickstart.md

---

## Success Metrics (from spec.md)

Track these outcomes during implementation:

- ✅ SC-001: Configure new listing and obtain iCal URL within 5 minutes
- ✅ SC-002: iCal imports successfully into Airbnb, Google Calendar, Apple Calendar
- ✅ SC-003: Zero date discrepancies between Cloudbeds and iCal events
- ✅ SC-004: Support 50 listings with 365 bookings each without degradation
- ✅ SC-005: iCal updates reflect changes within 15 minutes
- ✅ SC-006: 100% successful Home Assistant authentication
- ✅ SC-007: Zero custom field configuration errors
- ✅ SC-008: 99.5%+ uptime for iCal feed delivery
- ✅ SC-009: 80% reduction in manual calendar synchronization time
- ✅ SC-010: Zero sensitive data leakage (full phone, addresses, payment info)

---

## Notes

- All tasks follow strict checklist format: `- [ ] [ID] [P?] [Story?] Description with path`
- [P] marker indicates parallelizable tasks (different files, no blocking dependencies)
- [Story] labels (US1-US4) map tasks to user stories from spec.md
- Each user story is independently implementable and testable
- MVP = Phases 1-3 only (T001-T037)
- Full feature = All phases (T001-T094)
- Atomic commits required per constitution
- Pre-commit hooks must pass (never use --no-verify)
- Each task = one implementation commit + separate docs commit for checklist update
- Stop at any checkpoint to validate independently
- SPDX headers required on all source files
- Environment variables in .env for local, injected by addon for HA deployment
- SQLite database persists to `/data/rentalsync.db` (HA standard)
- Standalone mode (`STANDALONE_MODE=true`) disables auth for testing only

---

## Task Summary

- **Total Tasks**: 105
- **Setup Phase**: 8 tasks
- **Foundational Phase**: 15 tasks (BLOCKS all user stories)
- **User Story 1 (MVP)**: 14 tasks
- **User Story 2**: 18 tasks
- **User Story 3**: 7 tasks
- **User Story 4**: 9 tasks
- **Polish Phase**: 23 tasks

**Parallelizable Tasks**: 62 tasks marked [P]
**Sequential Tasks**: 32 tasks with dependencies

**Suggested MVP Scope**: Phases 1-3 (T001-T037) = 37 tasks
**Estimated MVP Effort**: 2-3 weeks for single developer
**Full Feature Effort**: 4-6 weeks for single developer
