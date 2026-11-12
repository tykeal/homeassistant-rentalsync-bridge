<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->
# Feature Specification: Guesty Reservations V3 Migration

**Feature Branch**: `004-guesty-reservations-v3`
**Created**: 2026-11-11
**Status**: Draft
**Input**: User description: "Migrate the Guesty provider from Guesty's legacy Open API reservation resources to Reservations V3 before the legacy resources are retired on 2027-03-01 (issue #145), without changing anything Rental Control or door-code automations depend on."

## Background

Guesty notified customers on 2026-11-09 that its legacy Open API reservation resources will be removed from service on **2027-03-01** (issue #145). The Guesty provider's reservation listing call is one of those resources; once it is removed, Guesty syncs stop and every Guesty-backed calendar freezes at its last-good state — and with it the Rental Control door-code schedules built from those calendars.

The migration was previously deferred (issue #117) because the replacement looked like a regression on two door-code-affecting points: it reports no total record count (our partial-snapshot guard depends on one) and its listing filter matches only a reservation's first stay segment. Guesty has since dated the retirement and removed the replacement's Beta tag, so both concerns now have to be solved rather than avoided. The server-side stay-window filtering proposed in issue #133 is folded into this feature.

Research inputs (documentation only — there is no Guesty test account and no live calls were made): Guesty's reservation migration guide, the 2026-11-05 changelog, the Reservations V3 search and get-by-IDs references, the guest reference, and the rate-limit guide, all on `open-api-docs.guesty.com`.

> Constraint language below names Guesty's external API contract (resource names, response fields, status values) because those facts constrain the design. They describe *what* Guesty offers, not *how* this project must be built.

## Clarifications

### Session 2026-11-10

- Q: Which cutover strategy should move the provider from the legacy reservation resource to V3 (FR-041)? → A: User toggle. An add-on setting selects legacy or V3 reservation retrieval, defaulting to V3 in both the pre-release and the stable release; the legacy path is removed in a release after 2027-03-01. Rationale: a user can roll back without downgrading the add-on (add-on downgrades do not reverse database migrations). No shadow-comparison mode.
- Q: How is a reservation in the transient `alteration` status treated (FR-023)? → A: The booking keeps its last-known stored status and data until the reservation leaves `alteration`: it is never retired, a previously exported live stay is never withheld, and the in-flight (mid-edit) dates are never published. "Data" here means the booking's export shape — status, dates and room assignment; door-code enrichment (custom-field values, guest contact details) keeps following the normal freshness, reassignment and failure rules of FR-028–FR-032, so a door code changed during the edit is still published and a reassigned guest's details never leak.
- Q: How is a reservation first seen in `alteration`, with no stored prior state, treated (FR-023)? → A: Withheld quietly (debug-level log, like the recognised non-export statuses), never retired, and not stored as a new booking until it leaves `alteration`; it counts as classified for the health guard and is not listed in the withheld-bookings panel as an unknown status. Nothing has been exported for it, so withholding cannot remove a live stay, and publishing its in-flight dates is ruled out. (Decision recorded by the clarify stage, consistent with the constitution's door-code-safety principle; PM to confirm.)
- Q: How is the `completed` status treated (FR-023)? → A: Like `checked_out`: exported, and ages out of the feed and retention normally.
- Q: How are the description fields V3 no longer supplies on the reservation handled (FR-036)? → A: `nightsCount` is re-derived from the stay dates with an identical key and meaning. `money_totalPaid`, `money_balanceDue` and `money_currency` are retired with the non-silent notice of FR-037/FR-038; they are not re-sourced from Guesty's financial resources.
- Q: How do `guestsCount` and `notes`, which V3 delivers in a different shape, keep their existing key and meaning (FR-034)? → A: `guestsCount` is the guest count V3 reports for the stay segment(s) the booking covers (for a single-segment reservation this equals the reservation's guest count, as today), rendered as a whole number. `notes` is the non-empty text of V3's general, guest and special-request notes joined with `"; "` in that order (today's join separator); V3's cleaning and key-code notes are excluded because the legacy key never carried them and a key code is a door secret that must not be newly published.

### Session 2026-11-12

- Q: How are reservations retrieved — one search over the whole account, or a search scoped to the tracked listings (FR-016, FR-027)? → A: A combined search scoped to the tracked listings. Each sync runs one paged V3 search whose listing filter names every tracked listing plus, for multi-unit properties, the parent unit type and its known sub-units (Guesty's listing filter matches either the unit or the unit type of the first segment, so a parent ID catches moves within a multi-unit property). Each result is then attributed to listings and rooms by checking every segment. If the ID list would make a request too long, it is split into several searches, each held to the full completeness guard; any one failing aborts the sync, and results are de-duplicated by reservation ID. Cost scales with the tracked listings' bookings, not with the size of the Guesty account. Accepted residual gap: a reservation whose first segment is in a property this installation does not track, and which was never captured before, is not discovered by the search (the previous release has the same gap); diagnostics log each reservation's segment listing IDs so the gap can be detected in the field.
- Q: What happens to a stored booking that vanishes from the combined search because Guesty re-arranged its reservation so the first segment is now in an untracked property, while a later segment is still in a tracked listing (FR-011a)? → A: The confirmed-absence lookup returns it, and the reservation is re-attributed from that lookup's segments: bookings whose room still holds a segment of the reservation are kept (status and dates follow the lookup), bookings for rooms it no longer touches are retired by the normal rules, and it is retired outright only if cancelled or no segment is tracked any more; the `alteration` hold still applies. Such a lookup result never aborts the sync — only a genuinely inconsistent or invalid lookup response does, so a re-arranged reservation cannot cause a permanent outage. Booking identifiers and event UIDs are identical whichever Guesty resource supplied the data.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Calendars and door codes keep working through Guesty's retirement date (Priority: P1)

A property manager runs rentalsync-bridge against Guesty and feeds its iCal calendars into Home Assistant Rental Control, whose automations set door-lock codes and — through user blueprints — write those codes back to Guesty using the reservation's booking ID. They install the add-on update that moves to Guesty's supported reservation resources. Nothing they rely on changes: the same feeds, the same events, the same event identities, the same `Booking ID:` line. Their automations keep working, and they keep working after 2027-03-01 when the legacy resources disappear.

**Why this priority**: Without this, every Guesty install loses its calendars on 2027-03-01. Changing event identities would be nearly as bad: Rental Control would see every stay as deleted and re-created, and blueprints writing codes back to Guesty would target the wrong reservation.

**Independent Test**: With an installation synced on the current release, upgrade to the new release and sync. Compare the exported feeds before and after: every event present before is still present with a byte-identical event UID, identical dates and an identical `Booking ID:` line; and no request is made to a Guesty resource on the retirement list.

**Acceptance Scenarios**:

1. **Given** a Guesty installation synced on the previous release, **When** the user upgrades and the first sync completes, **Then** every previously exported event keeps the same event UID, the same stored booking identifier (`{reservationId}::{roomId}`) and the same `Booking ID: <reservation id>` description line.
2. **Given** the new release is running, **When** any sync, guest lookup, custom-field lookup or connection test runs, **Then** no request is made to a Guesty resource that Guesty has scheduled for removal.
3. **Given** a reservation created, modified or cancelled in Guesty after the upgrade, **When** the next sync runs, **Then** the feed reflects the change exactly as the previous release would have.
4. **Given** the date is after 2027-03-01, **When** syncs run, **Then** they succeed with no dependency on any retired resource.

---

### User Story 2 - An incomplete or inconsistent fetch never revokes a live guest's door code (Priority: P1)

Rental Control removes a lock code when its event disappears from the feed, so a sync that wrongly concludes a reservation no longer exists locks a guest out. Today the sync protects against this by refusing any reservation snapshot it cannot prove is complete. The new Guesty resource no longer reports how many records exist, and it can be asked to return only the relevant stay window. The property manager needs the same guarantee as today: if the bridge cannot prove it saw every relevant reservation, it keeps the last-good calendar rather than publishing a shorter one.

**Why this priority**: This is the door-code safety boundary. A partial snapshot retires every reservation it missed; issues #113 and #119 were exactly this failure.

**Independent Test**: Feed the provider a sequence of pages that ends early, repeats or shifts records (including a shift caused by an already-fetched record leaving the result set), carries contradictory or missing pagination signals, or exceeds the page cap, and verify that the sync aborts with a clear error and the published calendar is unchanged. Separately, verify that a guest who checked in long before the window's lower bound and is still on site is always included.

**Acceptance Scenarios**:

1. **Given** Guesty indicates more results exist but returns an empty or malformed page, **When** the sync runs, **Then** it aborts, records a sync error, and the previously published calendar is unchanged.
2. **Given** reservations are created or changed in Guesty while a multi-page fetch is in progress such that records shift between pages, **When** the shift is detectable, **Then** the sync aborts that cycle rather than publishing a mixed snapshot, and the next cycle succeeds.
3. **Given** a long-term guest whose check-in is earlier than the sync window's configured lower bound (today 365 days back, so e.g. 400 days ago) and whose check-out is still in the future, **When** the sync runs with a server-side stay-window filter, **Then** that reservation is in the snapshot and its door code stays published.
4. **Given** Guesty rate-limits a reservation page and retries are exhausted, **When** the sync runs, **Then** it aborts before any booking is retired.
5. **Given** a multi-page fetch during which an already-fetched reservation leaves the filtered result set (e.g. moved to another listing, re-dated outside the window, or deleted), shifting a not-yet-fetched reservation into an already-read page position, **When** the fetch ends with Guesty reporting no more results, **Then** the skipped reservation's stored booking is NOT retired.

---

### User Story 3 - Multi-unit and moved stays land in the right room calendar (Priority: P2)

A property manager with a multi-unit Guesty property relies on room-level calendars so each physical door lock gets only its own guests' codes. Under Reservations V3, Guesty can move a guest between units part-way through a stay, describes each unit assignment as a separate stay segment, and only matches its listing filter against the *first* segment. The manager needs every reservation that touches one of their units — on any segment — to appear on that unit's calendar, for the dates the guest is actually assigned to it, and never on a sibling unit's calendar. The one accepted exception is a reservation that starts in a property the installation does not track and was never captured before (FR-016).

**Why this priority**: Mis-attribution either opens the wrong door for a guest or misses a guest entirely. It affects fewer installations than Stories 1 and 2, but where it applies it is equally severe.

**Independent Test**: Using fixtures modelled on Guesty's documented V3 schema, sync a multi-unit property with: a reservation assigned to one unit; a reservation not yet assigned to any unit; a reservation whose first segment is in a different tracked listing and whose second segment is in this property; and a stored reservation re-arranged so its first segment is in an untracked listing. Verify each appears in exactly the calendars it should, for exactly the dates it should.

**Acceptance Scenarios**:

1. **Given** a reservation assigned to sub-unit B for its whole stay, **When** the sync runs, **Then** it appears on sub-unit B's calendar with the same booking identifier and event UID as the previous release produced.
2. **Given** a reservation for a multi-unit property that is not yet assigned to a sub-unit, **When** the sync runs, **Then** it is handled as today (listing calendar only, informational log, no room calendar) — issue #116 behaviour is preserved.
3. **Given** a reservation whose first stay segment is in another listing this installation tracks and whose later segment is in tracked sub-unit B, **When** the sync runs, **Then** the reservation is captured and appears on sub-unit B's calendar for the dates of that later segment only.
4. **Given** a reservation moved mid-stay from sub-unit A to sub-unit B, **When** the sync runs, **Then** A's calendar covers only the A segment's dates, B's calendar covers only the B segment's dates, and neither door is scheduled outside the guest's assignment to it.
5. **Given** a reservation belonging only to a sibling listing, **When** it is returned by Guesty for any reason, **Then** it is not stored against this listing.
6. **Given** a reservation already stored for tracked sub-unit B that Guesty re-arranges so its first segment is in a listing this installation does not track while a later segment stays in B, **When** the sync runs, **Then** B's booking is kept with the same identifier and event UID (dates following Guesty's segment), it is not retired, and the sync does not abort.
7. **Given** a reservation never captured before whose first segment is in a listing this installation does not track and whose later segment is in tracked sub-unit B, **When** the sync runs, **Then** it is not discovered (accepted residual gap, FR-016), and nothing already published is affected.

---

### User Story 4 - Status changes in Guesty are reflected safely (Priority: P2)

Guesty's V3 status vocabulary adds states the bridge has never seen: a transient `alteration` state while a reservation is being modified, `completed`, and system-managed `checked_in`/`checked_out`. A property manager editing a live booking in Guesty (extending a stay, changing a unit) must not see that guest's door code disappear for the duration of the edit, and a status the bridge does not understand must never silently retire a booking.

**Why this priority**: A transient state that withholds or retires a live stay locks a guest out during a routine edit. The existing status policy (unknown → withheld not retired; cancellation retires; health guard) is the product of several incidents and must survive the vocabulary change.

**Independent Test**: Sync fixtures carrying each V3 status, including an exported live stay that transitions to `alteration` and back, and verify the exported calendar and stored status after each sync.

**Acceptance Scenarios**:

1. **Given** an exported confirmed stay, **When** Guesty reports it as `alteration` on the next sync, **Then** the stay is neither retired nor removed from the feed.
2. **Given** a reservation reported as `canceled`, **When** the sync runs, **Then** it is retired exactly as today.
3. **Given** a reservation with a status the bridge has never seen, **When** the sync runs, **Then** it is withheld from export but not retired, it appears in the admin UI's withheld-bookings panel, and a warning is logged.
4. **Given** more than half of a listing's in-window reservations (with at least the existing minimum sample) carry statuses the bridge does not classify, **When** the sync runs, **Then** the sync aborts and the last-good calendar keeps serving.

---

### User Story 5 - The bridge stays within the shared Guesty request budget (Priority: P3)

Guesty limits requests per account, not per application: 15 per second, 120 per minute and 5,000 per hour, shared with every other client using the same Guesty account (including the Home Assistant Guesty integration). The V3 search response no longer embeds the guest's name or a "last updated" timestamp — the key the bridge currently uses to skip re-fetching unchanged reservation details (issues #128/#129) — but it does carry custom-field values inline. A property manager with several listings needs syncs to cost no more requests than they do today, without weakening the guarantees that keep door codes fresh.

**Why this priority**: Exceeding the account budget throttles every Guesty client on the account, and issue #128 showed a multi-listing account can reach it. It is ranked below correctness because a slower, more expensive sync is recoverable; a wrong calendar is not.

**Independent Test**: For a reference account fixture (several listings, ~110 reservations, multi-page history), count Guesty requests per steady-state sync cycle on the previous release and on the new one, and verify the new count is not higher. Separately verify the door-code freshness guarantees with reservations inside and outside the freshness window.

**Acceptance Scenarios**:

1. **Given** a steady-state account with no Guesty changes between syncs, **When** a sync cycle runs, **Then** it makes no more Guesty requests than the previous release made for the same account.
2. **Given** reservations whose custom-field values are delivered inline with the reservation, **When** the sync runs, **Then** no separate per-reservation custom-field request is made for them.
3. **Given** a stay currently in progress, checked out within the last 7 days, or starting within the next 30 days, **When** any sync runs, **Then** its custom-field values and guest phone last-4 reflect Guesty's current data, never a reused copy.
4. **Given** a reservation reassigned in Guesty to a different guest, **When** the new guest's lookup fails, **Then** the previous guest's phone last-4 is not published under the reassigned reservation.
5. **Given** a guest or custom-field lookup fails transiently, **When** the sync completes, **Then** previously published door-code values for that same guest/reservation are kept rather than blanked.

---

### User Story 6 - Existing calendar description settings carry over visibly (Priority: P3)

A property manager has chosen which reservation details appear in each event's description (for example booking source, confirmation code, number of nights, number of guests, notes, or amount paid/balance due/currency) and renamed some of them. Several of those details are delivered differently by Reservations V3, and the financial ones are not delivered by the reservation resource at all. After upgrading, the manager must not discover weeks later that a line quietly vanished from every event; anything that changes must be visible and explained.

**Why this priority**: Description content is user-configured and sometimes consumed by automations, but losing it is not a door-code safety issue. It matters for trust in the upgrade.

**Independent Test**: Configure an installation on the previous release with every Guesty-supplied description field enabled, upgrade, sync, and check each field: either it still renders with equivalent content, or the user is told clearly (admin UI and log) that it is no longer available and why.

**Acceptance Scenarios**:

1. **Given** a description field that V3 still supplies (possibly in a different shape), **When** the sync runs after upgrade, **Then** the field renders with equivalent content under the user's existing label.
2. **Given** a description field V3 no longer supplies, **When** the sync runs after upgrade, **Then** the user is informed in the admin UI and the log, and the field is handled according to FR-036.
3. **Given** custom-field labels resolved from Guesty's account definitions (issues #127/#130/#132), **When** the sync runs after upgrade, **Then** the labels are unchanged.

---

### User Story 7 - Real users can validate the migration before it is forced on them (Priority: P3)

There is no Guesty test account. The maintainer cannot exercise Reservations V3 directly, and several behaviours (identifier continuity, multi-segment filtering, the shape of new statuses in real data) are documented ambiguously or not at all. A Guesty user willing to help needs a way to run the new behaviour against their real account early, with clear diagnostics and a way back if something is wrong, well before 2027-03-01.

**Why this priority**: This is how the P1 stories are proven in practice. It is ranked P3 only because it delivers no end-user value on its own.

**Independent Test**: Publish a pre-release, have a Guesty user run it, and confirm from their diagnostics that each live-verification item listed under Assumptions is answered.

**Acceptance Scenarios**:

1. **Given** a pre-release build, **When** a Guesty user installs it, **Then** they can run the V3 path against their real account following documented instructions, without losing their existing calendars or configuration.
2. **Given** the V3 path produces a result the user believes is wrong, **When** they follow the documented recovery steps, **Then** they can return to a known-good state before the retirement date by switching the reservation-retrieval setting back to legacy, without downgrading the add-on (FR-041).
3. **Given** a sync on the V3 path, **When** debug logging is enabled, **Then** the logs contain enough non-personal detail (status values seen, segment and pagination shapes, request counts) to answer the live-verification questions without exposing guest names, phone numbers or emails.

---

### Edge Cases

- **Pagination ends ambiguously**: Guesty reports more results, then returns an empty page; or omits the pagination block; or never reports the end before the page cap. → Treat as incomplete; abort; keep last-good (FR-010).
- **Result set shifts mid-pagination**: a reservation created, deleted, moved or re-dated between page requests shifts records across page boundaries, so one is seen twice or not at all. Ordering alone cannot prevent the "not at all" case with offset pagination: if an already-fetched record leaves the filtered set, a later record slides into an already-read position and the fetch can still end normally. → A detectable shift (e.g. the same reservation on two pages) aborts the cycle (FR-011), and no stored booking is retired on the strength of its absence alone without positive confirmation (FR-011a).
- **Long in-progress stay**: checked in before the server-side window's lower bound, still on site. → Always included (FR-013).
- **Stay crossing the upper bound**: checks in inside the window, checks out after it. → Included, as today.
- **Mid-stay unit move**: segments in two sub-units of the same property, or one inside and one outside the tracked property. → Each tracked room gets only its own segment's dates; untracked segments are ignored; a reservation is captured if *any* segment is tracked, subject to the residual gap below (FR-016–FR-018).
- **Move into a tracked listing from an untracked property** (first segment untracked, never captured before): Guesty's listing filter matches only the first segment, and the search names only tracked listings. → Not discovered by the search — an accepted residual gap, the same as the previous release's. Debug diagnostics log each reservation's segment listing IDs so the gap can be detected in the field (FR-016, FR-043).
- **Stored reservation re-arranged so its first segment is untracked** (a later segment still in a tracked listing): it vanishes from the search. → The confirmed-absence lookup finds it and it is re-attributed from that lookup's segments: kept for rooms it still touches, retired for rooms it no longer touches, retired outright only if cancelled or no segment is tracked; the sync does not abort (FR-011a). A tracked room it now touches for the first time is not published (FR-016 residual gap) and is logged; if the lookup's dates cannot reproduce the booking identifiers unambiguously, nothing is retired for that cycle.
- **Many tracked listings**: the listing filter would make one request too long. → The search is split into several searches, each held to the full completeness guard; any one failing aborts the sync, and results are de-duplicated by reservation ID (FR-016a).
- **Guest returns to the same unit in non-adjacent segments** (A → B → A): → Each A segment is a separate occupancy interval on A's calendar, so A's door is not scheduled for the B segment; the intervals have distinct, stable identities (FR-018).
- **Unassigned multi-unit reservation**: no sub-unit yet. → Listing calendar only, as today (FR-019).
- **Reservation with no usable listing reference at all**: → Abort rather than guess an owner, as today (FR-020).
- **Transient `alteration` during an edit**: → The booking keeps its last-known stored status, dates and room assignment until the reservation leaves `alteration`; it is never retired, a previously exported live stay is never withheld, and in-flight dates are never published; door-code enrichment still refreshes under FR-028–FR-032 (FR-023).
- **Reservation first seen in `alteration`** (no stored booking yet): → Withheld quietly, never retired, not stored until it leaves `alteration`; classified for the health guard (FR-023).
- **`completed` status**: → Exported like `checked_out` and ages out normally (FR-023).
- **Legacy retrieval selected**: the user switches the setting to legacy (e.g. to roll back). → The next sync uses the previous release's reservation retrieval; no database change is needed in either direction, and every safety guard still applies. Legacy retrieval cannot see V3 stay segments, so a multi-segment reservation reverts to the previous release's single full-stay booking in its first unit (the extra per-segment bookings are retired by the normal room-change handling) — the known limitation that this feature fixes, accepted for a temporary rollback. After 2027-03-01 the legacy resource is gone, so a legacy-mode sync fails like any other fetch failure (last-good calendar kept) and the error tells the user to switch back to V3 (FR-041).
- **Status unknown to the bridge**: → Withheld, not retired; warned; shown in the withheld panel; counted by the health guard (FR-022, FR-025).
- **Reservation ID in V3 differs from the legacy ID for the same stay**: not expected, but not explicitly documented. → Detect and fail safe rather than retire and re-create every booking (FR-006).
- **Guest record unavailable**: the guest lookup 404s or fails. → Name falls back to last-good for the same guest, else the existing placeholder behaviour; phone last-4 never borrowed from a different guest (FR-031, FR-032).
- **Inline custom fields absent or malformed on one reservation**: → Treated like today's failed custom-field lookup: last-good values for that reservation are kept, nothing is blanked, the sync is not aborted for that alone (FR-028).
- **Unsupported filter or parameter**: V3 rejects it with HTTP 400 rather than ignoring it (V1 silently ignored it — the root cause of #119 and tykeal/homeassistant-guesty#204). → Surfaced as a sync error; never interpreted as "no reservations" (FR-021).
- **Rate limit hit by another client on the same Guesty account**: → Existing 429 backoff and Retry-After handling applies; exhausted retries on the reservation fetch abort before pruning (FR-033).
- **Configured description field no longer supplied**: → Handled visibly per FR-036–FR-038.

## Requirements *(mandatory)*

### Functional Requirements

#### Retirement compliance and timeline

- **FR-001**: With the default (V3) reservation-retrieval setting (FR-041), every request the Guesty provider makes MUST target a Guesty resource that Guesty documents as supported after 2027-03-01. At the time of writing the only retiring resource the provider calls is the legacy reservation list; the guest lookup's status is unclear (Guesty's migration guide points guest reads at a different resource) and MUST be confirmed and, if necessary, moved.
- **FR-002**: The provider MUST NOT depend on Guesty webhooks or guest-payment resources (it does not today; they remain out of scope).
- **FR-003**: A stable release containing this migration MUST be published no later than **2027-01-31**, leaving at least four weeks before the 2027-03-01 retirement for users to upgrade. A pre-release suitable for real-user validation SHOULD be available by mid-December 2026.
- **FR-004**: The release notes for the stable release MUST tell Guesty users that upgrading before 2027-03-01 is required for continued syncing, and MUST describe any user-visible change made under FR-036–FR-038.

#### Identity contract (NON-NEGOTIABLE)

- **FR-005**: For every reservation that existed before the upgrade, the stored booking identifier (`{reservationId}` or `{reservationId}::{roomId}`), the exported iCal event UID derived from it, and the description line `Booking ID: <base reservation id>` MUST be byte-identical before and after the migration. Rental Control surfaces this line as `booking_id`, and user blueprints use it to write door codes back to Guesty.
- **FR-006**: Equality between legacy and V3 reservation identifiers is strongly implied but not explicitly documented by Guesty. The system MUST verify it before relying on it, and MUST fail safe if it is ever violated: if a sync's results indicate that known reservations have reappeared under different identifiers (for example, an active stored booking's confirmation code returned under a different reservation ID), the sync MUST abort without retiring or creating bookings for the affected listing, and MUST surface an actionable error.
- **FR-007**: Room identifiers used in booking identifiers MUST continue to be the Guesty sub-unit (listing) identifiers used today, so `{reservationId}::{roomId}` is unchanged for reservations assigned to a single unit.

#### Snapshot completeness (door-code safety)

- **FR-008**: A reservation snapshot MUST be treated as authoritative — and therefore allowed to retire bookings absent from it — only when the provider can positively establish that it is complete for the requested scope.
- **FR-009**: Because V3 reports only whether more results exist (no total count), the existing count cross-check MUST be replaced by a completeness guard of at least equivalent strength, designed around "more results" pagination.
- **FR-010**: The fetch MUST abort (raise, publish nothing, retire nothing) when: Guesty reports more results but returns an empty page; the pagination signal is missing or malformed; the page cap is reached before Guesty reports the end; or any page request fails after retries.
- **FR-011**: The fetch MUST use a deterministic ordering, and a detected shift (including the same reservation appearing on two pages) MUST abort the cycle as a transient inconsistency.
- **FR-011a**: Because offset pagination cannot rule out silent omission (an already-fetched record leaving the filtered set shifts a later record into an already-read position while the fetch still ends normally), a normally-ending fetch MUST NOT by itself justify retiring a stored, not-yet-cancelled booking that is absent from the snapshot. Before such a booking is retired, its absence MUST be positively confirmed — for example by looking the reservation up directly and finding it cancelled, no longer in this listing's scope, outside the window, or no longer existing. If the lookup returns the reservation with a segment still in a tracked room of this listing (for example after Guesty re-arranged it so its first segment is in an untracked property, which removes it from the FR-016 search), it MUST be re-attributed from the lookup's segments under the normal rules (FR-017, FR-018, FR-022–FR-023): bookings for rooms it still touches are kept, with status and dates following the lookup and identities byte-identical to those the search would produce; bookings for rooms it no longer touches are retired; their custom-field values and guest details keep their last-good values (FR-032) until the search returns the reservation again; a tracked room it now touches but has no stored booking for is not published from the lookup (the FR-016 residual gap) and is logged; such a result MUST NOT abort the sync. Only a genuinely inconsistent or invalid lookup response may stop confirmation, and then nothing is retired on its strength. An absence that cannot be confirmed (lookup fails, is rate-limited, or is ambiguous) MUST leave the booking as it is for that cycle and be logged. Confirmation requests count against FR-027's budget and SHOULD be batched.
- **FR-012**: On abort, the previously published calendar and stored bookings MUST be left unchanged, the listing's sync error MUST be recorded, and the next scheduled sync MUST retry normally. The existing in-progress guard (never retire a stored booking whose stay is currently underway merely because it is absent) MUST be preserved.

#### Server-side stay-window filtering (folded from #133)

- **FR-013**: The fetch MUST limit results server-side to stays relevant to the sync window, AND any stay that has checked in but not yet checked out at sync time MUST be included regardless of how long ago it checked in. A filter keyed only on check-in date is therefore not acceptable on its own.
- **FR-014**: The stay window MUST continue to cover everything the calendar export and the retention logic need today (in-progress stays, stays that checked out within the export look-back, and the existing look-ahead).
- **FR-015**: Server-side filtering MUST be demonstrated to narrow results (not merely to return HTTP 200), both in automated tests and during real-user validation (FR-042), because a filter that is silently not applied was the root cause of #119.

#### Multi-unit and multi-segment attribution

- **FR-016**: Each sync MUST retrieve reservations with one combined, server-side listing-filtered search naming every tracked listing plus, for multi-unit properties, the parent unit type and its known sub-units, so that a reservation whose first segment is in any tracked listing, or anywhere within a tracked multi-unit property, is captured; it MUST then be captured for every tracked listing in which ANY of its stay segments lies. Because Guesty's listing filter matches only the first segment, a reservation whose first segment is in a property this installation does not track is not discovered by the search; this residual gap is accepted (the previous release has the same gap for reservations moving in from an untracked property). It covers every tracked-room booking of such a reservation that is not already stored: a never-captured reservation, and a stored one that Guesty re-arranges into a tracked room it did not occupy before (FR-011a keeps only already-stored bookings). The gap MUST be detectable from debug diagnostics (FR-043). Request cost MUST scale with the tracked listings' bookings, not with the size of the Guesty account.
- **FR-016a**: If the listing filter would make a request too long, the search MUST be split into several searches of at most a documented number of listing IDs each. Every split search MUST individually satisfy FR-008–FR-011; any one failing MUST abort the whole sync as in FR-010; results MUST be de-duplicated by reservation ID, and a reservation returned with conflicting content by two split searches MUST abort the sync as a shift (FR-011).
- **FR-017**: A captured reservation MUST be attributed to the sub-unit(s) it is actually assigned to, segment by segment; a segment in an untracked listing contributes nothing to this listing's calendars.
- **FR-018**: For a reservation that moves between units mid-stay, each tracked room's calendar MUST cover only the dates the guest is assigned to that room; a door MUST NOT be scheduled for dates the guest is assigned elsewhere. Each contiguous assignment of a reservation to a room is a separate occupancy interval (so A → B → A yields two intervals on A's calendar, never one merged interval spanning the B segment). Identity rules: a reservation's first (or only) interval in a room keeps the existing `{reservationId}::{roomId}` identity, so single-segment reservations are unchanged per FR-005/FR-007; any further interval in the same room receives a distinct identity that is stable across syncs; every interval's `Booking ID:` line carries the base reservation ID.
- **FR-019**: An unassigned multi-unit reservation (no sub-unit yet) MUST be handled as today: stored against the listing, shown in the listing calendar only, logged at informational level (issue #116 behaviour).
- **FR-020**: Client-side ownership verification MUST be retained as a backstop: a reservation that references only sibling listings MUST NOT be stored against this listing, and a reservation that carries no usable listing reference MUST abort the fetch rather than be guessed (issue #119 behaviour). The distinction between the wider *fetch* scope (listing plus parent unit type) and the narrower *ownership* scope MUST be preserved.
- **FR-021**: A rejected request (HTTP 400 for an unsupported filter, operator or sort) MUST surface as a sync error naming the cause and MUST never be interpreted as an empty result.

#### Status policy

- **FR-022**: The existing status policy MUST be preserved: exported statuses (`confirmed`, `checked_in`, `checked_out`, and V3's `completed` per FR-023) export; `canceled` retires the booking; recognised non-export statuses (`expired`, `closed`, `declined`, `awaiting_payment`) are withheld quietly (debug-level log); `inquiry` and `reserved` remain excluded; any other status is withheld but NOT retired, logged as a warning, and listed in the admin withheld-bookings panel (policy A).
- **FR-023**: A transient `alteration` status MUST NEVER retire a booking and MUST NEVER remove a previously exported live stay from the feed. `alteration` and `completed` MUST both be classified (they MUST NOT count as unrecognised for the health guard). Their treatment is:
  - **`alteration` with a stored booking**: every stored booking of the reservation MUST keep its last-known stored status, dates and room assignment until the reservation leaves `alteration`. The sync MUST NOT retire, withhold, re-date, re-room or create bookings for it from the in-flight data, and the reservation's presence in the snapshot MUST protect its stored bookings from pruning. Custom-field values and guest contact details MUST continue to follow FR-028–FR-032 unchanged (refreshed when obtainable, never blanked by a failed lookup, never leaked across a guest reassignment), so freshness-window door codes stay current during the edit. Logged at debug level.
  - **`alteration` with no stored booking** (first seen mid-edit): MUST be withheld quietly (debug-level log), MUST NOT be stored or exported until it leaves `alteration`, and MUST NOT be listed in the withheld-bookings panel as an unknown status.
  - **`completed`**: MUST be treated like `checked_out` — exported, and aging out of the feed and retention normally.
- **FR-024**: Status comparison MUST remain case-insensitive and MUST accept Guesty's single-L `canceled` spelling (and continue to accept `cancelled`).
- **FR-025**: The health guard MUST be preserved: if more than 50% of a listing's in-window mapped reservations carry unclassified statuses, with at least the existing minimum sample of 4, the fetch aborts and the last-good calendar keeps serving.
- **FR-026**: A reservation with no status at all MUST abort the fetch, as today (V3 documents status as always present, so absence indicates a contract break).

#### Enrichment, request budget and door-code freshness

- **FR-027**: Guesty requests per steady-state sync cycle MUST be no higher than the previous release's for the same account (see SC-006). The account-wide limits (15/s, 120/min, 5,000/h, shared with other clients of the same Guesty account) MUST be respected.
- **FR-028**: Custom-field values delivered inline with V3 reservations MUST be used in place of per-reservation custom-field requests. A reservation whose inline custom fields are missing or malformed MUST keep its last-good custom-field values (never blanked), MUST NOT abort the sync on that basis alone, and SHOULD be logged.
- **FR-029**: Guest name, phone and email MUST be resolved via a guest lookup (V3 search no longer embeds the guest's name), at most once per distinct guest per sync. Because V3 provides no reservation "last updated" timestamp, any reuse of previously fetched guest data MUST use a key that changes whenever the reservation's guest changes (at minimum, the guest identifier).
- **FR-030**: The #129 freshness guarantees MUST be preserved: for any stay in progress, checked out within the last 7 days, or starting within the next 30 days, custom-field values and guest phone last-4 MUST reflect Guesty's data as of the current sync whenever that data was obtainable.
- **FR-031**: Guest reassignment MUST never leak the previous guest's phone last-4 (or other contact details) into the reassigned reservation, including when the new guest's lookup fails.
- **FR-032**: A failed guest or custom-field lookup MUST never wipe values already published for the same guest/reservation; and a guest's name, once known, MUST NOT be blanked from the feed by a failed lookup or by reuse.
- **FR-033**: Existing rate-limit behaviour MUST be preserved: HTTP 429 handling with exponential backoff and Retry-After, and exhausted retries during the reservation fetch abort the sync before any pruning.

#### Description and custom-data continuity

- **FR-034**: Reservation details that V3 still supplies MUST keep their existing custom-data keys so that users' configured description fields and labels keep working without reconfiguration: booking source (`source`) and confirmation code (`confirmationCode`) unchanged; guest count (`guestsCount`) is the guest count V3 reports for the stay segment(s) the booking covers — for a single-segment reservation this equals the reservation's guest count as today — rendered as a whole number; notes (`notes`) are the non-empty text of V3's general, guest and special-request notes, in that order, joined with `"; "` as today. V3's cleaning and key-code notes MUST NOT be included (the key code is a door secret the key never carried).
- **FR-035**: Custom-field labels resolved from Guesty's account definitions (issues #127/#130/#132) MUST be unchanged, and custom-field values MUST keep the same keys so existing description configuration still matches.
- **FR-036**: For description fields V3 no longer supplies on the reservation — number of nights (`nightsCount`, not supplied but derivable from stay dates) and amount paid, balance due and currency (`money_totalPaid`, `money_balanceDue`, `money_currency`, moved by Guesty to separate financial resources) — the system MUST handle existing installs explicitly as follows: `nightsCount` MUST be re-derived with an identical key and meaning — the reservation's total number of nights, computed from its overall local check-in and check-out dates — so it needs no notice; `money_totalPaid`, `money_balanceDue` and `money_currency` MUST be retired with the notice of FR-037/FR-038 and MUST NOT be re-sourced from Guesty's financial resources (doing so would add per-reservation requests against FR-027's budget).
- **FR-037**: Any description field that is retired or changes meaning MUST be surfaced non-silently: a one-time warning in the log at upgrade/first sync naming the field and the affected listings, and a visible indication in the admin UI's field configuration. A configured field MUST NOT simply render nothing with no explanation.
- **FR-038**: The user's stored description configuration (enabled fields, labels, order) MUST NOT be deleted by the upgrade; a retired field remains visible as retired so the user can remove it deliberately.

#### Validation and cutover

- **FR-039**: Automated tests MUST cover the V3 behaviour using fixtures built from Guesty's documented V3 schema, including at least: multi-page results with "more results" boundaries, each FR-010/FR-011 abort condition, and a deletion/filter-exit shift that a normally-ending fetch cannot detect (FR-011a); an in-progress long stay; single-unit, multi-unit assigned, unassigned and mid-stay-move reservations, including a move whose first segment is in another tracked listing, a stored reservation re-arranged so its first segment is outside the tracked properties (FR-011a re-attribution), and the FR-016 residual gap; a split (FR-016a) search with a failing split and a duplicate across splits; every V3 status including `alteration` and `completed`; inline custom fields present, absent and malformed; the identity contract (FR-005) against stored data from the previous release; and switching the FR-041 setting in both directions.
- **FR-040**: Contract tests MUST pin every Guesty request the provider makes (resource, filters, sort, paging) so an accidental return to a retiring resource or an unsupported filter fails the test suite.
- **FR-041**: The cutover from the legacy reservation resource to V3 MUST be a user toggle: an add-on configuration setting selects legacy or V3 reservation retrieval, defaulting to V3 in both the pre-release and the stable release. Selecting legacy MUST restore the previous release's reservation retrieval behaviour so a user can roll back without downgrading the add-on (add-on downgrades do not reverse database migrations). Switching in either direction MUST NOT require or perform a database change, MUST keep FR-005 identities, and MUST leave every safety guard in force. The legacy path MUST be isolated so it can be removed cleanly in a release after 2027-03-01; once Guesty has retired the legacy resource, a legacy-mode fetch failure MUST abort as in FR-010 and its error MUST tell the user to switch to V3. Because legacy retrieval does not expose V3 stay segments, rolling back MUST be allowed to revert multi-segment reservations to the previous release's representation (one booking in the first unit for the whole stay, identity `{reservationId}::{roomId}` unchanged; additional FR-018 intervals retired through the normal room-change handling); single-segment identities MUST be unaffected, and the rollback instructions and release notes MUST state this limitation. There is no shadow-comparison mode.
- **FR-042**: Before the stable release, at least one real Guesty user MUST validate a pre-release against their live account, and the following MUST be confirmed from that validation: legacy and V3 reservation IDs are equal for existing reservations; server-side listing and stay-window filters narrow results as specified (including that a parent unit-type ID in the listing filter matches reservations assigned to its sub-units); multi-unit and (if any exist) multi-segment reservations are attributed correctly; the guest lookup works on a non-retiring resource; the shape of statuses seen in the wild, including `alteration`/`completed` if encountered.
- **FR-043**: Debug-level diagnostics MUST make FR-042 answerable from logs (status values seen, segment counts and every segment's listing ID per reservation — so the FR-016 residual gap can be detected in the field — pagination signals, Guesty request counts per sync) without logging guest names, phone numbers, emails or door codes.

### Key Entities

- **Reservation (Guesty V3)**: A Guesty booking. Key attributes: reservation ID (expected equal to the legacy ID), status (V3 vocabulary), overall check-in/check-out (first segment's start, last segment's end), guest reference (ID only — no embedded name), confirmation code, source, notes (structured), inline custom-field values, and one or more stay segments.
- **Stay segment**: One contiguous assignment of a reservation to a unit. Key attributes: assigned listing/sub-unit, parent listing (multi-unit only), segment dates, guest count. A reservation may have several after a mid-stay move.
- **Booking**: The bridge's stored record of one occupancy interval of a reservation in a room, identified by `{reservationId}::{roomId}` (or `{reservationId}` with no room). Source of the exported event UID and the `Booking ID:` description line. Unchanged for single-segment reservations; a reservation that returns to the same room after a mid-stay move gains an additional, distinctly identified booking for that room (FR-018).
- **Reservation snapshot**: The complete set of in-scope reservations for one listing at one sync, attributed from the sync's combined search (FR-016). Authoritative (may retire absent bookings) only when proven complete.
- **Enrichment record**: Guest contact details and custom-field values attached to a booking, with provenance (which guest/reservation they came from) so failed lookups can fall back safely and reassignment cannot leak data.
- **Description field configuration**: Per-listing user choice of which custom-data keys appear in event descriptions, with labels and order. Keys must remain stable or be visibly retired.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A stable release with the migration is published by 2027-01-31; from that release on, with the default (V3) setting, 100% of Guesty requests made by the bridge target resources Guesty supports after 2027-03-01.
- **SC-002**: Across the upgrade, 100% of previously exported events keep a byte-identical event UID and `Booking ID:` line (verified by automated comparison in tests and confirmed by at least one real user's before/after feed comparison).
- **SC-003**: Zero bookings are retired by a sync whose snapshot is incomplete or inconsistent: every FR-010/FR-011 condition aborts the sync with the last-good calendar unchanged, and no booking whose absence was not positively confirmed (FR-011a) is retired, in 100% of tested scenarios.
- **SC-004**: 100% of tested in-progress stays (including those that checked in before the window's lower bound) remain in the feed across every sync.
- **SC-005**: 100% of tested multi-unit and multi-segment reservations appear in exactly the right room calendars for exactly the assigned dates, and in no other room calendar — except the documented FR-016 residual gap (tracked-room bookings not already stored for reservations whose first segment is untracked, whether never captured or re-arranged into a new tracked room), which is tested as not discovered and as leaving published data unaffected.
- **SC-006**: For the reference account fixture (several listings, ~110 reservations, multi-page history), Guesty requests per steady-state sync cycle are less than or equal to the previous release's, and no sync cycle exceeds 120 requests in any rolling minute.
- **SC-007**: For an account with years of history outside the sync window, or with many listings the installation does not track, reservation pages fetched per sync fall in proportion to the tracked listings' in-window reservation count rather than total account history or account size.
- **SC-008**: A live stay transitioning through `alteration` stays continuously present in the feed in 100% of tested transitions.
- **SC-009**: After upgrading, 100% of previously configured description fields either render equivalent content (`source`, `confirmationCode`, `guestsCount`, `notes`, `nightsCount`, custom fields) or are flagged as retired to the user in the admin UI and log (`money_totalPaid`, `money_balanceDue`, `money_currency`); no configured field disappears silently.
- **SC-011**: Switching the reservation-retrieval setting from V3 to legacy and back, on an installation with exported bookings, requires no add-on downgrade and changes no exported event UID or `Booking ID:` line of any single-segment reservation, in 100% of tested switches; multi-segment reservations follow FR-041's documented rollback behaviour.
- **SC-010**: At least one real Guesty user confirms every FR-042 item on a pre-release before the stable release is cut.

## Assumptions

- **Only the reservation list is retiring among the provider's calls.** Per Guesty's 2026-11-05 changelog and migration guide: the reservation list is retiring; the per-reservation custom-field resource already in use is a V3 resource and is final; authentication, listing and account resources are not retiring. The guest resource is the open question addressed by FR-001.
- **Reservation IDs are expected to carry over unchanged** (migration guide maps legacy reads to V3 reads by the same ID; V3 custom fields are documented as compatible with legacy reservations; our Home Assistant Guesty integration already uses legacy IDs with V3 custom fields in production). FR-006 guards the residual risk.
- **V3 rejects unsupported filters with HTTP 400** rather than silently ignoring them as the legacy resource did. This is a benefit — it eliminates the #119 / tykeal/homeassistant-guesty#204 failure class at the source — but client-side ownership verification is retained regardless (FR-020).
- **V3 offers no field projection**, so the legacy field-projection hazard (issue #121: an omitted projection drops `status`) no longer applies; V3 documents `status` as always present.
- **Stay-window overlap can be expressed with V3's documented date operators** (check-out on/after the window start AND check-in on/before the window end); this is inferred from the documented operators and is confirmed during FR-042 validation.
- **`reserved` stays excluded and `awaiting_payment` stays withheld**, even though Guesty's documentation lists both among "blocking" statuses. Changing that policy is outside this feature.
- **Guesty's listing filter accepts a list of listing IDs and matches each against the first segment's unit or unit type**, so naming a multi-unit parent catches its sub-units' reservations; this is documented but unverified, and is confirmed during FR-042 validation.
- **The FR-016 residual gap is acceptable.** A reservation that starts in an untracked property and moves into a tracked one was not captured by the previous release either, and a stored reservation re-arranged into a new tracked room behind an untracked first segment is the same case; capturing it would require searching the whole Guesty account every sync, whose cost grows with account size rather than with the tracked listings.
- **Mid-stay unit moves are a V3 capability.** For a reservation with a single stay segment, FR-018 produces exactly the event the previous release produced (same identity, same dates); only multi-segment reservations gain per-segment dates.
- **The legacy setting exists only for rollback during the transition.** It is not expected to be used in steady state; it is removed in a release after 2027-03-01, whose release notes announce the removal.
- **The guest-name placeholder** used today when no name is available continues to apply when a guest lookup yields nothing and no last-good name exists.
- **Listing and account resources are not touched by this feature.** Documentation differences noticed during research (account ID field name; whether the single-listing resource returns child-listing and multi-unit blocks) concern non-retiring resources that work in production today; they are recorded for FR-042 validation but are not changed here unless found broken.

## Out of Scope

- Guesty webhooks and guest-payment resources (not used by the bridge).
- The Home Assistant Guesty integration's own migration, tracked separately as tykeal/homeassistant-guesty#223 (reference only).
- Cloudbeds behaviour (must not change).
- Writing door codes back to Guesty (done by user blueprints via the booking ID; this feature only guarantees that ID is unchanged).
- Changes to which statuses are exported, beyond defining treatment for the new V3 statuses.
- A shadow-comparison mode that fetches both legacy and V3 data (FR-041).
- Re-sourcing the retired financial description fields from Guesty's financial resources (FR-036).

## Dependencies

- Guesty's published V3 documentation as of 2026-11-10 and its 2027-03-01 retirement date.
- At least one Guesty user willing to run a pre-release against their live account (no Guesty test account is available to the project).
- Prior decision records: issues #117 (deferral rationale and blocking concerns) and #133 (stay-window filtering and the in-progress-stay hazard).
