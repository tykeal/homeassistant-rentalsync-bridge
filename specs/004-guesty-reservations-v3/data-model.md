<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->
# Data Model: Guesty Reservations V3 Migration

**Feature Branch**: `004-guesty-reservations-v3`
**Date**: 2026-11-11 (revised 2026-11-12: combined listing search, by-IDs re-attribution)

**No database schema change and no Alembic migration** (research R13). Every change in this document is one of:

- a provider DTO
- an in-memory value object
- a configuration setting
- a new bookkeeping key inside the existing `Booking.enrichment_data` JSON column

---

## 1. Provider DTOs (`src/providers/base.py`)

### 1.1 `PMSReservation`: additive, backward compatible

| Field | Type | Status | Meaning |
|---|---|---|---|
| `pms_booking_id` … `updated_at` | (unchanged) | existing | as today. Legacy and Cloudbeds populate exactly what they do now |
| `intervals` | `tuple[PMSOccupancyInterval, ...]` | **new**, default `()` | per-room occupancy intervals (R6). When non-empty, the sync creates one booking per interval and **ignores** `room_ids`. When empty, it uses today's `room_ids` behaviour (Cloudbeds, legacy Guesty) |
| `change_key` | `str \| None` | **new**, default `None` | content hash used for enrichment reuse when `updated_at` is unavailable (R9) |
| `inline_custom_fields` | `InlineCustomFields \| None` | **new**, default `None` | custom-field values delivered with the reservation (R8). `None` means the provider does not deliver them inline (legacy, Cloudbeds) |

V3 mode populates the existing fields as follows:

- `check_in` and `check_out` span the owned intervals: the earliest interval start and the latest interval end.
- `room_ids` is the tuple of distinct interval room IDs, kept for logging and compatibility.
- `updated_at` is `None`.
- `guest_name` is `None`, because V3 embeds no guest.

**Validation (V3 mapper)**

- `intervals` is non-empty for every DTO returned for a listing.
- The intervals are chronological.
- No two intervals for the same room overlap.

### 1.2 `PMSOccupancyInterval` (**new**, frozen dataclass)

| Field | Type | Meaning |
|---|---|---|
| `room_id` | `str` | the segment `listingId`: a sub-unit ID, a single-unit listing ID, or the parent unit type when unassigned (FR-019) |
| `ordinal` | `int` (≥ 1) | 1 for the first interval in this room within the reservation, 2 for the next, and so on |
| `check_in` | `datetime` (UTC) \| `None` | first merged segment's `checkIn`; `None` only in a by-IDs re-attribution (§2.3) |
| `check_out` | `datetime` (UTC) \| `None` | last merged segment's `checkOut`; `None` only in a by-IDs re-attribution |
| `local_dates` | `tuple[date, date] \| None` | the interval's listing-localized check-in and check-out dates. Always set; for a by-IDs re-attribution (R5) it is the **only** date source, and `check_in`/`check_out` are re-anchored from the stored row by the sync |
| `guests_count` | `int \| None` | the **maximum** of the merged segments' `guestsCount` (non-additive: adjacent segments describe the same guests over time). It equals the common value when they agree. `None` when no merged segment has a count |

**Derived booking ID**: `f"{rid}::{room_id}"` if `ordinal == 1`, otherwise `f"{rid}::{room_id}#{ordinal}"`.

**Invariant**: for a single-segment reservation there is exactly one interval with `ordinal=1`, and its booking ID equals the legacy `{rid}::{_resolve_room_id(raw)}` (FR-005, FR-007, SC-002).

### 1.3 `InlineCustomFields` (**new**, frozen dataclass)

| Field | Type | Meaning |
|---|---|---|
| `state` | `Literal["present", "absent", "malformed"]` | see R8 |
| `values` | `Mapping[str, Any]` | `{fieldId: value}` when `present`, otherwise empty |

### 1.4 `AbsenceVerdict` (**new**, `StrEnum`) and `AbsenceResult` (**new**, frozen dataclass)

`AbsenceVerdict`: `CONFIRMED_ABSENT`, `UNCONFIRMED`, `REATTRIBUTED` and `INCONSISTENT`, with semantics as in research R5.

| `AbsenceResult` field | Type | Meaning |
|---|---|---|
| `verdict` | `AbsenceVerdict` | the per-candidate outcome |
| `reservation` | `PMSReservation \| None` | set only for `REATTRIBUTED`: the reservation rebuilt from the normalised by-IDs item (§2.3), with `intervals` computed by the same R6 code as a search result, interval dates as **localized dates only** (see `PMSOccupancyInterval.local_dates`), `inline_custom_fields=None`, `change_key=None`, `guest_name=None` and `guest_id` from the item's top-level `guestId` (or `None`), which the sync compares with the stored `_guest_source_id` (R5). Its reservation-level `check_in`/`check_out` are the localized dates at 00:00 in the listing timezone, used for logging only; the sync never writes them (R5) |
| `detail` | `str` | non-PII reason for logs (for example "no control", "unrequested _id") |

### 1.5 `PMSProvider` interface additions

| Member | Default (base class) | Guesty V3 | Guesty legacy | Cloudbeds |
|---|---|---|---|---|
| `begin_cycle(listing_pms_ids: Sequence[str]) -> None` | no-op | records the cycle's tracked listings for the combined search (R2) | no-op | no-op |
| `async confirm_absent(listing_pms_id, reservation_ids) -> dict[str, AbsenceResult]` | every ID → `CONFIRMED_ABSENT` (today's behaviour: absence retires) | by-IDs plus control, with re-attribution (R5) | base default (unchanged legacy behaviour) | base default |
| `retired_description_fields` (property) | `{}` | `RETIRED_DESCRIPTION_FIELDS` | `{}` | `{}` |

**Note**: The base default reproduces current pruning exactly, so Cloudbeds and legacy Guesty behaviour do not change. Only V3 mode, the path whose paging cannot prove completeness, requires positive confirmation.

### 1.6 Status constants

| Constant | Location | Value |
|---|---|---|
| `RECOGNISED_NON_EXPORT_STATUSES` | base.py | unchanged: `{expired, closed, declined, awaiting_payment}` |
| `HOLD_STATUSES` | base.py (**new**) | `{"alteration"}`, never stored, never retires (R7) |
| V3 `_STATUS_MAP` | `guesty/reservations_v3.py` | `confirmed→confirmed`, `checked_in→checked_in`, `checked_out→checked_out`, `completed→checked_out`, `canceled\|cancelled→cancelled` |
| V3 excluded | same | `{inquiry, reserved}` → `None` |

### 1.7 Status lifecycle as seen by the sync (V3 mode)

```text
             (first seen)                     (stored)
  alteration ───────────► not stored ──┐   alteration ──► stored row frozen
                                       │                  (status/dates/room),
  confirmed/checked_in/checked_out/    │                  enrichment refreshed
  completed ─────────────────► stored, exported
  expired/closed/declined/awaiting_payment ─► stored verbatim, withheld (DEBUG)
  unknown ───────────────────► stored verbatim, withheld, panel (WARNING)
  canceled ──────────────────► stored row → cancelled (retired)
  absent from snapshot ─► FR-011a confirm ─► CONFIRMED_ABSENT → cancelled
                                          ├► UNCONFIRMED → unchanged
                                          ├► REATTRIBUTED → rows in a still-occupied
                                          │   room kept (status + re-anchored dates);
                                          │   other rows retired (room change)
                                          └► INCONSISTENT → abort this listing's
                                             sync (no other listing affected)
```

---

## 2. Snapshot (provider-internal, `guesty/reservations_v3.py`)

### 2.1 `V3CombinedSearch` (per provider instance, which means per sync cycle)

| Field | Type | Meaning |
|---|---|---|
| `tracked` | `frozenset[str]` | listing PMS IDs declared by `begin_cycle`, plus any listing requested outside that set |
| `filter_ids` | `tuple[str, ...]` | sorted union of the tracked listings' fetch scopes (§2.2) |
| `chunks` | `tuple[tuple[str, ...], ...]` | `filter_ids` packed by family into chunks of at most `MAX_LISTING_IDS_PER_SEARCH = 50` (R2) |
| `window` | `(date, date)` | the `D_start` and `D_end` sent to Guesty (R3) |
| `items` | `tuple[dict, ...]` | validated raw search items, de-duplicated by `reservationId` across chunks |
| `pages` | `int` | pages fetched over all chunks (diagnostics, SC-007) |
| `error` | `PMSProviderError \| None` | set when any chunk failed its completeness guard, or two chunks returned conflicting copies of one reservation. Every listing in the cycle re-raises it |

**Validation**: research R4 checks 1–9 **per chunk**, then the cross-chunk de-duplication rule (R2). Any failure sets `error`, and no partial `items` are kept.

### 2.2 Listing scope (unchanged semantics)

| Name | Members | Used for |
|---|---|---|
| fetch scope | listing ID ∪ child sub-unit IDs ∪ parent unit type (imported child) | contributes to the combined search's `filter_ids` (R2) |
| ownership scope | listing ID ∪ child sub-unit IDs | which segments the listing owns (FR-017, FR-020), for search and by-IDs results alike |
| parent IDs | `mtl.p` / parent keys of the listing doc | recognising an unassigned segment of a multi-unit parent (FR-019) |
| timezone | listing doc `timezone` | re-anchoring re-attributed dates (R5) |

### 2.3 By-IDs item normalisation (`normalise_by_ids_item`, pure function)

The by-IDs resource returns a different shape from search. Before attribution, each item is normalised to the search shape so the same R6 code computes intervals and identities (FR-005: byte-identical `pms_booking_id`, UID and `Booking ID:` line whichever endpoint supplied the data):

| Search shape | By-IDs shape | Normalisation |
|---|---|---|
| `reservationId` | `_id` | copied |
| `status` | `status` | copied (R7 mapping applies afterwards) |
| `stay[].listingId` | `stay[].unitId`, `stay[].unitTypeId` | `unitId ?? unitTypeId` |
| `stay[].parentListingId` | — | `unitTypeId` when `unitId` is present and differs from it, else absent |
| `stay[].checkIn`/`checkOut` (UTC) | — | absent; intervals carry `local_dates` only |
| `stay[].check*DateLocalized` | same | copied |
| reservation-level window test | `stay[0].checkInDateLocalized`, `stay[-1].checkOutDateLocalized` | compared with the padded R3 window, the same bounds the search filter used |

A missing `_id`, an empty `stay[]`, or a segment with neither `unitId` nor `unitTypeId` makes the item invalid, and the whole batch `UNCONFIRMED` (R5 whole-response validation).

Two consecutive segments in the same normalised room whose `checkOutDateLocalized` equals the next `checkInDateLocalized` are **ambiguous**: without UTC instants, R6 cannot tell adjacency (merge) from a same-day gap (`#2`). Such a candidate's verdict is `UNCONFIRMED`, never `REATTRIBUTED`, so no `#n` row is retired on a guess (R5).

---

## 3. Persistence (`src/models/booking.py`: schema unchanged)

### 3.1 `Booking.pms_booking_id`

| Form | When |
|---|---|
| `{rid}` | provider gave no rooms (Cloudbeds without room). Unchanged |
| `{rid}::{roomId}` | first or only interval in a room. **Unchanged for every pre-existing booking** |
| `{rid}::{roomId}#{n}` | **new**: the *n*-th (n ≥ 2) non-adjacent interval in the same room (FR-018) |

`_extract_base_reservation_id` (`rsplit("::", 1)[0]`) returns `rid` for all three forms. `Booking.room_id` comes from `interval.room_id` through `RoomRepository.get_by_pms_id`. It is never parsed back out of the booking ID.

### 3.2 `Booking.enrichment_data` keys

| Key | Owner | Exported to custom data? | Written when | Fallback rules |
|---|---|---|---|---|
| custom-field `fieldId`s | custom fields | yes | inline `present`, or a successful confirmation call | last-good on failure or malformed (R8) |
| `guest_phone`, `guest_email`, `guest_phone_last4` | guest | yes (as today) | successful guest lookup | last-good only when `_guest_source_id` matches (FR-031) |
| `_guest_source_id` | guest (bookkeeping) | **no** | successful guest lookup | — |
| `_guest_name` | guest (bookkeeping, **new**) | **no** | successful guest lookup with a non-empty name | reused only when `_guest_source_id` matches. For a row written by the previous release (no `_guest_name`), the stored `Booking.guest_name` is the bootstrap fallback under the same `_guest_source_id` check |
| `_change_key` | sync (bookkeeping, **new**) | **no** | **only** after a fully successful enrichment of a reservation with `change_key` | never carried forward by a fallback |

The NULL vs `{}` distinction is unchanged. `None` means never stored, so it is not reusable. `{}` means a successful empty read.

`_exportable()` drops every key in `BOOKKEEPING_KEYS = {_guest_source_id, _guest_name, _change_key}`.

### 3.3 `Booking.pms_updated_at`

- Legacy mode: written as today.
- V3 mode: `None`, because the DTO carries no `updated_at`.
- Switching modes therefore forces exactly one full enrichment pass, which is safe.

### 3.4 Unchanged tables

The following tables are not modified:

- `listings`
- `rooms`
- `custom_fields` (configuration preserved, FR-038)
- `available_fields`
- `oauth_credentials`
- `system_settings`

---

## 4. Configuration

| Setting | Source | Type / values | Default | Consumer |
|---|---|---|---|---|
| `guesty_reservations_api` | add-on option → env `GUESTY_RESERVATIONS_API` → `Settings` | `"v3"` \| `"legacy"` (lower-cased, stripped; invalid values fail at startup) | `"v3"` | `providers/factory.py` → `GuestyProvider(reservations_api=…)` |

Cloudbeds ignores the setting.

---

## 5. Description-field registry

| Key | V3 mode | Legacy mode |
|---|---|---|
| `source`, `confirmationCode` | top-level item fields | as today |
| `guestsCount` | interval `guests_count`, as an int string | as today |
| `notes` | `other; guest; specialRequests` (non-empty, `"; "`), with `cleaning` and `keyCode` excluded | as today |
| `nightsCount` | derived from the localized reservation dates | as today |
| `money_totalPaid`, `money_balanceDue`, `money_currency` | **retired**: absent, flagged in the UI and log | as today |

`RETIRED_DESCRIPTION_FIELDS` (in `guesty/description_fields.py`) maps each retired key to its reason:

| Key | Reason |
|---|---|
| `money_totalPaid` | "Guesty Reservations V3 no longer includes payment totals on reservations" |
| `money_balanceDue` | same reason |
| `money_currency` | same reason |

---

## 6. State held per sync cycle (not persisted)

| Item | Scope | Purpose |
|---|---|---|
| `V3CombinedSearch` | provider instance | one combined (possibly chunked) search per cycle (R2) |
| request counter by resource | provider instance | FR-043 per-cycle INFO line, and SC-006 test assertions |
| retired-field warning emitted | process | one-time notice (FR-037) |
