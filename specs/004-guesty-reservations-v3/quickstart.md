<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->
# Quickstart: Guesty Reservations V3 Migration

**Feature Branch**: `004-guesty-reservations-v3`

## 1. Developer setup

```bash
cd rentalsync-bridge
uv sync
uv run pytest tests/ -x -q
uv run ruff check src/ tests/
```

## 2. Selecting the reservation API

| Environment | How |
|---|---|
| Home Assistant add-on | Configuration tab → **Guesty reservations API** → `v3` (default) or `legacy`. Then restart the add-on |
| Standalone / dev | `export GUESTY_RESERVATIONS_API=v3` (or `legacy`) before `uv run uvicorn src.main:app` |

The startup log prints `Guesty reservations API: v3`. Each cycle's INFO line reports the request count by resource, for example `search=2 by_ids=0 guests=12 custom_fields=0`.

## 3. Targeted tests

```bash
cd rentalsync-bridge
uv run pytest tests/contract/test_guesty_v3_contract.py -q        # request/response contracts
uv run pytest tests/unit/test_guesty_v3_source.py -q              # sweep, guard, mapping, intervals
uv run pytest tests/unit/test_guesty_service.py -q                # legacy path + provider surface
uv run pytest tests/integration/test_guesty_v3_sync.py -q         # end-to-end sync, FR-011a, alteration
uv run pytest tests/integration/test_guesty_v3_toggle.py -q       # SC-011 identity across switch
uv run pytest tests/integration/test_guesty_v3_budget.py -q       # SC-006 call counts
uv run pytest tests/integration/test_logging_privacy.py -q               # FR-043 no PII
```

All fixtures are synthetic. The `check-fixture-pii` pre-commit hook rejects realistic personal data.

## 4. Pre-release validation (FR-042)

Run this on the owner's live installation, with the pre-release installed and `v3` selected:

1. Set `log_level: debug` and restart.
2. Let three or more cycles run, then confirm each of the following.
   - **Identity.** The first-sync INFO line reports that every stored reservation ID was matched. No `SyncServiceError` for an identity mismatch appears.
   - **Feeds.** Every listing's iCal feed has the same event UIDs, `SUMMARY` and `Booking ID:` lines as the last legacy feed. Diff saved copies with `diff`.
   - **Codes.** The door-code (last-4) values are unchanged for every current and upcoming stay.
   - **Narrowing.** The DEBUG line shows that the search window narrowed the result. Its count must be below the account's total reservation count.
   - **Multi-segment.** For any multi-unit or room-move reservation, the DEBUG shape line lists its segments and the resulting intervals.
   - **Statuses.** The DEBUG status histogram contains only documented values.
   - **Paging.** No completeness-guard warnings appear, and there is no short `hasMore` page.
   - **Budget.** The per-cycle INFO request totals are at or below the legacy baseline (about 20–40). Rate-limit warnings do not increase.
3. Record the results for every item in research R15 on issue #145.

## 5. Rollback

1. Configuration → **Guesty reservations API** → `legacy`, then restart. There is no add-on downgrade and no DB change.
2. Single-segment bookings keep their UIDs.
3. A multi-segment reservation collapses to one booking in its first unit. Extra `#n` intervals are retired at the next sync. This limitation is documented in the release notes.
4. Legacy remains usable only until Guesty retires `/v1/reservations` on 2027-03-01.

## 6. Release gate

| Milestone | Target | Gate |
|---|---|---|
| Pre-release | mid-December 2026 | all tests green; quickstart §4 run by the owner |
| Stable | by 2027-01-31 | every FR-042 item confirmed by at least one real Guesty user on the pre-release (SC-010) |
| Legacy removal | a release after 2027-03-01 | separate change |

The owner cuts all releases.
