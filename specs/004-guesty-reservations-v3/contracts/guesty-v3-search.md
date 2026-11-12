<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->
# Contract: Guesty Reservations V3 Search (as consumed)

**Resource**: `GET https://open-api.guesty.com/v1/reservations-v3/search`
**Used by**: `V3ReservationSource.sweep()` (`src/providers/guesty/reservations_v3.py`), once per sync cycle (research R2)
**Retiring**: no

## Request (pinned by contract tests, FR-040)

| Parameter | Value | Notes |
|---|---|---|
| `filter[checkOut][gte]` | `YYYY-MM-DD` = UTC date(now − 91 d) − 1 d | in-progress stays are always included (FR-013) |
| `filter[checkIn][lte]` | `YYYY-MM-DD` = UTC date(now + 365 d) + 1 d | |
| `sort` | `_id` | ascending, deterministic (FR-011) |
| `limit` | `100` | documented maximum |
| `skip` | `0, 100, 200, …` | `page × 100`; at most 500 pages |
| Authorization | `Bearer <token>` | existing token manager |

**MUST NOT be sent**:

- `filter[listingId]`, because it matches only the first segment (R2)
- `fields`/projection, which is not supported
- any parameter the reference does not document

Any HTTP 400 surfaces as `PMSProviderError("Guesty rejected the reservation search: <status> <body excerpt>")` and aborts the cycle (FR-021). It is never an empty result.

Example: `GET /v1/reservations-v3/search?filter%5BcheckOut%5D%5Bgte%5D=2026-08-11&filter%5BcheckIn%5D%5Blte%5D=2027-11-13&sort=_id&limit=100&skip=0`

## Response fields consumed

```jsonc
{
  "results": [                       // REQUIRED list
    {
      "reservationId": "string",     // REQUIRED; identity; strictly ascending across the sweep
      "status": "confirmed",         // REQUIRED key (absent → abort, FR-026); 12-value enum
      "guestId": "string|null",
      "confirmationCode": "string",  // custom_data.confirmationCode; FR-006 check
      "source": "string",            // custom_data.source
      "checkIn": "ISO-8601 UTC",     // first segment start (change key)
      "checkOut": "ISO-8601 UTC",    // last segment end (change key)
      "checkInDateLocalized": "YYYY-MM-DD",   // nightsCount
      "checkOutDateLocalized": "YYYY-MM-DD",  // nightsCount
      "notes": { "other": "", "guest": "", "specialRequests": "", "cleaning": "", "keyCode": "" },
                                     // only other/guest/specialRequests used; cleaning & keyCode ignored
      "customFields": [ { "_id": "string", "fieldId": "string", "value": "any" } ],
                                     // OPTIONAL: absent when no value set (R8)
      "stay": [                      // REQUIRED, chronological, ≥ 1
        {
          "listingId": "string",     // REQUIRED: unitId ?? unitTypeId → interval room_id
          "parentListingId": "string",  // multi-unit only
          "checkIn": "ISO-8601 UTC", // interval start
          "checkOut": "ISO-8601 UTC",// interval end
          "checkInDateLocalized": "YYYY-MM-DD",
          "checkOutDateLocalized": "YYYY-MM-DD",
          "guestsCount": 2           // guestsCount
        }
      ]
    }
  ],
  "pagination": { "skip": 0, "limit": 100, "hasMore": true }   // REQUIRED
}
```

All other fields are ignored. They are never logged at INFO or above, and personal fields are never logged at all.

## Validation → outcome

| Condition | Outcome |
|---|---|
| missing `results` or `pagination`, or `hasMore` not a boolean | abort (FR-010) |
| `pagination.skip` ≠ requested, or `len(results) > limit` | abort |
| `hasMore` with an empty page | abort (FR-010) |
| `hasMore` with `len(results) < limit` | abort |
| `reservationId` missing or not a string | abort |
| duplicate `reservationId` in the sweep | abort (FR-011) |
| `reservationId` not strictly ascending | abort (FR-011) |
| `hasMore` still true at page 500 | abort (FR-010) |
| transport error, or 429 after retries | abort (FR-010, FR-033) |
| `status` key missing on any item | abort (FR-026) |
| `stay` missing or empty, or a segment without `listingId` | abort (FR-020) |
| malformed `customFields` | **not** an abort: `InlineCustomFields(state="malformed")` (FR-028) |

## Contract tests

`tests/contract/test_guesty_v3_contract.py`:

- the exact query for the first and subsequent pages
- the absence of `filter[listingId]` and `fields`
- the request never targets `/v1/reservations` in V3 mode
- each validation row above, using fixtures built from the documented schema
