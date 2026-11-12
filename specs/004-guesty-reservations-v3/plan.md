<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->
# Implementation Plan: Guesty Reservations V3 Migration

**Branch**: `004-guesty-reservations-v3` | **Date**: 2026-11-11 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/004-guesty-reservations-v3/spec.md`

## Summary

Guesty retires the legacy reservations Open API (`/v1/reservations`) on 2027-03-01. This plan moves the Guesty provider's reservation reads to Reservations V3. The sync's observable behaviour stays the same:

- the same bookings
- the same UIDs and `Booking ID:` lines
- the same door codes
- the same safety guarantees

The legacy path stays behind an add-on toggle for rollback until a release after the retirement date.

The approach, from [research.md](research.md):

- **One stay-window sweep per cycle.** Each sync cycle runs one account-wide, stay-window-filtered V3 search, sorted by `_id`. It is attributed to listings client-side through every `stay[]` segment, which solves V3's first-segment-only listing filter.
- **Completeness and absence.** A strict completeness guard checks every page. No booking is retired on absence without a by-IDs confirmation.
- **Inline custom fields.** They remove the per-reservation custom-field call.
- **Guest lookups.** These move to the non-retiring `guests-crud` resource.
- **Change key.** A content-hash change key replaces `lastUpdatedAt` for enrichment reuse.

There is no database migration.

## Technical Context

**Language/Version**: Python 3.14 (ruff `target-version = "py314"`, mypy strict)
**Primary Dependencies**: FastAPI, SQLAlchemy[asyncio] 2.x, aiosqlite, httpx, APScheduler, icalendar, Pydantic/pydantic-settings. No new dependency.
**Storage**: SQLite (WAL) via aiosqlite with Alembic. **No schema change**; the head stays in the `f5a6b7c8d9e0` lineage (R13).
**Testing**: pytest with pytest-asyncio (auto), using the existing `tests/{unit,integration,contract}` layout. Fixtures are synthetic and built from the documented V3 schema, and are checked by the `check-fixture-pii` hook.
**Target Platform**: Home Assistant add-on (Alpine container, s6-overlay), plus standalone uvicorn for development
**Project Type**: Single project: a FastAPI backend with an embedded admin SPA
**Performance Goals**: Per steady-state cycle, Guesty requests stay at or below the previous release (FR-027, SC-006). On the reference account (3 listings, ~101 retained, 5-minute interval) that is **~15–20 V3 vs ~26–39 legacy**, about 180–240 per hour against the 5,000/h limit. Reservation pages per cycle scale with the account's in-window reservations, not with total history (SC-007).
**Constraints**:

- Guesty limits: 15/s, 120/min and 5,000/h, shared with the user's other integrations.
- Token budget: 5 per 24h (unchanged; the token manager is untouched).
- V3 has no `lastUpdatedAt`, no field projection on search, `hasMore`-only paging, and a listing filter that matches only the first segment.
- At most 10 IDs per by-IDs call.

**Scale/Scope**: Single tenant, at most ~50 listings, thousands of bookings. The change touches one provider (Guesty), the sync service, config/add-on wiring, and one admin API/UI indication.

## Constitution Check

*GATE: Must pass before Stage 0 research. Re-check after Stage 1 design.* (Re-checked after Stage 1; still passes.)

Verify feature design compliance with `.specify/memory/constitution.md`:

- [x] **Code Quality**:
  - SPDX headers on every new file: `reservations_v3.py`, `legacy_reservations.py`, `description_fields.py`, `translations/en.yaml`, and the new tests.
  - ruff, mypy strict and 100% interrogate apply unchanged.
  - The legacy code moves verbatim into its own module in a pure-refactor commit, so it can be deleted cleanly later.
- [x] **Testing Standards**: test-first at three levels.
  - **Contract**: exact V3 query serialisation, by-IDs batching, and the guests-crud path/fields.
  - **Unit**: guard checks, status mapping, interval/ID derivation, change key, inline custom fields.
  - **Integration**: end-to-end sync, FR-011a, alteration, toggle round-trip, and request-budget call counts (mandatory for the caching and reuse behaviour).
  - All existing tests must pass in both modes where they apply.
- [x] **UX Consistency**:
  - The add-on option follows the existing `log_level` pattern and gets a translations entry.
  - The retired-field badge uses existing admin UI patterns.
  - Error messages follow the existing actionable style (they name the setting to change).
  - Logs never contain PII (FR-043).
- [x] **Performance**:
  - The goals above are measurable and enforced by a counting-transport test (`v3_calls ≤ legacy_calls`).
  - The per-cycle request count is logged at INFO.
- [x] **Commit Discipline**:
  - Atomic, signed, DCO commits with `Co-Authored-By` and a ≤50-character subject.
  - The refactor (legacy isolation) and behaviour changes go in separate commits.
  - No hook bypass.

*No violations.*

## Key Design Decisions

| # | Decision | Research |
|---|---|---|
| D1 | V3 calls: `/v1/reservations-v3/search`, `/v1/reservations-v3?reservationIds=` and `/v1/guests-crud/{id}?fields=fullName phone email`. Listings, token and the V3 custom-fields endpoint are unchanged. Legacy mode is byte-for-byte unchanged. | R1 |
| D2 | **First-segment problem**: no server listing filter. One account-wide stay-window sweep per cycle, cached on the provider instance, including a cached failure. Each listing claims the reservations that have any `stay[]` segment whose `listingId` is in its ownership scope, or which are unassigned on its parent unit type. This catches moves whose first segment is in another or untracked property. Cost is `ceil(R_in_window/100)` pages per cycle, which is 2 for the reference account. | R2, R6 |
| D3 | Window: `filter[checkOut][gte]` = UTC date(now−91d)−1d and `filter[checkIn][lte]` = UTC date(now+365d)+1d. In-progress stays are always included (checkOut ≥ today). Client-side `_overlaps_window` stays authoritative. A test pins lookback ≥ retention (90d)+1. | R3 |
| D4 | Paging: `sort=_id`, `limit=100`, `skip`, cap 500. Nine guard checks: shape, skip echo, empty `hasMore` page, short `hasMore` page, missing ID, duplicate, non-ascending ID, cap, retries. Any failure aborts every listing in the cycle, keeping last-good. | R4 |
| D5 | FR-011a: rows the prune would retire are confirmed by by-IDs (9 candidates + 1 control per call). They retire only on canceled, out-of-scope, out-of-window, excluded or omitted-with-control. alteration or an unverifiable result keeps the row; in-scope live aborts the listing. This costs 0 calls in the steady state. | R5 |
| D6 | Intervals: segments merge per room when adjacent. The first interval is `{rid}::{room}`, identical to legacy for single-segment reservations; later non-adjacent ones are `{rid}::{room}#{n}`. Rooms resolve from the interval, not by parsing the ID. | R6 |
| D7 | Status: `completed`→`checked_out`. `alteration` is a hold status: stored rows freeze status, dates and room, and enrichment refreshes. If first seen in `alteration`, the reservation is not stored, logged at DEBUG, and never retired (**PM to confirm**). Health guard thresholds are unchanged, and `inquiry`/`reserved` stay excluded. | R7 |
| D8 | Inline `customFields` (present/absent/malformed). Absent with stored values costs one confirmation call. Malformed keeps last-good. | R8 |
| D9 | Reuse key: SHA-256 `change_key` over the relevant V3 fields, stored as `_change_key` in `enrichment_data`. `_guest_name` is also persisted. Every #129 invariant is preserved (NULL vs `{}`, `_guest_source_id` gating, freshness window, failed lookups never wipe codes, no stale last-4). | R9 |
| D10 | Description fields: `nightsCount` is derived from localized dates; `guestsCount` comes from the covered segments; `notes` is other/guest/specialRequests joined with "; ", excluding `keyCode`/`cleaning`. `money_*` are retired, with a one-time WARNING and an admin "Retired" badge, and configuration is kept. | R10 |
| D11 | Toggle: add-on option `guesty_reservations_api: v3\|legacy`, default `v3`, carried to the provider via env, `Settings` and the factory, then a `ReservationSource` strategy. Legacy is isolated in `legacy_reservations.py`. | R11 |
| D12 | Identity check: the same `confirmationCode` under a different ID, with the old ID absent, aborts the listing and advises a switch to legacy. | R14 |
| D13 | **No DB migration.** | R13 |
| D14 | FR-042 live items and FR-043 diagnostics (DEBUG, no PII). The per-cycle request count by resource is logged at INFO. | R15 |

### Per-sync request estimate (reference account; R12)

| | Legacy (today) | V3 |
|---|---|---|
| Listing docs | 3 | 3 |
| Reservation pages | 3–6 | 2 |
| Guest lookups | 10–15 | 10–15 |
| Custom-field calls | 10–15 | 0 |
| By-IDs confirmations | — | 0 (steady state) |
| **Total** | **~26–39** | **~15–20** |

## Owner Decisions Before Implement

1. **First-seen `alteration` (D7).** The proposal is to withhold the reservation quietly and never retire it; it would not appear in the withheld-reservations panel. The alternative is to show it in the panel.
2. **Enrichment during the alteration freeze (D7).** Status, dates and room are frozen, but door-code enrichment keeps refreshing. This refines the clarify answer "keep status and data", and was adopted in review of PR #197.
3. **Sweep vs scoped search (D2).** The sweep satisfies FR-016, but it can exceed legacy request counts for an account that tracks only a small part of a large portfolio (FR-027 is measured on the reference account). The fallback is a per-listing scoped search, which misses cross-property first-segment moves. The recommendation is the sweep, with the page count logged.
4. **`notes` / `guestsCount` mapping (D10).** `keyCode` is excluded from `notes` because it is a door secret.

## Release Gate

| Milestone | Target | Gate |
|---|---|---|
| Pre-release | mid-December 2026 | all tests green; default `v3` |
| Stable | by 2027-01-31 | SC-010: every FR-042 item confirmed on a live account ([quickstart.md](quickstart.md) §4) |
| Legacy removal | a release after 2027-03-01 | delete `legacy_reservations.py`, the `legacy` option value and its tests |

The owner cuts all releases.

## Project Structure

### Documentation (this feature)

```text
specs/004-guesty-reservations-v3/
├── spec.md              # Feature spec (clarified)
├── plan.md              # This file
├── research.md          # Stage 0: R1–R15 design decisions
├── data-model.md        # Stage 1: DTOs, identities, enrichment keys, settings
├── quickstart.md        # Stage 1: dev, toggle, validation, rollback, release gate
├── contracts/
│   ├── guesty-v3-search.md
│   ├── guesty-v3-by-ids.md
│   ├── guesty-guests-crud.md
│   ├── addon-toggle.md
│   └── admin-custom-fields.md
├── checklists/requirements.md
└── tasks.md             # Stage 2 (/speckit.tasks), not created here
```

### Source Code (repository root)

```text
rentalsync-bridge/
├── config.yaml                                   # + guesty_reservations_api option/schema
├── translations/en.yaml                          # NEW: option names/descriptions
├── rootfs/etc/s6-overlay/s6-rc.d/rentalsync/run  # export GUESTY_RESERVATIONS_API
├── src/
│   ├── config.py                                 # + guesty_reservations_api setting
│   ├── providers/
│   │   ├── base.py                               # + PMSOccupancyInterval, InlineCustomFields,
│   │   │                                         #   AbsenceVerdict, HOLD_STATUSES,
│   │   │                                         #   PMSReservation.intervals/change_key/
│   │   │                                         #   inline_custom_fields, confirm_absent(),
│   │   │                                         #   retired_description_fields
│   │   ├── factory.py                            # pass reservations_api to Guesty
│   │   └── guesty/
│   │       ├── service.py                        # GuestyProvider: delegates to a ReservationSource
│   │       ├── legacy_reservations.py            # NEW: verbatim move of the legacy fetch/map
│   │       ├── reservations_v3.py                # NEW: sweep, guard, attribution, mapping,
│   │       │                                     #   by-IDs confirmation, change key
│   │       └── description_fields.py             # NEW: RETIRED_DESCRIPTION_FIELDS
│   ├── services/
│   │   └── sync_service.py                       # intervals, hold status, FR-011a confirm,
│   │                                             #   identity check, change-key reuse,
│   │                                             #   bookkeeping keys, request-count log
│   ├── api/custom_fields.py                      # + retired / retired_reason
│   └── static/js/admin.js                        # "Retired" badge
└── tests/
    ├── contract/test_guesty_v3_contract.py       # NEW
    ├── unit/test_guesty_v3_source.py             # NEW
    ├── unit/test_guesty_service.py               # legacy mode + toggle wiring
    ├── unit/test_config.py                       # setting
    ├── unit/test_provider_registry.py            # factory
    ├── integration/test_guesty_v3_sync.py        # NEW: end-to-end, FR-011a, alteration, FR-006
    ├── integration/test_guesty_v3_toggle.py      # NEW: SC-011
    ├── integration/test_guesty_v3_budget.py      # NEW: SC-006/SC-007 call counts
    ├── integration/test_custom_fields_api.py     # retired flag
    └── integration/test_logging_privacy.py       # V3 paths, no PII
```

**Structure Decision**: This is the existing single project under `rentalsync-bridge/`. New code goes in the Guesty provider package, behind a strategy seam (`ReservationSource`), so the legacy path can be deleted after 2027-03-01 without touching V3 or the sync service. Changes to the sync service are provider-agnostic and default to today's behaviour, so Cloudbeds is unchanged.

## Test Strategy

- **Fixtures.** They are synthetic V3 payloads written from the documented schema (search item, by-IDs item, guests-crud) and include:
  - single-unit and multi-unit (assigned and unassigned) reservations
  - a same-room re-entry (`#2`)
  - a cross-property move whose first segment is untracked
  - long in-progress stays
  - every status, including `alteration` and `completed`
  - absent and malformed `customFields`
- **Transport.** A fake transport (`httpx.MockTransport`) records every request, for:
  - contract assertions on the exact query
  - call-count assertions (SC-006, reuse, one sweep per cycle, zero custom-field calls)
- **Guard and absence.** Every R4 guard condition and every R5 verdict has a test. The SC-003 test asserts that no booking is retired after any abort.
- **Legacy parity.**
  - Existing Guesty tests run in legacy mode unchanged.
  - A parametrised identity test asserts single-segment UIDs are equal across modes (SC-002, SC-011).
- **Privacy and diagnostics.** The logging-privacy test is extended to the V3 paths. The FR-043 diagnostics are asserted at DEBUG/INFO.

## Complexity Tracking

No constitution violations.
