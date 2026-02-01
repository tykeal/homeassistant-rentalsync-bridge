<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Room-Level Calendar Export and Custom Fields UI

**Input**: Design documents from `/specs/002-room-level-calendars/`
**Prerequisites**: plan.md (required), spec.md (required)

**Tests**: Tests are REQUIRED for all new models, repositories, and API endpoints.

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions

---

## Phase 1: Data Model & Migration

**Purpose**: Create Room model and database schema changes

### Tests for Phase 1

- [x] T001 [P] Create unit tests for Room model in tests/unit/test_room_model.py
- [x] T002 [P] Create unit tests for Room-Listing relationship in tests/unit/test_room_model.py

### Implementation for Phase 1

- [x] T003 Create Room model in src/models/room.py with fields: id, listing_id, cloudbeds_room_id, room_name, room_type_name, ical_url_slug, enabled, created_at, updated_at
- [x] T004 Add rooms relationship to Listing model in src/models/listing.py
- [x] T005 Add room_id nullable FK to Booking model in src/models/booking.py
- [x] T006 Update src/models/__init__.py to export Room model
- [x] T007 Create Alembic migration for rooms table and booking.room_id column in alembic/versions/

**Checkpoint**: Room model exists, migration runs successfully, tests pass

---

## Phase 2: Room Repository & API

**Purpose**: CRUD operations and API endpoints for rooms

### Tests for Phase 2

- [x] T008 [P] Create unit tests for RoomRepository in tests/unit/test_room_repository.py
- [x] T009 [P] Create integration tests for room API endpoints in tests/integration/test_room_api.py

### Implementation for Phase 2

- [x] T010 Create RoomRepository in src/repositories/room_repository.py with CRUD operations: create, get_by_id, get_by_listing_id, get_by_slug, update, delete
- [x] T011 Add slug generation utility function (room name to slug) in src/repositories/room_repository.py
- [x] T012 Create room API router in src/api/rooms.py with endpoints: GET /api/rooms/{id}, PATCH /api/rooms/{id}
- [x] T013 Add GET /api/listings/{id}/rooms endpoint to src/api/listings.py
- [x] T014 Update GET /api/listings/{id} to include rooms in response in src/api/listings.py
- [x] T015 Register room router in src/main.py

**Checkpoint**: Room CRUD works, API endpoints return room data, tests pass

---

## Phase 3: Cloudbeds Room Sync

**Purpose**: Fetch rooms from Cloudbeds API and sync to database

### Tests for Phase 3

- [ ] T016 [P] Create unit tests for get_rooms() in tests/unit/test_cloudbeds_service.py
- [ ] T017 [P] Create integration tests for room sync in tests/integration/test_room_sync.py

### Implementation for Phase 3

- [ ] T018 Add get_rooms(property_id) method to CloudbedsService in src/services/cloudbeds_service.py
- [ ] T019 Add room sync logic to SyncService in src/services/sync_service.py - fetch rooms for each property
- [ ] T020 Update booking sync to associate bookings with rooms based on roomID in reservation data in src/services/sync_service.py
- [ ] T021 Update POST /api/sync/properties endpoint to sync rooms in src/api/sync.py
- [ ] T022 Rename sync button text from "Sync Properties" to "Sync Rooms" in admin UI src/static/js/admin.js

**Checkpoint**: Syncing from Cloudbeds creates Room records, bookings linked to rooms

---

## Phase 4: Room-Level iCal Generation

**Purpose**: Generate iCal feeds per room instead of per property

### Tests for Phase 4

- [ ] T023 [P] Create unit tests for room-level iCal generation in tests/unit/test_ical_generator.py
- [ ] T024 [P] Create integration tests for room iCal endpoint in tests/integration/test_room_ical.py

### Implementation for Phase 4

- [ ] T025 Update iCal generator to accept room_id and filter bookings by room in src/services/ical_generator.py
- [ ] T026 Add GET /ical/{listing_slug}/{room_slug}.ics endpoint in src/api/ical.py
- [ ] T027 Remove property-level iCal endpoint GET /ical/{slug}.ics from src/api/ical.py
- [ ] T028 Update booking repository get_confirmed_for_listing to optionally filter by room_id in src/repositories/booking_repository.py
- [ ] T029 Update iCal cache to use room-level keys in src/services/calendar_cache.py

**Checkpoint**: Room-level iCal URLs work, property-level removed, tests pass

---

## Phase 5: Admin UI - Room Management

**Purpose**: Display and manage rooms in admin interface

### Implementation for Phase 5

- [ ] T030 Add expandable room list to listing cards in src/static/js/admin.js
- [ ] T031 Add room iCal URL display with copy button in src/static/js/admin.js
- [ ] T032 Add room enable/disable toggle in src/static/js/admin.js
- [ ] T033 Add room slug edit functionality in src/static/js/admin.js
- [ ] T034 [P] Add CSS styles for room list display in src/static/css/admin.css
- [ ] T035 Update listing card template in src/templates/admin.html if needed

**Checkpoint**: Admin UI shows rooms per listing with working controls

---

## Phase 6: Custom Fields Enhancement

**Purpose**: Add guest_phone_last4 field and dropdown UI for field selection

### Tests for Phase 6

- [ ] T036 [P] Add unit tests for guest_phone_last4 field in tests/unit/test_custom_field_repository.py
- [ ] T037 [P] Add unit tests for available-custom-fields endpoint in tests/unit/test_custom_fields_api.py
- [ ] T038 [P] Add unit tests for guest_phone_last4 in iCal output in tests/unit/test_ical_generator.py

### Implementation for Phase 6

- [ ] T039 Add guest_phone_last4 to AVAILABLE_FIELDS in src/repositories/custom_field_repository.py
- [ ] T040 Add guest_phone_last4 as default field in create_defaults_for_listing() in src/repositories/custom_field_repository.py
- [ ] T041 Update iCal generator to format guest_phone_last4 as "Phone Number (Last 4 Digits): XXXX" in src/services/ical_generator.py
- [ ] T042 Create GET /api/listings/{id}/available-custom-fields endpoint in src/api/custom_fields.py
- [ ] T043 Update admin UI custom fields modal to use dropdown for field selection in src/static/js/admin.js
- [ ] T044 Filter dropdown to show only unconfigured fields in src/static/js/admin.js
- [ ] T045 Auto-populate display label from AVAILABLE_FIELDS when field selected in src/static/js/admin.js

**Checkpoint**: New field works in iCal, dropdown shows available fields only

---

## Phase 7: Polish & Testing

**Purpose**: Final validation, documentation, and cleanup

### Manual Testing

- [ ] T046 **MANUAL** Test multi-room property sync from Cloudbeds
- [ ] T047 **MANUAL** Test room iCal import to Airbnb calendar
- [ ] T048 **MANUAL** Test custom fields dropdown workflow
- [ ] T049 **MANUAL** Test guest_phone_last4 appears in iCal output
- [ ] T050 **MANUAL** Verify room enable/disable affects iCal generation

### Documentation & Cleanup

- [ ] T051 [P] Update README.md with room-level calendar information
- [ ] T052 [P] Update docs/api-usage.md with new room endpoints
- [ ] T053 [P] Update quickstart.md with room sync workflow
- [ ] T054 Run full test suite and verify all tests pass
- [ ] T055 Final code review and cleanup

**Checkpoint**: All manual tests pass, documentation complete, ready for PR

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1**: No dependencies - start immediately
- **Phase 2**: Depends on Phase 1 (Room model must exist)
- **Phase 3**: Depends on Phase 2 (Repository and API must exist)
- **Phase 4**: Depends on Phase 3 (Rooms must be syncable)
- **Phase 5**: Depends on Phase 2 (Room API must exist)
- **Phase 6**: No dependencies on Phases 1-5 (can run in parallel after Phase 1)
- **Phase 7**: Depends on all previous phases

### Parallel Opportunities

Within each phase, tasks marked [P] can run in parallel:
- Phase 1: T001, T002 (tests) can run in parallel
- Phase 2: T008, T009 (tests) can run in parallel
- Phase 3: T016, T017 (tests) can run in parallel
- Phase 4: T023, T024 (tests) can run in parallel
- Phase 5: T034 (CSS) can run in parallel with JS tasks
- Phase 6: T036, T037, T038 (tests) can run in parallel
- Phase 7: T051, T052, T053 (docs) can run in parallel

### Cross-Phase Parallelism

- Phase 5 (Admin UI) and Phase 6 (Custom Fields) can run in parallel after Phase 2
- Both depend on Phase 2 completion but not on each other

---

## Notes

- [P] tasks = different files, no dependencies
- Atomic commits required: one logical change per commit
- Pre-commit hooks MUST pass; never bypass with --no-verify
- Checklist items: implementation commit + separate Docs commit for checklist update
- Tests should be written first and verified to fail before implementation
- Room slugs: auto-generate from room name, allow user override
- Rooms enabled by default when synced
- Property-level iCal is removed (not deprecated) - pre-production
