<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->
# Contract: Guesty Reservations V3 Retrieve by IDs (as consumed)

**Resource**: `GET https://open-api.guesty.com/v1/reservations-v3`
**Used by**: `GuestyProvider.confirm_absent()` (V3 mode only), for FR-011a confirmed-absence retirement and re-attribution (research R5)
**Retiring**: no

## Request

| Parameter | Value | Notes |
|---|---|---|
| `reservationIds` | array, form-exploded: `reservationIds=<id>&reservationIds=<id>…` | **≤ 10 per call**: up to 9 candidates plus 1 control |
| Authorization | `Bearer <token>` | |

- **No other parameters are sent.** In particular, the financial `merge*` flags are left unset.
- The **control** is a `reservationId` from the current accepted combined search result (research R2), preferably one in this listing's scope, otherwise any.
- If the combined result is empty, no control exists. The call is still made with candidates only: a **returned** candidate's verdict stands (whole-response validation proves it was requested), and an **omitted** candidate is `UNCONFIRMED`.
- Candidates are de-duplicated by base reservation ID and sent in ascending order.

Example: `GET /v1/reservations-v3?reservationIds=6650aa…&reservationIds=6650ab…&reservationIds=<control>`

## Response fields consumed

```jsonc
[                                    // REQUIRED bare array
  {
    "_id": "string",                 // REQUIRED: matched against the requested IDs (search calls it reservationId)
    "status": "canceled",            // REQUIRED: 12-value enum
    "stay": [                        // REQUIRED, chronological, ≥ 1
      { "unitTypeId": "string",      // the listing or multi-unit parent
        "unitId": "string",          // the assigned sub-unit (absent when unassigned)
        "checkInDateLocalized": "YYYY-MM-DD",
        "checkOutDateLocalized": "YYYY-MM-DD" }
    ]
  }
]
```

### Shape differences from search, and normalisation

The by-IDs item has **no** `reservationId`, **no** `stay[].listingId`/`parentListingId` and **no** UTC `checkIn`/`checkOut` instants. `normalise_by_ids_item` (data-model.md §2.3) maps it to the search shape before attribution:

| Search field | From by-IDs |
|---|---|
| `reservationId` | `_id` |
| `stay[].listingId` | `unitId ?? unitTypeId` |
| `stay[].parentListingId` | `unitTypeId` when `unitId` is present and differs from it, else absent |
| `stay[].check*DateLocalized` | same fields |
| UTC instants | none: re-attributed rows keep or re-anchor their stored instants (research R5) |

- The same R6 attribution and identity code then runs, so `pms_booking_id`, the event UID and the `Booking ID:` line are **byte-identical** to what a search result for the same reservation produces (FR-005).
- The window test uses the first segment's `checkInDateLocalized` and the last segment's `checkOutDateLocalized` against R3's padded `[D_start, D_end]`, the same bounds the search filter used.
- The first-segment test for `INCONSISTENT` checks the first segment's `unitId` and `unitTypeId` against the searched filter ID set.
- All other fields, including the financial and guest blocks, are ignored and never logged.

## Outcome per candidate

The whole response is validated first: it is a list, every item has an `_id` and a non-empty `stay[]` whose segments each have `unitId` or `unitTypeId`, every `_id` was requested, and there are no duplicates. Any violation makes the batch `UNCONFIRMED`. Only a valid response yields per-candidate verdicts:

| Result | Verdict |
|---|---|
| returned, `canceled`/`cancelled` | `CONFIRMED_ABSENT` |
| returned, `inquiry`/`reserved` | `CONFIRMED_ABSENT` |
| returned, no segment in the ownership scope | `CONFIRMED_ABSENT` |
| returned, entirely outside the window | `CONFIRMED_ABSENT` |
| returned, `alteration` | `UNCONFIRMED` (FR-023 hold) |
| returned, any other status, a segment in scope, in window, first segment's `unitId` and `unitTypeId` **not** in the searched filter IDs | `REATTRIBUTED`, carrying the normalised reservation: rows in still-occupied rooms are kept (mapped status, re-anchored dates), other rows retire as room changes; **no abort** |
| as above, but the first segment's `unitId` or `unitTypeId` **is** in the searched filter IDs | `INCONSISTENT`: this listing's sync aborts (FR-011) with an actionable error; no other listing is affected |
| not returned, control returned | `CONFIRMED_ABSENT` |
| not returned, control not returned or no control available | `UNCONFIRMED` + ERROR log |
| response not a list, invalid item, HTTP error, or 429 after retries | `UNCONFIRMED` for the whole batch + WARNING log |
| any returned `_id` not in the requested set, or a duplicate `_id` | `UNCONFIRMED` for the whole batch + ERROR log (parameter ignored or mis-serialised) |
| HTTP 400 | `UNCONFIRMED` for the batch + ERROR log naming the cause (FR-021); never "absent" |

## Contract tests

`tests/contract/test_guesty_v3_contract.py`:

- the exact query serialisation
- no more than 10 IDs per call, and that the control is always included
- each outcome row
- an **ignored-parameter** fixture: the response contains unrelated account reservations, including the control, but omits a still-existing candidate. Every candidate must be `UNCONFIRMED`, and nothing is retired
- that no request is made when there are no candidates
- **shape normalisation**: the same reservation as a search item and as a by-IDs item (single-segment, multi-unit assigned, unassigned, and A → B → A) yields identical `pms_booking_id`s
- `REATTRIBUTED` vs `INCONSISTENT` on the first segment's `unitId`/`unitTypeId` membership in the filter IDs
- with no control available, a returned cancelled candidate is still `CONFIRMED_ABSENT` and an omitted one is `UNCONFIRMED`
