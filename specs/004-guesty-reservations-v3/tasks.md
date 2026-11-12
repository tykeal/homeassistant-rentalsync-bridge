<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->
# Tasks: Guesty Reservations V3 Migration

**Input**: Design documents from `/specs/004-guesty-reservations-v3/`
**Prerequisites**: plan.md ✓, spec.md ✓, research.md ✓, data-model.md ✓, contracts/ ✓, quickstart.md ✓

**Tests**: Required. FR-039 and FR-040 mandate them, and constitution Principle II mandates test-first. Within each phase, the test tasks come first. Write them, run them and confirm they **fail** for the intended reason, then implement. Request-count assertions are mandatory for every caching or reuse behaviour (SC-006).

**Organization**: The phases are:

- Phases 1–2: shared setup and blocking foundations (no story label)
- Phases 3–9: one phase per user story, in spec priority order (US1–US7)
- Phase 10: polish

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel: a different file, and no dependency on an incomplete task
- **[US1]–[US7]**: the user stories in spec.md
  - **US1** (P1): calendars and door codes keep working through retirement
  - **US2** (P1): an incomplete or inconsistent fetch never revokes a door code
  - **US3** (P2): multi-unit and moved stays land in the right room
  - **US4** (P2): status changes are reflected safely
  - **US5** (P3): the bridge stays within the shared request budget
  - **US6** (P3): description settings carry over visibly
  - **US7** (P3): real users can validate and roll back

## Path Conventions

- Python paths are relative to the project directory `rentalsync-bridge/`, which holds `src/`, `tests/`, `config.yaml`, `rootfs/` and `translations/`.
- Repository-level paths start with `docs/`, `releasenotes/` or `specs/`.
- Run commands from `rentalsync-bridge/` with `uv run …`.

## Commit Guidance

Follow constitution Principle V:

- **Each completed task is its own atomic commit.** It is signed, has a DCO sign-off and a `Co-Authored-By` trailer, and a subject of at most 50 characters.
- **Each task commit is followed by a separate `Docs:` commit** that ticks that task's checkbox in this file.
- **Test tasks are committed before their implementation tasks.** Run each test, confirm it fails for the intended reason, and record that in the test commit body. The PR head must be green before push for review.
- T004 is a **pure refactor** and must be committed alone, with no behaviour change.
- Never bypass pre-commit.

---

## Phase 1: Setup (shared test infrastructure)

**Purpose**: Synthetic V3 fixtures and a counting fake transport used by every later phase.

- [x] T001 Create synthetic V3 payload builders as a factory fixture `guesty_v3_payloads` in tests/conftest.py. Include:
  - `search_item()`, `stay_segment()`, `search_page(results, skip, limit, has_more)`
  - `by_ids_item()` in the by-IDs shape (`_id`, per-segment `unitId`/`unitTypeId`, localized dates only), and `as_by_ids(search_item)` that converts one search item to the by-IDs shape so both endpoints describe the same reservation
  - `guests_crud_body()`
  - multi-segment helpers: same-property move, move from another **tracked** listing, A→B→A, unassigned parent
  - a stored reservation re-arranged so its first segment is in an **untracked** property and a later segment stays in a tracked room (FR-011a re-attribution), and a never-stored reservation that starts in an untracked property (the FR-016 residual gap)
  - every V3 status

  Follow contracts/guesty-v3-search.md, contracts/guesty-v3-by-ids.md and contracts/guesty-guests-crud.md. Values must be synthetic, because the `check-fixture-pii` hook scans them. Helpers live in `conftest.py`, never in a non-`test_*.py` module under `tests/`, because the `name-tests-test --pytest-test-first` hook rejects such modules.
- [x] T002 Create a counting fake Guesty transport as a fixture `guesty_transport` in tests/conftest.py.
  - Build it on `httpx.MockTransport`. It routes listings, legacy `/v1/reservations`, `/v1/reservations-v3/search`, `/v1/reservations-v3?reservationIds=`, `/v1/reservations-v3/{id}/custom-fields`, `/v1/guests/{id}` and `/v1/guests-crud/{id}`.
  - It records every request (path and parsed query) and counts per resource.
  - It applies `filter[listingId][in]` (matching the **first** segment's `listingId` or `parentListingId`, as Guesty documents for `unitId`/`unitTypeId`), `filter[checkOut][gte]`/`filter[checkIn][lte]` and `sort=_id`/`skip`/`limit` server-side, so the residual gap and narrowing are observable (FR-015, FR-016).
  - It supports injected faults: short or empty `hasMore` pages, duplicates, out-of-order IDs, 400, 429, and an ignored `reservationIds` parameter.
- [x] T003 Build a reference-account fixture `guesty_reference_account` in tests/conftest.py for SC-006 and SC-007:
  - 3 tracked listings, one of them multi-unit, plus 1 untracked listing
  - about 110 reservations within the window, plus multi-year out-of-window history
  - about 12 reservations inside the freshness window

  Expose it in both legacy and V3 shapes from one source, so the two modes describe the same data. This depends on T001.

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: The strategy seam, DTO extensions and setting that every story builds on. Legacy and Cloudbeds behaviour must be unchanged at the end of this phase. The full existing suite must pass.

**⚠️ CRITICAL**: No user-story work begins until this phase is complete.

**Deviation (owner-approved, delivery plan)**: The work ships as eight PRs, one or more phases each. The `guesty_reservations_api` default is `legacy` on main from this phase through Phase 8, so main stays releasable while V3 is incomplete. Tests that exercise V3 select `v3` explicitly. The final PR (Phase 9 and Phase 10) flips the default to `v3` in Settings, config.yaml, the run script, translations and docs as its own atomic commit. Until then:

- `Settings.guesty_reservations_api` defaults to `"legacy"` (T005, T006).
- The `V3ReservationSource` stub raises `PMSProviderError`, not `NotImplementedError` (T008). Selecting `v3` before it is implemented aborts the listing's sync before any snapshot reaches the prune loop, so it fails safely like any other provider error and never yields an empty snapshot.
- T009's tests extend the existing tests/unit/test_pms_provider_base.py rather than creating tests/unit/test_provider_base.py.
- T013's manual-sync test lives in the existing tests/integration/test_listings_api.py; there is no tests/unit/test_listings_api.py.

- [x] T004 Pure refactor (plan D11, research R11):
  - Move the legacy reservation fetch, guard and mapping verbatim from src/providers/guesty/service.py into a `LegacyReservationSource` class in a new src/providers/guesty/legacy_reservations.py. That covers `get_reservations` paging, `_guard_unclassified`, `_belongs_to_listing`, `_resolve_room_id`, `_map_reservation` and their helpers and constants.
  - Define a `ReservationSource` Protocol (`fetch(listing_pms_id, ...) -> list[PMSReservation]`).
  - `GuestyProvider` delegates to the source.

  No behaviour change. tests/unit/test_guesty_service.py, tests/integration/test_guesty_sync.py and tests/integration/test_guesty_edge_cases.py must pass unmodified, except for import paths.
- [x] T005 [P] Write failing tests in tests/unit/test_config.py for `Settings.guesty_reservations_api`:
  - the default is `"legacy"` until the final PR flips it to `"v3"` (see the deviation note above)
  - `" Legacy "` normalises to `"legacy"`
  - an invalid value raises at construction
  - the `GUESTY_RESERVATIONS_API` env var is read
- [x] T006 Add `guesty_reservations_api: Literal["v3", "legacy"]` with a normalising validator to `Settings` in src/config.py (contracts/addon-toggle.md). This depends on T005.
- [x] T007 [P] Write failing tests in tests/unit/test_provider_registry.py:
  - `create_provider_for_credential` passes `reservations_api` from settings to `GuestyProvider`
  - `GuestyProvider(reservations_api="legacy")` selects `LegacyReservationSource`
  - `"v3"` selects `V3ReservationSource` (a stub at this point)
  - Cloudbeds ignores the setting
- [x] T008 Wire `reservations_api` through src/providers/factory.py into `GuestyProvider.__init__` in src/providers/guesty/service.py.
  - Create src/providers/guesty/reservations_v3.py with a `V3ReservationSource` stub that raises `PMSProviderError` (deviation note above), so V3 is not yet selectable as working code and selecting it fails safely.
  - Existing Guesty tests construct the provider with `reservations_api="legacy"`.

  This depends on T004, T006 and T007.
- [x] T009 [P] Write failing unit tests in tests/unit/test_pms_provider_base.py (existing; see the deviation note above):
  - `PMSOccupancyInterval.booking_suffix` for ordinal 1 vs ≥ 2
  - the defaults of `PMSReservation.intervals`, `change_key` and `inline_custom_fields`
  - `InlineCustomFields` states
  - `AbsenceVerdict` members
  - the `HOLD_STATUSES` contents
  - `AbsenceResult` (a `reservation` only with `REATTRIBUTED`)
  - the base `begin_cycle` is a no-op
  - the base `confirm_absent` default returns `CONFIRMED_ABSENT` for every ID
  - the base `retired_description_fields` is empty

  See data-model.md §1.
- [x] T010 Add `PMSOccupancyInterval` (including `local_dates`), `InlineCustomFields`, `AbsenceVerdict`, `AbsenceResult`, `HOLD_STATUSES`, the three optional `PMSReservation` fields, `PMSProvider.begin_cycle()`, `PMSProvider.confirm_absent()` and `PMSProvider.retired_description_fields` with base defaults in src/providers/base.py. This depends on T009.
- [x] T011 Write failing tests in tests/unit/test_sync_service.py that pin the default code paths:
  - with the base `confirm_absent` default, prune behaviour is unchanged for Cloudbeds and Guesty legacy
  - an empty `intervals` keeps today's `room_ids` behaviour

  These are regression guards for the T012 seams. This depends on T010.
- [x] T012 Add provider-agnostic seams in src/services/sync_service.py:
  - The prune loop collects candidates after the in-progress guard and before retiring. It calls `provider.confirm_absent(listing.pms_id, base_ids)` and retires only `CONFIRMED_ABSENT`. `INCONSISTENT` raises `SyncServiceError` for this listing before commit. `REATTRIBUTED` keeps the rows for now; US2 completes it.
  - `_create_bookings_for_reservation` branches on `reservation.intervals`: one booking per interval, with ID `{rid}::{room}` or `{rid}::{room}#{n}`, and `room_id` resolved from `interval.room_id` through `RoomRepository.get_by_pms_id`.

  This depends on T011.

- [x] T013 [P] Write failing tests for the cycle hook (research R2):
  - in tests/unit/test_scheduler.py, a scheduled cycle calls `provider.begin_cycle` once per provider with the PMS IDs of every `enabled` and `sync_enabled` listing, before the first `sync_listing`
  - in tests/integration/test_listings_api.py, the manual single-listing sync declares the same set before syncing its listing
- [x] T014 Call `begin_cycle` from src/services/scheduler.py and from the manual sync in src/api/listings.py. This depends on T010 and T013.

**Checkpoint**: The full suite is green. Legacy Guesty and Cloudbeds behave identically. The toggle exists and defaults to `legacy`; V3 is a stub that fails safely with `PMSProviderError` when selected.

---

## Phase 3: User Story 1: Calendars and door codes keep working (Priority: P1) 🎯 MVP

**Goal**: In V3 mode, a single-segment account syncs to byte-identical feeds, door codes and `Booking ID:` lines without touching any retiring resource.

**Independent Test**:

- Seed a DB by syncing the reference account in legacy mode, then switch to V3 and sync.
- Every UID, date and `Booking ID:` line is identical (SC-002).
- The transport recorded no request to `/v1/reservations` or `/v1/guests/{id}` (FR-001).

### Tests for User Story 1 (write first, confirm they fail)

- [x] T015 [P] [US1] Write a contract test in tests/contract/test_guesty_v3_contract.py (new) for the search request (contracts/guesty-v3-search.md):
  - the exact query for page 0 and page 1: `filter[listingId][in]` (comma-joined, sorted fetch-scope IDs of the tracked listings), `filter[checkOut][gte]`, `filter[checkIn][lte]`, `sort=_id`, `limit=100`, `skip`
  - no `fields`
  - an untracked listing's ID never appears in the filter
  - the window dates computed from a frozen clock per research R3, including both 1-day pads
- [x] T016 [US1] Write a contract test in tests/contract/test_guesty_v3_contract.py for guests-crud (contracts/guesty-guests-crud.md):
  - the path `/v1/guests-crud/{id}` and `fields=fullName phone email`
  - 404 → `None`, 5xx → `PMSProviderError`, 401 → `PMSAuthenticationError`
  - legacy mode still calls `/v1/guests/{id}`
- [x] T017 [P] [US1] Write unit tests in tests/unit/test_guesty_v3_source.py (new) for single-segment mapping:
  - `pms_booking_id`, UTC `check_in`/`check_out`, one interval with `ordinal=1` whose `room_id` equals the legacy `_resolve_room_id` for the same stay
  - `guest_id`, with `guest_name=None` and `updated_at=None`
  - `custom_data` keys `source` and `confirmationCode`
  - `InlineCustomFields` PRESENT, ABSENT and MALFORMED parsing (research R8)
- [x] T018 [US1] Write unit tests in tests/unit/test_guesty_v3_source.py for the combined search (research R2), with call-count assertions:
  - after `begin_cycle` with three listings, `get_reservations` for each issues the search pages exactly once per cycle
  - a `get_reservations` for a listing outside the declared set adds it and starts a fresh combined search
  - the filter ID set is the union of fetch scopes: a tracked multi-unit parent contributes its sub-unit IDs, and an imported child contributes its parent unit type
  - a fetch-scope resolution failure aborts only that listing, whose own ID is still filtered
- [x] T019 [US1] Write unit tests in tests/unit/test_guesty_v3_source.py for chunking (FR-016a):
  - 50 filter IDs make one chunk and 51 make two
  - a multi-unit family is never split unless it alone exceeds 50 IDs
  - a reservation returned by two chunks with the same change key is kept once; with differing change keys the combined result fails (FR-011)
- [x] T020 [P] [US1] Write integration tests in tests/integration/test_guesty_v3_sync.py (new) for custom-field enrichment (research R8):
  - PRESENT makes no custom-field call
  - ABSENT with non-NULL `{}`-shaped enrichment makes no call
  - ABSENT with stored non-empty values makes exactly one confirmation call. On success its values are used; on failure the last-good values are kept
  - ABSENT with NULL `enrichment_data` makes one confirmation call. On failure, `_bootstrap_custom_fields` recovery applies
  - MALFORMED keeps last-good and logs a WARNING without aborting
  - a published door-code custom field is never blanked in any of these cases
- [x] T021 [US1] Write integration tests in tests/integration/test_guesty_v3_sync.py for identity verification (research R14, FR-006):
  - the same `confirmationCode` under a new ID, with the old ID absent → `SyncServiceError`; nothing created or retired; the message names the setting `guesty_reservations_api`
  - matching IDs → the INFO line reports the matched count
- [x] T022 [US1] Write the upgrade-continuity integration test in tests/integration/test_guesty_v3_sync.py (SC-002):
  - sync the reference account in legacy mode, render the feeds, switch the provider to V3, sync, and render again
  - assert every single-segment event's UID, dates, `SUMMARY` and `Booking ID:` line are byte-identical
  - assert that no retiring resource was requested
  - assert that door codes (`guest_phone_last4` and custom-field codes) are unchanged

  This depends on T003.

### Implementation for User Story 1

- [x] T023 [US1] Implement `V3ReservationSource` window computation and the combined search in src/providers/guesty/reservations_v3.py:
  - `V3_LOOKBACK_DAYS = 91`, with pads, against `DEFAULT_LOOKAHEAD_DAYS`
  - `begin_cycle`, the fetch-scope union and `filter[listingId][in]`
  - chunk packing by multi-unit family with `MAX_LISTING_IDS_PER_SEARCH = 50`, and cross-chunk de-duplication by `reservationId`
  - `sort=_id`, `limit=100`, `skip` paging, `MAX_RESERVATION_PAGES` reuse per chunk
  - the per-instance `V3CombinedSearch` cache (data-model.md §2.1)

  The minimal guard is shape and `hasMore`; the full guard comes in US2. This makes T015, T018 and T019 pass.
- [x] T024 [US1] Implement the single-segment V3 mapping in src/providers/guesty/reservations_v3.py:
  - status via `_STATUS_MAP`, initially confirmed, checked_in, checked_out and canceled
  - top-level and stay dates to UTC
  - one interval per segment with `room_id = stay.listingId`
  - `custom_data` for `source`/`confirmationCode`
  - `InlineCustomFields` parsing
  - client-side `_overlaps_window` kept authoritative

  This makes T017 pass.
- [x] T025 [US1] Implement V3 `get_guest` via `/v1/guests-crud/{id}?fields=fullName phone email` in src/providers/guesty/service.py. It branches on `reservations_api`, keeps the legacy `/v1/guests/{id}` path unchanged, and makes T016 pass.
- [x] T026 [US1] Implement inline custom-field enrichment in `SyncService._custom_field_enrichment` in src/services/sync_service.py, per research R8:
  - PRESENT → values, with no call
  - ABSENT → no call if `enrichment_data` is non-NULL with no custom values; otherwise one confirmation call, with last-good or bootstrap on failure
  - MALFORMED → last-good, with a WARNING

  This makes T020 pass.
- [x] T027 [US1] Implement the FR-006 identity check in src/services/sync_service.py, before any upsert or prune for the listing. It maps stored active bookings' `custom_data.confirmationCode` to base IDs, aborts on a mismatch, and logs the first-sync matched count at INFO. This makes T021 pass.
- [x] T028 [US1] Make the V3 path end-to-end, so that T022 passes:
  - replace the `PMSProviderError` stub so `GuestyProvider(reservations_api="v3")` serves `get_reservations` from the combined search, partitioned per listing by simple single-segment ownership (`stay[0].listingId` in the ownership scope); the full multi-segment attribution comes in US3
  - fix any parity gaps T022 exposes

**Checkpoint**: The V3 MVP works for single-segment accounts. US1 is independently testable with T015–T022.

---

## Phase 4: User Story 2: Incomplete or inconsistent fetch never revokes a door code (Priority: P1)

**Goal**: Every incompleteness or shift signal aborts the cycle with last-good kept. Absence retires a booking only with by-IDs confirmation.

**Independent Test**:

- Inject each R4 fault and each by-IDs outcome on the fake transport.
- After each, the published calendar and stored bookings are unchanged, except for `CONFIRMED_ABSENT` retirements. A sync error is recorded.

### Tests for User Story 2 (write first, confirm they fail)

- [x] T029 [P] [US2] Write unit tests in tests/unit/test_guesty_v3_source.py for each research R4 guard check:
  - (1) missing or malformed `results`/`pagination`/`hasMore`
  - (2) skip echo mismatch or `len > limit`
  - (3) empty `hasMore` page
  - (4) short `hasMore` page
  - (5) missing `reservationId`
  - (6) duplicate across pages
  - (7) non-ascending `reservationId`
  - (8) page cap
  - (9) 429 after retries
  - (10) any of these in the second chunk, after the first chunk succeeded

  Each must raise `PMSProviderError` with no partial items, and the failure must be cached so a second listing in the same cycle raises without new requests (call-count assertion).
- [x] T030 [US2] Write unit tests in tests/unit/test_guesty_v3_source.py:
  - HTTP 400 on search → `PMSProviderError` naming the status and an excerpt (FR-021), never an empty result
  - a missing `status` key → abort (FR-026)
- [x] T031 [P] [US2] Write contract tests in tests/contract/test_guesty_v3_contract.py for by-IDs (contracts/guesty-v3-by-ids.md):
  - form-exploded `reservationIds`
  - ≤ 10 IDs per call, the control always included, and 9 candidates per batch
  - no request when there are no candidates
  - with no control available, the call is still made: a returned cancelled candidate is `CONFIRMED_ABSENT` and an omitted one is `UNCONFIRMED`
  - every outcome row, including `REATTRIBUTED` vs `INCONSISTENT` on the first segment's `unitId`/`unitTypeId` membership in the searched filter IDs, and whole-response validation (unrequested `_id` or duplicate → batch `UNCONFIRMED`)
  - the ignored-parameter fixture retires nothing
  - control missing → `UNCONFIRMED` with ERROR
  - HTTP 400 → `UNCONFIRMED` with ERROR
- [x] T032 [P] [US2] Write unit tests in tests/unit/test_guesty_v3_source.py for `normalise_by_ids_item` (data-model.md §2.3, FR-005):
  - for a single-segment stay, an assigned multi-unit stay, an unassigned parent and A→B→A, the same reservation from `search_item` and from `as_by_ids` yields byte-identical `pms_booking_id`s and interval rooms
  - `_id` → `reservationId`; `listingId = unitId ?? unitTypeId`; `parentListingId` only when `unitId` differs from `unitTypeId`
  - intervals carry `local_dates` only, with no UTC instants
  - two same-room segments whose localized boundary dates are equal are reported as ambiguous, whether UTC-adjacent or separated by a same-day gap
  - an item with no `_id`, no `stay[]`, or a segment with neither `unitId` nor `unitTypeId` is invalid
- [x] T033 [P] [US2] Write integration tests in tests/integration/test_guesty_v3_sync.py for FR-011a:
  - a reservation vanishing mid-search (US2-5) whose first segment is in the searched filter IDs is not retired when by-IDs returns it live; the verdict is `INCONSISTENT`, that listing aborts with last-good kept and an error naming the `guesty_reservations_api` setting, and the other listings in the cycle sync normally
  - a stored reservation re-arranged so its first segment is in an untracked property (US3-6) is `REATTRIBUTED`: the row in the still-occupied room is kept with its mapped status, its dates are re-anchored (unchanged local dates keep the stored instants byte-for-byte; a changed local date keeps the stored local time of day in the listing timezone), its custom fields and guest details stay last-good, and its UID and `Booking ID:` line are unchanged
  - the same re-arranged reservation over three consecutive cycles never aborts a sync, stays exported and costs one by-IDs call per cycle (call-count assertion)
  - a re-arranged reservation with no segment left in a tracked listing retires
  - a stored reservation in room A re-arranged to untracked U → tracked B (residual gap): A's row retires, B is not created, and an INFO line names the reservation and B
  - same-room segments meeting on one local date give `UNCONFIRMED` and retire no `#n` row
  - a re-anchored row whose check-out would not be after its check-in (a same-day assignment) makes the whole candidate `UNCONFIRMED`: no row updated or retired
  - a re-attributed reservation reassigned to another guest publishes no previous-guest contact or last-4 and makes one `guests-crud` call for the new guest; with the same `guestId` it makes no guest call; with no `guestId` it publishes no guest contact and keeps its custom fields (FR-031)
  - a re-attributed `alteration` reservation is held (`UNCONFIRMED`)
  - a canceled reservation absent from the window retires
  - an omitted candidate with the control returned retires
  - an unverifiable result keeps the row
  - the steady state makes 0 by-IDs calls
- [x] T034 [P] [US2] Write integration tests in tests/integration/test_in_progress_reservations.py, extended to V3 mode (SC-004, US2-3):
  - a stay that checked in 400 days ago and checks out in the future is in the snapshot and stays exported across syncs
  - a test pins `V3_LOOKBACK_DAYS >= OLD_BOOKING_RETENTION_DAYS + 1`
- [x] T035 [US2] Write an integration test in tests/integration/test_guesty_v3_sync.py for SC-003: for every R4 fault injected mid-search, in either chunk, no booking is retired and the cached feed is unchanged.

### Implementation for User Story 2

- [x] T036 [US2] Implement the full research R4 completeness guard per chunk and the cached combined-search failure in src/providers/guesty/reservations_v3.py, plus 400 surfacing and missing-status abort. This makes T029, T030 and T035 pass.
- [x] T037 [US2] Implement `GuestyProvider.confirm_absent()` for V3 mode in src/providers/guesty/reservations_v3.py (with delegation in service.py), per research R5:
  - batching and control selection
  - whole-response validation
  - `normalise_by_ids_item` and per-candidate verdicts against scope, window and the searched filter IDs
  - `REATTRIBUTED` results carrying the normalised reservation, with intervals from the same R6 code

  `INCONSISTENT` never poisons the cached search. Legacy mode keeps the base default. This makes T031 and T032 pass.
- [x] T038 [US2] Finish FR-011a integration in the src/services/sync_service.py prune path:
  - group candidates by base ID
  - an `INCONSISTENT` verdict raises before commit for this listing only
  - `REATTRIBUTED`: keep and mark seen the rows whose `pms_booking_id` matches an interval, apply the mapped status and re-anchored dates (stored instants, listing timezone), retire other rows as room changes, keep `custom_data` last-good, guard guest-owned enrichment by `guestId` vs `_guest_source_id` (research R5), downgrade the candidate to `UNCONFIRMED` when any re-anchored row is invalid (validated before any write), and exclude the result from the health guard
  - log `UNCONFIRMED` at WARNING or ERROR as specified

  This makes T033 and T034 pass.

**Checkpoint**: US1 and US2 together form the P1 release candidate. Door-code safety is proven by T029–T035.

---

## Phase 5: User Story 3: Multi-unit and moved stays land in the right room (Priority: P2)

**Goal**: Segment-level attribution across tracked listings, with stable interval identities.

**Independent Test**: Sync each multi-segment fixture from T001. Each interval appears only in its room's feed, for exactly its dates, and single-segment IDs are unchanged (SC-005).

### Tests for User Story 3 (write first, confirm they fail)

- [x] T039 [P] [US3] Write unit tests in tests/unit/test_guesty_v3_source.py for attribution and intervals (research R6):
  - assigned single unit → `{rid}::{unit}` (US3-1)
  - unassigned parent → listing-only (US3-2, FR-019)
  - move from another tracked listing (first segment in tracked listing X, later segment in tracked listing Y) → captured for Y, with only Y's dates (US3-3)
  - a never-stored reservation whose first segment is in an untracked property is not returned by the filtered search, and the residual-gap diagnostic is absent until it is (US3-7; asserted against the fake transport's first-segment filter)
  - A→B same-property move → two intervals (US3-4)
  - A→B→A → `{rid}::A`, `{rid}::B`, `{rid}::A#2`
  - adjacent same-room segments merge, with `guests_count` = max
  - sibling-only → not attributed (US3-5, FR-020)
  - missing `stay` or segment `listingId` → abort
- [x] T040 [P] [US3] Write integration tests in tests/integration/test_guesty_v3_sync.py:
  - each interval becomes its own booking with the correct `room_id` and room-feed membership
  - the DTO span equals the owned span
  - a room change that removes a `#n` interval retires that row through the room-change prune path
  - `_extract_base_reservation_id` returns `rid` for `#n` IDs

### Implementation for User Story 3

- [x] T041 [US3] Implement multi-segment attribution in src/providers/guesty/reservations_v3.py:
  - ownership-scope and parent-ID matching across every `stay[]` segment
  - adjacent same-room merging
  - ordinals and `#n`
  - per-interval `guests_count` (max)
  - DTO `check_in`/`check_out` = owned span
  - `room_ids` = distinct interval rooms

  Replace the US1 first-segment partitioning. This makes T039 pass.
- [x] T042 [US3] Complete interval handling in src/services/sync_service.py:
  - room resolution per interval
  - unassigned parent → listing-level booking with no room, as today
  - prune of disappearing interval IDs
  - verify the #116 informational log is preserved

  This makes T040 pass.

**Checkpoint**: SC-005 is met. The single-segment identity tests from US1 still pass.

---

## Phase 6: User Story 4: Status changes reflected safely (Priority: P2)

**Goal**: The full V3 status vocabulary, with `alteration` held, `completed` exported, unknown statuses withheld and the health guard kept.

**Independent Test**: Sync a live stay through confirmed → alteration (with changed dates) → confirmed. It stays continuously exported with its original dates until it leaves `alteration` (SC-008). Each other status yields the policy outcome.

### Tests for User Story 4 (write first, confirm they fail)

- [x] T043 [P] [US4] Write unit tests in tests/unit/test_guesty_v3_source.py for status mapping (research R7):
  - `completed`→`checked_out`
  - `canceled`/`cancelled`→`cancelled`, case-insensitive (FR-024)
  - `inquiry`/`reserved` excluded
  - `expired`/`closed`/`declined`/`awaiting_payment` stored and withheld
  - an unknown status is stored verbatim with a WARNING
  - `alteration` is passed through as a hold status
  - the health guard counts `alteration` as classified, with thresholds unchanged (FR-025)
- [x] T044 [P] [US4] Write integration tests in tests/integration/test_guesty_v3_sync.py for the alteration lifecycle (FR-023, SC-008):
  - for a stored live stay, status, dates and room are frozen while V3 reports new dates
  - enrichment (custom fields and guest contact) refreshes during the hold
  - the stay is never retired or withheld
  - leaving `alteration` applies the new dates
  - first-seen `alteration` is not stored, is logged at DEBUG, is not in the withheld panel, and is never retired
  - by-IDs `alteration` for a candidate gives `UNCONFIRMED`
- [x] T045 [US4] Write an integration test in tests/integration/test_guesty_v3_sync.py for an unknown status: it appears in the withheld-bookings panel API (US4-3), and the over-50% unclassified case aborts with last-good (US4-4).

### Implementation for User Story 4

- [x] T046 [US4] Complete `_STATUS_MAP`, the excluded set and the health-guard classification in src/providers/guesty/reservations_v3.py, reusing the base `RECOGNISED_NON_EXPORT_STATUSES` and `HOLD_STATUSES`. This makes T043 pass.
- [x] T047 [US4] Implement hold-status handling in src/services/sync_service.py:
  - a stored base ID → mark all rows seen, skip the upsert, run an enrichment-only refresh that writes `custom_data` and enrichment without touching status, dates or room
  - no stored row → skip with a DEBUG log

  This makes T044 and T045 pass.

**Checkpoint**: The full status policy holds in V3 mode.

---

## Phase 7: User Story 5: Within the shared request budget (Priority: P3)

**Goal**: V3 steady-state requests are at or below legacy (SC-006). The #129 freshness and no-leak invariants hold without `lastUpdatedAt`.

**Independent Test**: Run the reference account through two steady-state cycles in each mode on the counting transport. `v3_calls ≤ legacy_calls`, and the second V3 cycle makes no guest or custom-field calls for reservations outside the freshness window.

### Tests for User Story 5 (write first, confirm they fail)

- [x] T048 [P] [US5] Write unit tests in tests/unit/test_guesty_v3_source.py for `change_key` (research R9):
  - it is deterministic across key order
  - it changes on each included field: status, guestId, dates, stay segments, customFields, notes, source, confirmationCode
  - it is unchanged by ignored fields such as financial blocks
- [x] T049 [P] [US5] Write integration tests in tests/integration/test_guesty_v3_sync.py for reuse (research R9):
  - reuse only when all R9 conditions hold
  - no reuse when `enrichment_data` is NULL
  - no reuse inside the freshness window (US5-3)
  - no reuse when any stored booking is inside the freshness window on its frozen dates during an alteration hold
  - `_change_key` is not persisted after a failed lookup
  - the bookkeeping keys `_guest_source_id`, `_guest_name` and `_change_key` never reach `custom_data` or the description
- [x] T050 [US5] Write integration tests in tests/integration/test_guesty_v3_sync.py for guest failure (FR-031, FR-032, US5-4, US5-5):
  - after a reassignment with a failed lookup, no previous-guest last-4 is published
  - a failed lookup for the same guest keeps last-good contact and `_guest_name`
  - on a first-V3-sync failure for a row written by the previous release (no `_guest_name`), a matching `_guest_source_id` keeps `Booking.guest_name`
  - with a non-matching source, the placeholder is used
- [x] T051 [US5] Write the budget integration test in tests/integration/test_guesty_v3_budget.py (new):
  - run the reference account in legacy and V3 modes over two cycles and assert `v3_calls ≤ legacy_calls` (SC-006)
  - assert that reservation pages per cycle equal `ceil(in_window/100)`, independent of out-of-window history size (SC-007)
  - assert one combined search per cycle, and one page sequence per chunk
  - assert that no reservation of the untracked listing is fetched beyond those that also touch a tracked listing
  - assert zero custom-field calls when inline data is PRESENT

  This depends on T003.

### Implementation for User Story 5

- [ ] T052 [US5] Implement `change_key` computation in the V3 mapper in src/providers/guesty/reservations_v3.py. This makes T048 pass.
- [ ] T053 [US5] Implement change-key reuse in src/services/sync_service.py:
  - `BOOKKEEPING_KEYS` and the `_exportable` filter
  - `_guest_name` persistence
  - `_change_key` persistence, written only on full success
  - the `_can_reuse_enrichment` change-key branch, including the stored-booking freshness check
  - `_StoredEnrichment.guest_name` for the upgrade name bootstrap

  This makes T049 and T050 pass.
- [ ] T054 [US5] Add per-cycle request counters by resource to `GuestyProvider` in src/providers/guesty/service.py. Log one INFO line per cycle (`search=… by_ids=… guests=… custom_fields=… listings=…`) when the provider closes (FR-043). Then make T051 pass, tuning only within the R9 and R12 design.

**Checkpoint**: SC-006 and SC-007 are enforced by tests, and the #129 invariants are preserved.

---

## Phase 8: User Story 6: Description settings carry over visibly (Priority: P3)

**Goal**: Same keys and meaning for `source`, `confirmationCode`, `guestsCount`, `notes`, `nightsCount` and custom fields. `money_*` are retired with a visible notice. Configuration is never deleted.

**Independent Test**: With every description field enabled on the previous release, upgrade and sync in V3 mode. Each field renders equivalently or is flagged "Retired" in the admin API and UI and in a one-time WARNING (SC-009).

### Tests for User Story 6 (write first, confirm they fail)

- [ ] T055 [P] [US6] Write unit tests in tests/unit/test_guesty_v3_source.py for the description fields (research R10):
  - `guestsCount` is per interval (max), rendered as an int string, and equals legacy for single-segment stays
  - `notes` joins other, guest and specialRequests with `"; "` and excludes `cleaning` and `keyCode`; when all are empty the key is omitted
  - `nightsCount` is computed from the localized reservation dates and equals legacy; unparsable dates omit it
  - no `money_*` keys are produced
- [ ] T056 [P] [US6] Write integration tests in tests/integration/test_custom_fields_api.py (contracts/admin-custom-fields.md):
  - GET custom-fields, GET available-custom-fields and the PUT response carry `retired`/`retired_reason` for `money_*` in V3 mode
  - the flags are false in legacy mode and for Cloudbeds
  - PUT round-trips a retired field, and its configuration is never deleted
- [ ] T057 [P] [US6] Write a unit test in tests/unit/test_sync_service.py for the one-time WARNING per enabled retired field. It names the listings, is logged once per process, and is not logged in legacy mode (FR-037).
- [ ] T058 [P] [US6] Write a test in tests/integration/test_guesty_v3_sync.py for custom-field label continuity: labels resolved from the account definitions are unchanged in V3 mode (US6-3, FR-035).

### Implementation for User Story 6

- [ ] T059 [US6] Add `guestsCount`, `notes` and `nightsCount` mapping to src/providers/guesty/reservations_v3.py. This makes T055 pass.
- [ ] T060 [US6] Create src/providers/guesty/description_fields.py with `RETIRED_DESCRIPTION_FIELDS`. Expose it through `GuestyProvider.retired_description_fields` in V3 mode only, and add a static `retired_fields_for(pms_type, reservations_api)` helper.
- [ ] T061 [US6] Add `retired` and `retired_reason` to `CustomFieldResponse` and `AvailableFieldResponse` and populate them on GET and PUT in src/api/custom_fields.py. This depends on T060 and makes T056 pass.
- [ ] T062 [US6] Emit the one-time retired-field WARNING at the first V3 sync in src/services/sync_service.py. This depends on T060 and makes T057 pass.
- [ ] T063 [P] [US6] Show a "Retired" badge, with `retired_reason` as its title, for retired fields in src/static/js/admin.js. Add any badge style in src/templates/admin.html. The field stays editable.

**Checkpoint**: SC-009 is met.

---

## Phase 9: User Story 7: Real users can validate and roll back (Priority: P3)

**Goal**: An add-on toggle exposed to users, rollback documentation, an actionable legacy-retirement error, and FR-042/FR-043 diagnostics without PII.

**Independent Test**:

- Switch V3 → legacy → V3 on one DB. Single-segment UIDs are unchanged and `#n` rows retire on legacy (SC-011).
- DEBUG logs answer every research R15 item and contain no names, phones or emails.

### Tests for User Story 7 (write first, confirm they fail)

- [ ] T064 [P] [US7] Write an integration test in tests/integration/test_guesty_v3_toggle.py (new) for SC-011:
  - V3 → legacy → V3 against one DB, with no migration
  - single-segment UIDs and `Booking ID:` lines are unchanged
  - multi-segment `#n` rows retire in legacy mode while the first interval keeps `{rid}::{room}`
- [ ] T065 [P] [US7] Write a unit test in tests/unit/test_guesty_service.py: in legacy mode, a 404 or 410 from `/v1/reservations` raises `PMSProviderError` with an actionable message to switch `guesty_reservations_api` to `v3`, and last-good is kept.
- [ ] T066 [P] [US7] Extend tests/integration/test_logging_privacy.py to V3 mode at DEBUG:
  - no guest name, phone, email, door code or `keyCode` appears in any record
  - the research R15 diagnostic lines are present: status histogram, segment-shape lines with **every segment's listing ID** per reservation (FR-043), filter IDs and chunk count, per-chunk pagination signals, window-narrowing counts, matched-ID count, FR-011a verdict counts with the INFO re-attribution lines, per-cycle request counts

### Implementation for User Story 7

- [ ] T067 [US7] Add the `guesty_reservations_api: v3` option and the `list(v3|legacy)?` schema to config.yaml. Create translations/en.yaml with names and descriptions for `log_level` and `guesty_reservations_api`, with an SPDX header or a `REUSE.toml` entry (contracts/addon-toggle.md).
- [ ] T068 [US7] Export `GUESTY_RESERVATIONS_API` (default `v3`; the same PR flips the `Settings` default from `legacy` to `v3`, see the Phase 2 deviation note) and log the selected value in rootfs/etc/s6-overlay/s6-rc.d/rentalsync/run.
- [ ] T069 [US7] Implement the legacy 404/410 message in src/providers/guesty/legacy_reservations.py. This makes T065 pass.
- [ ] T070 [US7] Add the FR-043 DEBUG diagnostics (research R15) in src/providers/guesty/reservations_v3.py and src/services/sync_service.py. Use reservation and listing IDs, counts and enum values only. This makes T066 pass.
- [ ] T071 [US7] Make T064 pass. Expected to need no code beyond T012 and T042; fix any gap found.
- [ ] T072 [P] [US7] Document the option, pre-release validation (quickstart §4) and rollback (quickstart §5) in docs/homeassistant-addon-setup.md. Include the multi-segment rollback limitation and the legacy removal after 2027-03-01.

**Checkpoint**: The pre-release is ready for FR-042 validation.

---

## Phase 10: Polish & Cross-Cutting Concerns

- [ ] T073 [P] Add a reno release note in releasenotes/notes/ (FR-004):
  - upgrade before 2027-03-01
  - the new option and the rollback path
  - the retired `money_*` fields
  - the multi-segment rollback limitation
  - the legacy removal in a release after 2027-03-01
- [ ] T074 [P] Update rentalsync-bridge/README.md and README.md wherever they describe Guesty endpoints or settings.
- [ ] T075 Run the full suite and static checks from rentalsync-bridge/: `uv run pytest tests/ -q`, `uv run ruff check src/ tests/`, `uv run mypy src/` and interrogate through pre-commit. Fix all findings.
- [ ] T076 Validate quickstart.md §3 commands against the final test file names, and update specs/004-guesty-reservations-v3/quickstart.md if any changed.
- [ ] T077 Re-check that `GuestyProvider` makes no request to `/v1/reservations` or `/v1/guests/{id}` in V3 mode across the whole test suite. This uses the transport's request log in a session-level assertion in tests/contract/test_guesty_v3_contract.py (FR-001, FR-040).

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)**: none. T001 → T002 → T003, all in tests/conftest.py.
- **Foundational (Phase 2)**: depends on Phase 1. T004 must be committed first and alone. It blocks all stories. T014 must land before T023 relies on `begin_cycle`.
- **US1 (Phase 3)**: depends on Phase 2. **This is the MVP.**
- **US2 (Phase 4)**: depends on US1, because it extends the combined search built in T023.
- **US3 (Phase 5)**: depends on US1 (it replaces the T028 partitioning) and on the T012 interval seam.
- **US4 (Phase 6)**: depends on US1. It can run in parallel with US3. T047 must merge after T038, because both edit the sync prune and seen sets.
- **US5 (Phase 7)**: depends on US1 and on US4 (the stored-booking freshness check covers alteration holds).
- **US6 (Phase 8)**: depends on US1. It can run in parallel with US2–US5. T061 depends on T060.
- **US7 (Phase 9)**: T064 depends on US3. T066 depends on US2–US5 diagnostics. The rest depend only on Phase 2.
- **Polish (Phase 10)**: after all the stories.

### Story completion order

```text
Setup → Foundational → US1 (MVP) ─┬─► US2 ──────────────┐
                                  ├─► US3 ──────────────┤
                                  ├─► US4 ──► US5 ──────┼─► US7 ─► Polish
                                  └─► US6 ──────────────┘
```

- US2, US3, US4 and US6 each need only US1.
- US5 also needs US4.
- US7 needs US2–US5 (T064, T066).

### Within each story

- Tests come first and must fail.
- Then the provider mapping or source, then the sync service, then the API or UI.
- Each story ends at a checkpoint with its tests green and the full suite green.

---

## Parallel Examples

```text
# Parallel means distinct files only. Tasks sharing a file run sequentially.

# Phase 1: sequential (all in tests/conftest.py)
T001 → T002 → T003

# Phase 2 tests
T005 (test_config.py)  ‖  T007 (test_provider_registry.py)  ‖  T009 (test_pms_provider_base.py)  ‖  T013 (scheduler, listings API)

# US1 tests: one task per file in parallel, then the same-file follow-ups
T015 (contract)  ‖  T017 (unit v3 source)  ‖  T020 (integration v3 sync)
then  T016 (contract), T018 → T019 (unit), T021 → T022 (integration)

# US2 tests
T029 (unit)  ‖  T031 (contract)  ‖  T033 (integration v3 sync)  ‖  T034 (in-progress)
then  T030 → T032 (unit), T035 (integration v3 sync)

# After US1: US3, US4 and US6 provider/API work in parallel (different modules);
# their src/services/sync_service.py edits are sequenced
```

---

## Implementation Strategy

### P1 validation checkpoint (US1 + US2)

1. Phase 1 → Phase 2 (legacy unchanged; full suite green).
2. US1. Validate single-segment parity (T022).
3. US2. Validate door-code safety (T029–T035). This is an **internal P1 validation checkpoint, not a shippable scope**: the alteration hold (US4) and the request budget (US5) are not in place yet. Nothing is released before all phases are complete.

### Incremental delivery

1. US3 and US4 (P2): multi-unit correctness and the status vocabulary. These are required before the pre-release, because real accounts have them.
2. US5 (P3): budget. Required before the pre-release (FR-027 is a MUST, and a 5-minute interval without reuse would exceed legacy).
3. US6 and US7 (P3): visibility, the toggle UX, diagnostics and docs. Required for the pre-release (FR-041, FR-042, FR-043).

### Release gate (owner cuts releases)

| Milestone | Target | Gate |
|---|---|---|
| Pre-release | mid-December 2026 | all phases complete; default `v3` |
| Stable | by 2027-01-31 | SC-010: every FR-042 item confirmed on a live account |

---

## Notes

- [P] means a different file and no dependency on an incomplete task.
- A test task and its implementation may be committed together only if the test was first seen to fail. Record that in the commit body.
- Never use `--no-verify` or `git reset` after a failed hook. Fix the problem and recommit.
- Owner decisions 1–4 in plan.md ("Owner Decisions Before Implement") were confirmed on 2026-11-12: the alteration behaviour (T047), the combined listing search (T023, T028, T041) and the notes/guestsCount mapping (T059). Decision 5 (enrichment of re-attributed reservations) does not gate implementation: FR-011a already requires last-good values, and any refresh from by-IDs data would be a later refinement.
