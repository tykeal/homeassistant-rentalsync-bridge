<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->
# Research: Guesty Reservations V3 Migration

**Feature Branch**: `004-guesty-reservations-v3`
**Date**: 2026-11-11 (revised 2026-11-12: combined listing search, by-IDs re-attribution)
**Status**: Complete (documentation-only; live confirmation deferred to FR-042)

All Guesty facts below come from `open-api-docs.guesty.com`: the Reservations V3 search and by-IDs references, the reservation migration guide, the 2026-11-05 changelog, the guests-crud reference and the rate-limit guide. They were read on 2026-11-10/11. No live calls were made because there is no Guesty test account. Every fact that the design depends on and that the documentation leaves ambiguous is listed in [R15](#r15-live-verification-items-fr-042-and-diagnostics-fr-043) for pre-release validation.

Code facts were read from the live source on `main` at the 004 clarify merge (`04f9346`).

---

## R1: Which Guesty resources the provider calls, before and after

### Findings

| Call today (`src/providers/guesty/service.py`) | Retiring 2027-03-01? | V3-mode replacement |
|---|---|---|
| `POST /oauth2/token` (auth.py) | No | unchanged |
| `GET /v1/listings`, `GET /v1/listings/{id}` (import, `_listing_scopes`) | No | unchanged |
| `GET /v1/reservations` (`get_reservations`) | **Yes** | `GET /v1/reservations-v3/search` |
| `GET /v1/guests/{id}` (`get_guest`) | Unclear — the migration guide points guest reads at guests-crud | `GET /v1/guests-crud/{id}?fields=…` |
| `GET /v1/reservations-v3/{id}/custom-fields` (`get_custom_fields`) | No (already V3) | used only as a rare confirmation call (R8) |
| `GET /v1/accounts/me`, `GET /v1/accounts/{id}/custom-fields` | No | unchanged (24 h process cache) |
| — | — | **new**: `GET /v1/reservations-v3?reservationIds=…` (FR-011a confirmation, R5) |

### Decision

- In V3 mode the provider calls only non-retiring resources (FR-001, SC-001).
- The guest lookup moves to `guests-crud` in V3 mode.
- Legacy mode keeps the previous release's calls byte-for-byte, so a rollback really is the known-good path (R11).

### Alternatives considered

- **Keep `/v1/guests/{id}` in V3 mode until Guesty confirms it is retiring.** Rejected. If it disappears on 2027-03-01, door-code phone last-4 values silently stop refreshing. `guests-crud` is the documented target and costs the same one call.

---

## R2: Reservation retrieval — one combined listing-filtered search per sync cycle

### Context

`filter[listingId]` on `/v1/reservations-v3/search` "matches unitId or unitTypeId on the **first** stay segment" only. FR-016 (as clarified 2026-11-12) requires one combined, listing-filtered search over the tracked listings, attribution through every segment, and a documented residual gap for reservations whose first segment is in an untracked property.

### Findings

- The filter options are:
  - `filter[listingId]` (eq / in, first segment only; a comma-separated value is an implicit `in`)
  - `filter[status]` (equality, inequality, inclusion and exclusion operators)
  - `filter[checkIn]` and `filter[checkOut]` (eq / gte / gt / lte / lt, localized `YYYY-MM-DD`)
  - `filter[createdAt]`
- Several filters are ANDed. There is no filter on later segments, and no projection.
- `sort` accepts `_id`, `checkIn`, `checkOut` or `createdAt`, each with an optional `-` prefix. `limit` is ≤ 100, with `skip` paging.
- The response is `{results, pagination:{skip, limit, hasMore}}`, with no total count.
- An unsupported filter, operator or sort returns HTTP 400. V1 ignored these silently.
- Every result carries `stay[]` in chronological order, and each segment has `listingId` (= `unitId ?? unitTypeId`) and `parentListingId`. `parentListingId` is present only for multi-unit listings.

### Decision

- **Tracked set.** Before the first `get_reservations` of a cycle, the caller declares the cycle's tracked listings through a new optional provider hook, `begin_cycle(listing_pms_ids)`. The scheduler passes every `enabled` and `sync_enabled` listing; the manual single-listing sync passes the same set, so a manual sync sees what a scheduled one sees. The base-class hook is a no-op, so Cloudbeds is unaffected. A `get_reservations` for a listing outside the declared set adds it to the set (and starts a fresh combined search), so a caller that forgets the hook degrades to a per-listing search rather than failing.
- **Filter ID set.** The union of every tracked listing's existing **fetch scope** (`_listing_scopes`, unchanged): the listing, its child sub-unit IDs and, for an imported child, its parent unit type. A tracked multi-unit parent therefore contributes the parent ID and all known sub-unit IDs. Each tracked listing's scope is resolved with one listing-document call per cycle, as today. A scope-resolution failure aborts only that listing (as today), and that listing's own ID still goes into the filter.
- **Request.** `filter[listingId][in]=<comma-joined, sorted IDs>` plus the R3 stay-window filters, `sort=_id`, `limit=100`, `skip` paging (contracts/guesty-v3-search.md).
- **Chunking (FR-016a).** `MAX_LISTING_IDS_PER_SEARCH = 50`. IDs are grouped into **families** (a multi-unit parent with all its sub-units; a tracked child with its parent unit type; a single-unit listing alone) and families are packed, in sorted order, into chunks of at most 50 IDs, so a reservation's first-segment `unitId` and `unitTypeId` fall in the same chunk. Only a family larger than 50 IDs is split across chunks. A 24-character ObjectId plus an encoded comma is 27 characters, so a full chunk's query is about 1.4 KB plus about 150 bytes of other parameters: well under the 2 KB a conservative proxy accepts.
- **Guard per chunk.** Every chunk runs the full R4 guard independently (its own `_id` order, duplicate set and 500-page cap). Any chunk failure fails the whole combined result.
- **De-duplication across chunks.** Results are merged by `reservationId`. A reservation returned by two chunks (possible only when an oversized family is split) is kept once when both copies have the same R9 `change_key`; if the copies differ, the reservation changed between requests, and the combined result fails as an FR-011 shift.
- **Caching.** The validated combined result is cached on the provider instance for the rest of the cycle (the scheduler builds one provider per cycle and closes it afterwards; see `scheduler.py`). A failure is cached **as a failure**: every listing's `get_reservations` raises the same error, so all listings keep last-good and no pages are re-spent. The next cycle retries.
- **Attribution.** Each listing claims the results with any segment in its ownership scope, through every segment's `listingId`/`parentListingId` (R6), exactly as before.

### What the search finds, and the residual gap

| Reservation shape | Found by the search? | Why |
|---|---|---|
| Single segment in a tracked listing | yes | its unit (or unit type) is in the filter |
| Multi-unit, any sub-unit, unassigned or moved within the property | yes | the first segment's `unitTypeId` is the tracked parent (or the tracked child's parent) |
| First segment in another **tracked** listing, later segment here | yes | the first segment is in the filter; attribution reads every segment |
| First segment in an **untracked** property, stored before (re-arranged by Guesty) | no, but kept | it becomes an FR-011a candidate, and R5 re-attributes it from by-IDs |
| First segment in an **untracked** property, never stored | **no** | the accepted residual gap (FR-016); the previous release had the same gap |

The residual gap is made measurable in the field rather than invisible. The per-reservation DEBUG line (R15) logs every segment's `listingId`, so a user's logs show whether cross-property moves happen on their account at all (a found reservation whose segments leave the tracked set is the mirror image of the gap). R5 logs each re-arranged reservation it re-attributes, and each interval it could not create, at INFO. A reported missing booking can then be checked against Guesty's segment list.

### Rationale

- Cost scales with the tracked listings' in-window bookings: `Σ_chunks ceil(R_chunk_in_window / 100)` pages per cycle, independent of account size (SC-007).
- Each tracked reservation is fetched once per cycle, however many listings it touches (for the reference account: one chunk, 2 pages).
- The filter is honoured server-side (V3 rejects what it does not support with HTTP 400), and client-side ownership attribution remains the backstop (FR-020).

### Alternatives considered

1. **One account-wide stay-window sweep (the previous design).**
   - It closes the residual gap.
   - Its cost grows with account size: an installation tracking 3 listings of a 300-listing portfolio would page the whole account every cycle. The owner rejected it (2026-11-12).
2. **One search per listing.**
   - It misses cross-listing moves between two tracked listings, which the combined search catches, and it costs a separate page sequence per listing. Rejected.
3. **Probing stored bookings by ID to find movers.**
   - It can only find reservations already stored, never new ones. It is used only as R5's re-attribution for stored bookings that vanish.

## R3: Stay-window filter construction (FR-013–FR-015)

### Decision

- Send `filter[checkOut][gte]=<D_start>` and `filter[checkIn][lte]=<D_end>`, both as `YYYY-MM-DD`, where:
  - `D_start` = UTC date of `now − (OLD_BOOKING_RETENTION_DAYS + 1)` days, minus a 1-day timezone pad. That is now − 92 days.
  - `D_end` = UTC date of `now + DEFAULT_LOOKAHEAD_DAYS (365)` days, plus a 1-day timezone pad.
- The client-side `_overlaps_window` check (instant-based) stays the authoritative trim, as today. The server filter only has to be a **superset**.

### Rationale

- **In-progress long stays (FR-013).** Any stay in progress at sync time has a check-out on or after today, which is on or after `D_start`. It is therefore returned whatever its check-in date. A check-in-only filter would drop it; this filter cannot.
- **Timezones.** The filter compares listing-localized dates, but one combined search covers listings in many timezones. Real offsets are within ±14 h, so a 1-day pad on each side keeps the server result a superset of the instant-based window.
- **Lookback.** Retention is the binding lookback. `SyncService._filter_to_retention_window` discards any reservation that checked out more than `OLD_BOOKING_RETENTION_DAYS` (90) ago before anything else sees it, and the prune loop skips stored rows past retention. A lookback of 365 days would only fetch rows the sync throws away. The V3 lookback therefore aligns to retention plus padding. A unit test pins `V3_LOOKBACK_DAYS ≥ OLD_BOOKING_RETENTION_DAYS + 1` so a future retention change cannot silently narrow the fetch.
- **FR-014.** Export look-back, the in-progress guard and the look-ahead (365 days, unchanged) are all inside the window.
- **FR-015.** A test proves that out-of-window fixtures are not requested. It asserts the exact query string and that the fake server's filtered result is smaller than its unfiltered set. Pre-release validation logs the narrowed count (R15).

### Alternatives considered

- **Lookback of 365 days, as legacy did client-side.** Rejected: it adds ~3× the pages for rows the sync discards.
- **Listing-local dates computed per listing.** Not applicable: there is one combined search, and the pad makes the result timezone-agnostic.

---

## R4: Completeness guard for `hasMore` paging (FR-008–FR-011)

### Decision

`sort=_id` (ascending), `limit=100`, `skip = page × 100`, with a page cap of `MAX_RESERVATION_PAGES = 500` (unchanged). The snapshot is accepted only if **every** check below passes. Otherwise the fetch raises `PMSProviderError` before anything is mapped. Nothing is published or retired, and the error is recorded per listing (FR-012).

1. The response body is an object with a `results` list and a `pagination` object containing a boolean `hasMore`. If either is missing or malformed, abort (FR-010).
2. `pagination.skip` (when present) equals the requested `skip`, and `len(results) ≤ limit`. Otherwise abort.
3. If `hasMore` is true and the page is empty, abort (FR-010).
4. If `hasMore` is true and the page is short (fewer than `limit` results), abort. Guesty would only return a short page mid-stream if the set shifted.
5. Every item has a non-empty string `reservationId`. Otherwise abort, because there is no usable identity.
6. If any `reservationId` was already seen on an earlier page, abort (FR-011, a shift into an already-read position).
7. Every `reservationId` must be strictly greater than the previous one across the whole chunk (R2), because the results are sorted by `_id` ascending. Otherwise abort: the order is not deterministic, or the set shifted.
8. If `hasMore` is still true after page 500, abort (FR-010).
9. If any page request fails after the existing 429 and backoff retries, abort (FR-010, FR-033).

### Rationale

- Ascending `_id` order (Mongo ObjectIds embed creation time) means a **newly created** reservation sorts after everything already read. Creation, the commonest mid-search event, therefore cannot shift earlier pages.
- Moving into the filtered set behind the cursor (for example, re-dated into the window) shifts later records forward. That shows up as a duplicate, check 6.
- A record **leaving** the filtered set behind the cursor shifts later records back by one, and one record is silently never read. No paging check can detect this. FR-011a/R5 covers it by never retiring on absence alone.

### Alternatives considered

- **Sort by `checkIn`.** Rejected: it is not unique, so the order between equal values is not guaranteed, and created or edited stays land mid-stream.
- **Re-reading page 1 at the end as a canary.** Rejected: one more call per cycle, and it adds nothing to the duplicate and monotonicity checks.

---

## R5: Confirmed-absence retirement and re-attribution (FR-011a) via by-IDs lookup

### Decision

- After a listing's snapshot is accepted, the sync collects that listing's **retirement candidates**. These are stored bookings that pass all of the prune loop's existing checks: not already cancelled, inside retention, exact ID not seen, base reservation ID not seen in the snapshot, and stay not in progress.
- It calls a new provider method, `confirm_absent(listing_pms_id, reservation_ids) -> dict[str, AbsenceResult]`. The method batches `GET /v1/reservations-v3?reservationIds=…` (array, form-exploded; at most 10 IDs per call). Each call carries **at most 9 candidates plus 1 control ID**. The control is a reservation present in the current accepted combined result (R2), preferably one in this listing's scope.
- `AbsenceResult` carries a `verdict` and, for `REATTRIBUTED`, the reservation rebuilt from the by-IDs payload (below). The verdict for each candidate is one of:

  | By-IDs result for the candidate | Verdict | Prune action |
  |---|---|---|
  | Returned, status `canceled`/`cancelled` | `CONFIRMED_ABSENT` | retire every stored row of the reservation in this listing (as today) |
  | Returned, status excluded (`inquiry`/`reserved`) | `CONFIRMED_ABSENT` | retire (excluded statuses are dropped from snapshots today, so they are pruned today) |
  | Returned, no segment in this listing's ownership scope | `CONFIRMED_ABSENT` | retire (moved away; same as today's room-change retirement) |
  | Returned, overall stay outside the padded R3 window | `CONFIRMED_ABSENT` | retire |
  | Returned, status `alteration` | `UNCONFIRMED` | keep unchanged: the FR-023 hold |
  | Returned, any other status, a segment in this listing's ownership scope, in window, and its **first segment's `unitId` and `unitTypeId` both outside the searched filter ID set** | `REATTRIBUTED` | re-attribute (below); **no abort** |
  | Returned, as in the previous row, but the first segment's `unitId` or `unitTypeId` **is** in the searched filter ID set | `INCONSISTENT` | keep; abort **this listing's** sync (FR-011): the search should have returned it, so either the set shifted mid-search or Guesty's listing filter does not behave as documented |
  | Not returned, control returned | `CONFIRMED_ABSENT` | retire (deleted) |
  | Not returned, control **not** returned, or no control was available | `UNCONFIRMED` | keep; log ERROR (omission proves nothing) |
  | Request failed, rate-limited after retries, or malformed | `UNCONFIRMED` for the whole batch | keep; log WARNING (FR-011a) |
  | Response contains any `_id` that was **not requested**, or the same `_id` twice | `UNCONFIRMED` for the whole batch | keep; log ERROR. The parameter was ignored or mis-serialised, so the response proves nothing |

- **Whole-response validation first.** The response is validated as a whole before any verdict is assigned. It must be a list, every item must have an `_id`, every `_id` must be one of the requested IDs, and there must be no duplicates. Only then are per-candidate verdicts derived. This stops an ignored `reservationIds` parameter, which could return unrelated account results that happen to include the control, from turning an omission into a retirement.
- **No control available.** When the combined result is empty, the call is still made with candidates only. A *returned* candidate's verdict stands (the whole-response check already proves every returned item was requested); an *omitted* candidate is `UNCONFIRMED`. A cancelled reservation can therefore still retire on an account with no other in-window reservation.
- **Why `REATTRIBUTED` must not abort.** Guesty can re-arrange a stored reservation so its first segment is in an untracked property while a later segment stays in a tracked room. The combined search (R2) no longer returns it, every cycle. Treating that as a shift would abort the listing on every sync: a permanent outage. Instead the lookup's own segments are the evidence, and the reservation stays on the calendar it still occupies.
- **Why `INCONSISTENT` still aborts.** When the first segment *is* covered by the filter, the search should have returned the reservation, so the miss is a genuine shift (FR-011) or a filter that does not behave as documented. The listing aborts with last-good kept, and the error names the reservation ID and says: if it persists, switch `guesty_reservations_api` to `legacy` and report it. The abort is per listing; there is no cycle-wide poisoning, because every other listing's retirements are each confirmed individually. A transient shift clears on the next cycle; a persistent one is a contract break that FR-042 validation is designed to catch before the stable release.
- With no candidates, no request is made. In steady state that is **0 calls**. When `k` stored bookings are absent from the search, it costs `ceil(k/9)` calls per cycle; a reservation that stays `REATTRIBUTED` keeps costing one slot per cycle until it leaves the window or the search finds it again.
- Candidates are grouped by base reservation ID, so a multi-room reservation costs one slot.
- The in-progress guard runs **before** confirmation, as today. An in-progress stay is never even a candidate (FR-012).

### Re-attribution from the by-IDs payload

The by-IDs item differs from a search item: `_id` instead of `reservationId`; per-segment `unitId` and `unitTypeId` instead of `listingId` and `parentListingId`; and only listing-localized `YYYY-MM-DD` dates. A pure adapter, `normalise_by_ids_item`, turns it into the search shape before attribution, so the **same** R6 attribution and identity code runs for both endpoints:

| Search field | Built from the by-IDs item |
|---|---|
| `reservationId` | `_id` |
| `status` | `status` (R7 mapping, including `completed` → `checked_out`) |
| `stay[].listingId` | `unitId ?? unitTypeId` (Guesty's own definition of the search field) |
| `stay[].parentListingId` | `unitTypeId` when `unitId` is present and differs from it; otherwise absent |
| `stay[].checkInDateLocalized`, `checkOutDateLocalized` | the same fields |
| `stay[].checkIn`, `checkOut` (UTC instants) | not available; see "Dates" below |

- **Identities.** Intervals and `#n` numbering come from the normalised `stay[]` through R6 steps 2–5 unchanged, so a booking's `pms_booking_id`, and therefore its event UID and `Booking ID:` line, are byte-identical whichever endpoint supplied the data. A contract test feeds the same reservation through both shapes and asserts identical IDs.
- **Which rows survive.** For the candidate's stored rows in this listing: a row whose `pms_booking_id` is one of the re-attributed intervals is kept and marked seen; a row whose ID is not among them is retired by the normal room-change path. An interval with no stored row is **not created** from by-IDs data (it has no UTC instants and no description data); it is logged at INFO with the reservation ID and room, as part of the FR-016 residual gap.
- **Dates.** For each kept row, the stored `check_in`/`check_out` instants are converted to the listing's timezone (from the listing document `_listing_scopes` already reads). If the local dates equal the interval's localized dates, the stored instants are kept byte-for-byte. Otherwise the instant is re-anchored: the new local date combined with the stored instant's local time of day, in the listing's timezone, converted to UTC. The published check-in and check-out times therefore stay the same while the dates follow Guesty.
- **Status.** The R7 mapped status is applied, so an exported status keeps exporting and a recognised non-export status is withheld as usual. An unclassified status is withheld, warned and listed in the panel, as for a search result; by-IDs results are not counted by the health guard, which is a property of the search snapshot.
- **Description and enrichment.** `custom_data`, `enrichment_data` and `guest_name` are left as stored: last-good values, never blanked (FR-032), and no guest or custom-field call is made for the reservation. Fresh enrichment resumes when the search returns the reservation again. Whether the by-IDs payload carries `guestId` and inline `customFields` in the search shape is recorded for live validation (R15), as a possible later refinement.

### Rationale

The by-IDs reference documents only 400/500 responses and a bare array of matches. It does not say what happens to an unknown ID, so presumably it is omitted. The control ID turns "omitted" from an assumption into a checked fact on every call: an endpoint that returned `[]` for everything (a mis-serialised array, an ignored parameter, the V1-style failure behind #119) can never retire a booking. Splitting "returned but missed by the search" into `REATTRIBUTED` and `INCONSISTENT` keeps the shift detection where the search should have seen the reservation, without turning a legitimate re-arrangement into a permanent outage.

### Alternatives considered

- **Re-running the search and retiring only what is absent twice.** Rejected: it doubles the page cost and still has no positive evidence.
- **Retiring on omission without a control.** Rejected: it fails open if the endpoint misbehaves.
- **Aborting on every returned live candidate (the previous `PRESENT` verdict, with cycle poisoning).** Rejected by the owner (2026-11-12): a re-arranged reservation would abort every sync forever.
- **Adding the re-arranged reservation's first-segment listing to the filter.** Rejected: it would search an untracked property's bookings every cycle, and the result would still need the same attribution.

---

## R6: Ownership and multi-segment attribution (FR-016–FR-020, FR-007, FR-018)

### Findings (live code)

- `_listing_scopes` reads `GET /v1/listings/{id}` once per `get_reservations` call, using its `timezone`, `childListings` and `mtl.p`.
  - **Ownership scope** = the listing plus its child sub-unit IDs.
  - **Fetch scope** additionally includes the parent unit type.
- `_resolve_room_id` (legacy) returns the first `stay[].unitId`. Failing that it returns `mtl._id` if assigned, and otherwise the top-level `listingId` (= `unitId ?? unitTypeId`). The DTO carries `room_ids=(room,)`.
- `SyncService._create_bookings_for_reservation` then stores `{rid}::{room}` for every Guesty reservation.

### Decision

Each reservation goes through these steps per listing:

1. **Segments.** Read `stay[]`. A missing, empty or non-list value, or a segment without `listingId`, aborts the combined search result (FR-020; V3 documents `stay[]` and `listingId` as always present).
2. **Owned segments.** A segment is owned if its `listingId` is in the ownership scope. A segment whose `listingId` is the parent unit type, with no sub-unit assigned, is owned by the parent listing only, as today (FR-019). All other segments contribute nothing (FR-017).
3. **No owned segment.** The reservation is not this listing's. This covers sibling-only references, as today (FR-020).
4. **Intervals.** Merge chronologically **adjacent** owned segments that have the same `listingId` into one interval. Adjacent means `prev.checkOut == next.checkIn`. Non-adjacent returns to the same room (A → B → A) stay separate intervals (FR-018).
5. **Identity.** Number the intervals per room in chronological order.
   - The first interval in room R is `{rid}::{R}`, which is identical to legacy for every single-segment reservation (FR-005, FR-007).
   - The *n*-th interval (n ≥ 2) in room R is `{rid}::{R}#{n}`.
6. **Room ID equivalence.** The V3 segment `listingId` is `unitId ?? unitTypeId`. For a single segment this equals legacy `_resolve_room_id`, which takes the first `unitId`, else falls back to `listingId` = `unitId ?? unitTypeId`. A fixture-based identity test pins this against stored legacy rows (SC-002).
7. **Dates.** Each interval uses its segments' UTC `checkIn`/`checkOut` instants (first segment's in, last segment's out). For a single segment these equal the top-level `checkIn`/`checkOut` that legacy used.

### Why `#n` is safe

- `rsplit("::", 1)[0]` still yields `rid`. This affects `_extract_base_reservation_id`, `_stored_enrichment_state` and the calendar's `Booking ID:` line, so every interval's `Booking ID:` line carries the base reservation ID (FR-018).
- The UID is `sha256(f"{listing_id}-{pms_booking_id}")[:16]`, so the UID is distinct per interval and stable while the interval ordering is stable.
- `pms_booking_id` is `String(255)`, which has ample room.
- The room for the row is resolved from the interval's room ID, **not** parsed from the booking ID.

### Same-property moves vs cross-property moves

- A move within a tracked multi-unit property is always found: the first segment's unit type is the tracked parent, which is in the filter (R2).
- A move whose first segment is in another **tracked** listing is found, because that listing is in the filter too.
- A move whose first segment is in an **untracked** property is found only if it was stored before (R5 re-attribution); otherwise it is the FR-016 residual gap.
- Attribution is purely client-side, against stay segments, whichever endpoint supplied them.

### Quantified

- Single-unit listing: 1 interval, identical identity.
- Multi-unit move within one property (A → B): 2 bookings, `{rid}::A` and `{rid}::B`, each with its own dates.
- A → B → A: 3 bookings, `{rid}::A`, `{rid}::B` and `{rid}::A#2`.
- Unassigned multi-unit: `{rid}::{parent}`, which is listing-calendar only, as today.

---

## R7: Status mapping (FR-022–FR-026)

V3 status enum (12 values): `confirmed, reserved, awaiting_payment, inquiry, canceled, closed, declined, expired, alteration, completed, checked_in, checked_out`.

| V3 status | Stored / DTO status | Export | Retire | Health guard | Log |
|---|---|---|---|---|---|
| confirmed, checked_in, checked_out | same | yes | no | classified | — |
| **completed** | `checked_out` | yes, ages out normally | no | classified | — |
| canceled / cancelled | `cancelled` | no | **yes** | classified | — |
| expired, closed, declined, awaiting_payment | verbatim | withheld | no | classified | DEBUG |
| inquiry, reserved | dropped from snapshot (`None`) | — | as today | classified | — |
| **alteration** | `alteration` (DTO only, never stored) | see below | **never** | classified | DEBUG |
| anything else | verbatim | withheld; withheld panel | no | **unclassified** | WARNING |
| missing `status` key | — | abort fetch (FR-026) | — | — | ERROR |

The comparison is case-insensitive, and both spellings of cancelled are accepted (FR-024). The health guard keeps the same thresholds: `_UNCLASSIFIED_MIN_SAMPLE = 4` and ratio `> 0.5` over in-window mapped reservations (FR-025).

### `alteration` handling (in `SyncService`, since it needs stored state)

A new constant `HOLD_STATUSES = frozenset({"alteration"})` lives in `providers/base.py`. When the DTO status is in `HOLD_STATUSES`, the sync does the following:

- **Stored bookings exist for the base ID, whatever their status:**
  - Mark every stored row for the reservation as *seen*, so the prune loop and FR-011a never consider them.
  - Do **not** call the normal upsert. Status, dates, room and `room_id` are untouched, and no new interval rows are created.
  - Apply the **enrichment refresh only**: custom data (reservation-supplied keys and inline custom fields), guest contact and guest name, with full FR-028–FR-032 semantics. The stored export shape is kept, while door-code values stay fresh.
  - Log at DEBUG.
- **No stored booking:**
  - Skip. Do not store or export the reservation, and do not add it to `seen` (there is nothing to protect).
  - Log at DEBUG with the reservation ID only.
  - It is therefore never in the withheld panel (that panel lists stored rows).
- **By-IDs returns `alteration` for a retirement candidate:** the verdict is `UNCONFIRMED`, so the row is kept (R5).

### Alternatives considered

- **Storing `alteration` as a status.** Rejected: it would withhold an exported live stay, which violates FR-023.

---

## R8: Inline custom fields (FR-028, FR-035)

### Findings

- Search results carry `customFields: [{_id, fieldId, value}]`, but **only when at least one value is set**. Otherwise the key is absent.
- Today's per-reservation endpoint returns `{fieldId: value}`, which the sync stores under the same keys, labelled through account definitions (#127/#130/#132).

### Decision

The V3 mapper builds `InlineCustomFields(state, values)`:

| `customFields` in item | state | values |
|---|---|---|
| list of well-formed `{fieldId, value}` | `PRESENT` | `{fieldId: value}`, the same keys as today (FR-035) |
| key absent | `ABSENT` | `{}` |
| present but not a list, or any element malformed | `MALFORMED` | `{}` |

`SyncService._custom_field_enrichment` uses it as follows, with no per-reservation call in the common case (US5-2):

- **`PRESENT`:** use the values. No call. Success.
- **`ABSENT`:**
  - If stored `enrichment_data` is **not NULL** and holds **no** custom-field-owned values (for example `{}`), treat it as a successful empty read. No call.
  - If stored `enrichment_data` is **NULL** (unknown provenance: a row written before the enrichment column, which may still publish a door code from `custom_data`), or it holds **non-empty** custom-field-owned values, make **one confirmation call** to the existing `GET /v1/reservations-v3/{id}/custom-fields`.
    - If the call succeeds, use its values.
    - If it fails and `enrichment_data` is not NULL, keep the last-good values (FR-032).
    - If it fails and `enrichment_data` is NULL, use `_bootstrap_custom_fields` recovery, exactly as today.
    - On any failure, `_change_key` is not persisted, so the next sync confirms again.
  - This stops a dropped inline block, or a first V3 sync over pre-enrichment rows, from blanking a published door code. It costs one call only when values disappear or provenance is unknown.
- **`MALFORMED`:** keep the last-good values (existing fallback path, including `_bootstrap_custom_fields`). Log at WARNING. No abort (FR-028).

`has_separate_custom_fields` stays `True` for Guesty, so the existing fallback machinery is reused unchanged.

---

## R9: Guest lookup and enrichment reuse without `lastUpdatedAt` (FR-029–FR-032)

### Findings

- V3 search has `guestId` but no embedded guest and no `lastUpdatedAt`.
- Today's reuse (`_can_reuse_enrichment`) has two requirements, and both are lost in V3 mode:
  1. The provider's `updated_at` must equal the stored `pms_updated_at`.
  2. A guest name must be present on the DTO.
- Without a replacement, V3 mode would look up every distinct guest every sync: about 100 calls per cycle for the reference account, which breaks FR-027.

### Decision

1. **Change key.** The V3 mapper computes `change_key`: SHA-256 hex over the canonical JSON (sorted keys, no whitespace) of these item fields:
   - `reservationId`, `status`, `guestId`, `confirmationCode`, `source`
   - top-level `checkIn` and `checkOut`
   - `stay[]`, as a list of `{listingId, parentListingId, checkIn, checkOut, guestsCount}`
   - `customFields`, sorted by `fieldId`
   - `notes`

   Any change to the reservation, its guest assignment or its custom fields changes the key. That is at least as strict as `lastUpdatedAt`, and it contains `guestId` (FR-029 minimum).
2. **Where it lives.** `PMSReservation.change_key: str | None` is a new optional field (default `None`; Cloudbeds and legacy leave it `None`). It is persisted as a **bookkeeping key `_change_key` inside `Booking.enrichment_data`**. This follows the existing `_guest_source_id` precedent, so **no schema migration** is needed (R13).
3. **Guest name provenance.** On a successful guest lookup, guest-owned enrichment also records `_guest_name`, alongside `_guest_source_id`.
4. **Bookkeeping keys.** `BOOKKEEPING_KEYS = {_guest_source_id, _guest_name, _change_key}` are dropped by `_exportable`, so they never reach custom data or descriptions. `_guest_name` and `_guest_source_id` are guest-owned. `_change_key` is neither guest-owned nor custom-field-owned: it is written only on a fully successful enrichment and is never carried forward by any fallback.
5. **Reuse rule (V3).** `_can_reuse_enrichment` gains a change-key branch. It returns True only when **all** of these hold:
   - `cached.data is not None` (NULL is still distinct from `{}`)
   - `reservation.change_key` is not None and equals `cached.data["_change_key"]`
   - either the reservation has no guest, or `cached.data["_guest_source_id"] == reservation.guest_id` **and** `cached.data["_guest_name"]` is non-empty
   - the reservation is **not** in the freshness window: in progress, checked out ≤ 7 days ago, or starting ≤ 30 days ahead (unchanged constants)
   - **no stored booking** for the reservation is in the freshness window, judged on its stored dates. This matters during an `alteration` hold, where the published (frozen) dates can be near while the proposed V3 dates are far away. The published stay's door code must keep refreshing (FR-023, FR-030)

   On reuse, the guest name comes from `_guest_name` and the guest contact from the stored guest-owned keys. Custom fields still come from the inline block when it is `PRESENT`, which is free and fresh.
6. **Failure rules (unchanged #129 invariants).**
   - On a failed lookup, `_change_key` is not persisted, so the next sync retries.
   - On guest failure, fall back only if `_guest_source_id` matches the current guest. Otherwise publish no phone, email or last-4, so a reassigned guest never sees the previous guest's details (FR-031).
   - The name falls back to `_guest_name` only on the same source match, else to the existing placeholder. A stored name is never blanked (FR-032).
   - **Upgrade bootstrap for the name.** Rows written by the previous release have `_guest_source_id` but no `_guest_name`; their `Booking.guest_name` came from the legacy embedded guest. When the first V3 lookup fails and `_guest_source_id` matches, the stored `Booking.guest_name` is used, so the published name is kept (FR-032). `_StoredEnrichment` gains the stored guest name for this. If the source does not match, nothing is reused.
   - On custom-field failure, use the last-good values (R8).
   - `enrichment_data` NULL vs `{}` keeps its meaning.
7. **Guest resource.** `get_guest` in V3 mode calls `GET /v1/guests-crud/{id}?fields=fullName phone email`. A 404 returns `None`, as today. Lookups are deduplicated per distinct guest per sync, as today (`_resolve_guests`).

### Alternatives considered

- **A new `pms_change_key` column.** Rejected: it needs an Alembic migration, which loses the "add-on downgrade still works" safety net.
- **Key on `guestId` alone.** Rejected: it would reuse across date, status and custom-field changes that the content key catches. The content key strictly contains it.
- **Using by-IDs `alteredAt`.** Rejected: search has no such field, and fetching it costs a call per reservation.

---

## R10: Description-field continuity (FR-034–FR-038)

| Key | V3 source | Rule |
|---|---|---|
| `source` | `source` | unchanged |
| `confirmationCode` | `confirmationCode` | unchanged |
| `guestsCount` | `stay[].guestsCount` | the **maximum** over the segments merged into this booking's interval. This is non-additive, because adjacent same-room segments describe the same guests over time; it equals the common value when the counts agree. A single segment gives that segment's count, equal to today's reservation total. Integral numbers render as int strings (`3`, not `3.0`). Missing → key omitted, as today when absent |
| `notes` | `notes.other`, `notes.guest`, `notes.specialRequests` | non-empty, stripped strings joined with `"; "` in that order. `cleaning` and `keyCode` are **excluded**. None non-empty → key omitted |
| `nightsCount` | derived | `(checkOutDateLocalized − checkInDateLocalized).days` at **reservation** level (first segment's in, last segment's out), the same meaning as legacy. Unparsable → omitted |
| `money_totalPaid`, `money_balanceDue`, `money_currency` | — | **retired** in V3 mode (not supplied; not re-sourced) |
| custom fields | inline `customFields` | `{fieldId: value}`, unchanged keys and labels |

### Retired-field notice (FR-037/FR-038), with no migration

- A constant `RETIRED_DESCRIPTION_FIELDS: Mapping[str, str]` (key → reason) lives in `src/providers/guesty/description_fields.py`. It is active only for Guesty in V3 mode.
- **Log notice.** It is emitted once per process at the first V3 sync. For each enabled `custom_fields` row whose `field_name` is retired, it logs one WARNING per field naming the field and the affected listing IDs. Add-on restarts happen on upgrade, so "at upgrade/first sync" holds.
- **Admin UI.** `GET /api/listings/{id}/custom-fields` and `/available-custom-fields` gain `retired: bool` and `retired_reason: str | null` (contracts/admin-custom-fields.md). `admin.js` shows a "Retired" badge with the reason as tooltip text.
- **Configuration is kept.** The user's `custom_fields` rows (enabled, label, order) are never deleted (FR-038).
- **Rendering.** A retired field renders nothing, because the value is absent. It is flagged rather than silent, which satisfies SC-009.
- Legacy mode supplies the money fields as today, so nothing is flagged retired in legacy mode.

---

## R11: Cutover toggle (FR-041)

### Decision

| Layer | Change |
|---|---|
| `config.yaml` | `options: guesty_reservations_api: v3`, `schema: guesty_reservations_api: list(v3\|legacy)?` |
| `translations/en.yaml` (new) | name and description for both options, including the rollback and removal note |
| s6 `run` script | `bashio::config.has_value` gives `export GUESTY_RESERVATIONS_API`, defaulting to `v3`, and logs the active mode at INFO |
| `src/config.py` | `guesty_reservations_api: Literal["v3", "legacy"] = "v3"`. The value is normalised with strip + lower; an invalid value raises at startup, like `pms_type` |
| `src/providers/factory.py` | the Guesty branch passes `reservations_api=get_settings().guesty_reservations_api` |
| `GuestyProvider.__init__` | selects a `ReservationSource` strategy: `V3ReservationSource` or `LegacyReservationSource` |

### Legacy isolation

- The current legacy fetch and mapping code moves, **verbatim**, into `src/providers/guesty/legacy_reservations.py`. That covers `get_reservations`, the count cross-check, `_map_reservation`, `_resolve_room_id`, `_extract_custom_data` and the legacy `get_guest` path.
- The move is a pure refactor commit, and the existing tests pass unchanged against it.
- Removing it after 2027-03-01 then means deleting:
  - that module
  - its tests
  - the `legacy` option value
  - the strategy branch

### Safety in both directions

- Switching needs no DB change, and all guards apply in both modes.
- Single-segment identities are identical, because both modes produce `{rid}::{room}`.
- Multi-segment `#n` rows are retired by the existing room-change prune path when legacy is selected. This is documented (spec FR-041).
- In legacy mode, a `PMSProviderError` from `/v1/reservations` with HTTP 404/410 after 2027-03-01 is re-raised with the message "Guesty has retired the legacy reservations API; set `guesty_reservations_api` to `v3`".

---

## R12: Request budget (FR-027, SC-006)

Reference account: 3 tracked listings, ~101 retained reservations, about 10–15 of them inside the freshness window at any time, and a 5-minute sync interval.

| Per cycle | Previous release (legacy) | V3 mode (steady state) |
|---|---|---|
| Listing docs (`_listing_scopes`) | 3 | 3 |
| Reservation pages | 3–6 (all history per listing) | **2** (one combined search: 1 chunk, ~110 in-window tracked reservations) |
| Guest lookups | ~10–15 (freshness window + changed) | ~10–15 (distinct guests, freshness window + changed) |
| Custom-field calls | ~10–15 (same set) | **0** (inline; rare R8 confirmation) |
| FR-011a by-IDs | — | **0** (`ceil(k/9)` only when `k` stored bookings are absent from the search, including re-attributed ones) |
| Definitions / token | ~0 (cached) | ~0 (cached) |
| **Total** | **~26–39** (observed ~20–40) | **~15–20** |

- **Scaling.** Reservation pages are `Σ_chunks ceil(R_chunk_in_window / 100)`: proportional to the **tracked** listings' in-window bookings, with one extra chunk per 50 filter IDs. Listings the installation does not track cost nothing (SC-007). For example, tracking 3 listings of a 300-listing portfolio still costs one chunk and a page count set by those 3 listings' bookings.
- **Worst-case change cycle:** a few extra by-IDs or custom-field confirmations, still below today's count. A reservation held in `REATTRIBUTED` costs one by-IDs slot per cycle (a ninth of a call) and no guest or custom-field call.
- **Hourly:** about 180–240 requests per hour, against the 5,000/h limit.
- **Per minute:** a cycle completes well under 120.

SC-006 is enforced by a test that runs the reference fixture through both modes on a counting fake transport and asserts `v3_calls ≤ legacy_calls`. A second steady-state cycle asserts no guest or custom-field calls for reservations outside the freshness window.

---

## R13: Database migration

**None.** The design has three storage touch points, and none of them changes the schema:

- Change keys and guest-name provenance live in the existing `Booking.enrichment_data` JSON column.
- `#n` interval identities fit `pms_booking_id String(255)`.
- Retired-field flags are computed at read time.

Alembic head stays at the `f5a6b7c8d9e0` lineage. This also keeps a plain add-on downgrade possible during the transition, alongside the toggle.

---

## R14: Identity verification (FR-006)

### Decision

- Before any upsert or prune for a listing, the sync maps `confirmationCode` to base reservation ID. The map covers the listing's stored **active** bookings (not `cancelled`, within retention) that have a `confirmationCode` in `custom_data`.
- If a snapshot reservation's confirmation code maps to a **different** stored base ID, and that stored ID is absent from the snapshot, the listing's sync aborts with `SyncServiceError`. The message names both IDs, says the identifiers changed between APIs, and tells the user to switch `guesty_reservations_api` to `legacy` and report it.
- Nothing is created or retired. The check costs no requests.
- The first V3 sync logs at INFO how many stored reservation IDs were matched (FR-042 item 1).

### Rationale

- The confirmation code is stable across Guesty's APIs.
- Re-keying would show up as "same code, new ID, old ID gone". Acting on it would retire and re-create every booking under new UIDs.

### Alternatives considered

- **Mass-turnover heuristics on ID sets.** Rejected: they cannot tell a genuine full turnover on a small listing apart from re-keying.

---

## R15: Live-verification items (FR-042) and diagnostics (FR-043)

### What the pre-release must confirm

1. Legacy `_id` equals V3 `reservationId` for existing reservations, and the FR-006 identity check never trips.
2. `filter[checkOut][gte]`/`filter[checkIn][lte]` narrow the result. The search count must be below the legacy all-history count.
3. `stay[].listingId` equals legacy `_resolve_room_id` for single-segment reservations, and the multi-unit `parentListingId` shape.
4. Multi-segment reservations, if any exist: the segment count and listings per segment.
5. `guests-crud` returns `fullName`/`phone`/`email` with the `fields` parameter.
6. The by-IDs array serialisation (`reservationIds=a&reservationIds=b`), and that unknown IDs are omitted rather than rejected.
7. Status values seen, including `alteration` and `completed`.
8. Whether `customFields` is truly absent when it has no values.
9. Whether a page with `hasMore=true` is ever shorter than `limit` (R4 check 4 aborts on it).
10. Whether `sort=_id` ever deviates from strict ascending order (R4 check 7).
11. `filter[listingId][in]` with a comma-joined list is accepted, narrows the result to the tracked listings, and a multi-unit parent ID matches reservations assigned to its sub-units (first segment `unitTypeId`). A 50-ID chunk is accepted without HTTP 414.
12. The by-IDs `stay[]` shape: `unitId`/`unitTypeId` per segment, localized dates only, and whether `guestId` and inline `customFields` are present in the search shape (R5 "Description and enrichment").
13. Whether any `REATTRIBUTED` or `INCONSISTENT` verdict occurs in the wild (R5).

### Diagnostics (DEBUG unless noted; never names, phones, emails or codes)

- Per combined search: number of filter IDs and chunks; per chunk: pages, `hasMore` sequence and items; overall: total items, cross-chunk duplicates, items per status, and a histogram of items per segment count.
- Per listing: owned reservations, intervals created, `#n` intervals, alteration holds and unclassified count.
- Per reservation: ID, status, segment count and segment `listingId`s.
- Per cycle (INFO): Guesty request count by resource, plus reuse hits and misses.
- FR-011a: candidates, verdict counts (`CONFIRMED_ABSENT`, `UNCONFIRMED`, `REATTRIBUTED`, `INCONSISTENT`) and control outcome; at INFO, each re-attributed reservation ID with its segment `listingId`s, and each interval that could not be created from by-IDs data.
