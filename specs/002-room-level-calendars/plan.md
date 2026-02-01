<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Room-Level Calendar Export and Custom Fields UI

**Branch**: `002-room-level-calendars` | **Date**: 2026-02-01 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/002-room-level-calendars/spec.md`

## Summary

This feature transforms the iCal export from property-level to room-level calendars, enabling multi-unit properties to sync individual room availability to Airbnb and other OTAs. Additionally, the admin UI custom fields interface is enhanced with a dropdown of available fields and a new default field for guest phone last 4 digits.

**Primary Changes**:
1. New `Room` model with relationship to `Listing` and `Booking`
2. Room-level iCal URLs: `/ical/{listing_slug}/{room_slug}.ics`
3. Cloudbeds `getRooms` API integration
4. Custom fields dropdown UI with available fields endpoint
5. New `guest_phone_last4` default custom field

## Technical Context

**Language/Version**: Python 3.13
**Primary Dependencies**: FastAPI, SQLAlchemy (async), httpx, icalendar
**Storage**: SQLite with Alembic migrations
**Testing**: pytest with pytest-asyncio
**Target Platform**: Linux container (Podman/Docker), Home Assistant add-on
**Project Type**: Single project (FastAPI backend with embedded admin UI)
**Performance Goals**: iCal generation <100ms, room sync <5s per property
**Constraints**: SQLite single-writer, minimal memory footprint
**Scale/Scope**: Properties with 1-100 rooms, thousands of bookings

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Verify feature design compliance with `.specify/memory/constitution.md`:

- [x] **Code Quality**: SPDX headers planned for all new source files
- [x] **Testing Standards**: Test strategy defined (unit tests for models/repos, integration for sync)
- [x] **UX Consistency**: Admin UI follows existing patterns (cards, modals, toggles)
- [x] **Performance**: iCal generation <100ms, room sync <5s defined
- [x] **Commit Discipline**: Team aware of atomic commit and pre-commit requirements

## Project Structure

### Documentation (this feature)

```text
specs/002-room-level-calendars/
├── spec.md              # Feature specification
├── plan.md              # This file
└── tasks.md             # Task breakdown (created by /speckit.tasks)
```

### Source Code (repository root)

```text
src/
├── models/
│   ├── room.py          # NEW: Room model
│   ├── booking.py       # MODIFIED: Add room_id FK
│   └── listing.py       # MODIFIED: Add rooms relationship
├── repositories/
│   ├── room_repository.py       # NEW: Room CRUD
│   ├── booking_repository.py    # MODIFIED: Filter by room
│   └── custom_field_repository.py  # MODIFIED: Add guest_phone_last4
├── services/
│   ├── cloudbeds_service.py     # MODIFIED: Add getRooms API
│   ├── sync_service.py          # MODIFIED: Room sync logic
│   └── ical_generator.py        # MODIFIED: Room-level generation
├── api/
│   ├── rooms.py         # NEW: Room API endpoints
│   ├── ical.py          # MODIFIED: Room-level URLs
│   ├── custom_fields.py # MODIFIED: Available fields endpoint
│   └── listings.py      # MODIFIED: Include rooms
└── static/js/
    └── admin.js         # MODIFIED: Room UI, custom fields dropdown

alembic/versions/
└── xxx_add_rooms_table.py  # NEW: Migration

tests/
├── unit/
│   ├── test_room_model.py       # NEW
│   ├── test_room_repository.py  # NEW
│   └── test_custom_field_repository.py  # MODIFIED
└── integration/
    ├── test_room_sync.py        # NEW
    └── test_room_ical.py        # NEW
```

**Structure Decision**: Single project structure maintained. New `Room` model follows existing patterns with `models/`, `repositories/`, `api/` separation.

## Implementation Phases

### Phase 1: Data Model & Migration
- Create `Room` model with all fields
- Add `room_id` FK to `Booking` model (nullable)
- Create Alembic migration
- Add `rooms` relationship to `Listing`
- Unit tests for Room model

### Phase 2: Room Repository & API
- Create `RoomRepository` with CRUD operations
- Create `/api/rooms` endpoints (list, get, update)
- Add `/api/listings/{id}/rooms` endpoint
- Update `/api/listings/{id}` to include rooms
- Unit tests for repository

### Phase 3: Cloudbeds Room Sync
- Add `get_rooms(property_id)` to CloudbedsService
- Update SyncService to sync rooms from Cloudbeds
- Associate bookings with rooms during sync
- Rename button to "Sync Rooms from Cloudbeds"
- Integration tests for room sync

### Phase 4: Room-Level iCal
- Update iCal generator for room-level feeds
- Add `/ical/{listing_slug}/{room_slug}.ics` endpoint
- Remove property-level iCal endpoint
- Update booking queries to filter by room
- Integration tests for room iCal

### Phase 5: Admin UI - Rooms
- Add expandable room list to listing cards
- Room enable/disable toggle
- Room iCal URL copy button
- Room slug edit functionality

### Phase 6: Custom Fields Enhancement
- Add `guest_phone_last4` to AVAILABLE_FIELDS
- Add as default field in `create_defaults_for_listing()`
- Create `/api/listings/{id}/available-custom-fields` endpoint
- Update admin UI with dropdown for field selection
- Unit tests for new field and endpoint

### Phase 7: Polish & Testing
- Manual testing of full workflow
- Documentation updates
- Performance validation
- Final code review

## Complexity Tracking

No constitution violations identified. Implementation follows existing patterns.

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Cloudbeds API rate limits | Implement room caching, batch sync |
| Large room counts (100+) | Pagination in API and lazy loading in UI |
| Breaking existing bookings | Nullable room_id FK, no data loss on migration |

## Dependencies

- Cloudbeds API `getRooms` endpoint (must verify access)
- Existing Listing, Booking, CustomField models
- Admin UI JavaScript (vanilla JS, no framework)
