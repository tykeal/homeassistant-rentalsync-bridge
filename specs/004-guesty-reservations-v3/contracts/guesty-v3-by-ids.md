<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->
# Contract: Guesty Reservations V3 Retrieve by IDs (as consumed)

**Resource**: `GET https://open-api.guesty.com/v1/reservations-v3`
**Used by**: `GuestyProvider.confirm_absent()` (V3 mode only), for FR-011a confirmed-absence retirement (research R5)
**Retiring**: no

## Request

| Parameter | Value | Notes |
|---|---|---|
| `reservationIds` | array, form-exploded: `reservationIds=<id>&reservationIds=<id>…` | **≤ 10 per call**: up to 9 candidates plus 1 control |
| Authorization | `Bearer <token>` | |

- **No other parameters are sent.** In particular, the financial `merge*` flags are left unset.
- The **control** is a `reservationId` from the current accepted sweep that is in this listing's scope.
- When the listing's snapshot has no reservations, the control is taken from the account sweep.
- If the sweep is empty, no control exists. Every candidate then gets the verdict `UNCONFIRMED`, and no call is made.
- Candidates are de-duplicated by base reservation ID and sent in ascending order.

Example: `GET /v1/reservations-v3?reservationIds=6650aa…&reservationIds=6650ab…&reservationIds=<control>`

## Response fields consumed

```jsonc
[                                    // REQUIRED bare array
  {
    "_id": "string",                 // REQUIRED: matched against the requested IDs
    "status": "canceled",            // REQUIRED: 12-value enum
    "stay": [                        // segments: scope + window test
      { "unitTypeId": "string", "unitId": "string",
        "checkInDateLocalized": "YYYY-MM-DD", "checkOutDateLocalized": "YYYY-MM-DD" }
    ]
  }
]
```

- The segment room is `unitId ?? unitTypeId`.
- The window test uses the localized dates against R3's padded `[D_start, D_end]`.
- All other fields, including the financial and guest blocks, are ignored and never logged.

## Outcome per candidate

The whole response is validated first: it is a list, every item has an `_id`, every `_id` was requested, and there are no duplicates. Any violation makes the batch `UNCONFIRMED`. Only a valid response yields per-candidate verdicts:

| Result | Verdict |
|---|---|
| returned, `canceled`/`cancelled` | `CONFIRMED_ABSENT` |
| returned, `inquiry`/`reserved` | `CONFIRMED_ABSENT` |
| returned, no segment in the ownership scope | `CONFIRMED_ABSENT` |
| returned, entirely outside the window | `CONFIRMED_ABSENT` |
| returned, `alteration` | `UNCONFIRMED` |
| returned, in scope and window, any other status | `PRESENT`: the sync aborts the listing (FR-011), and the cycle's cached sweep is marked failed, so every later listing in the cycle aborts too |
| not returned, control returned | `CONFIRMED_ABSENT` |
| not returned, control not returned | `UNCONFIRMED` + ERROR log |
| response not a list, item without `_id`, HTTP error, or 429 after retries | `UNCONFIRMED` for the whole batch + WARNING log |
| any returned `_id` not in the requested set, or a duplicate `_id` | `UNCONFIRMED` for the whole batch + ERROR log (parameter ignored or mis-serialised) |
| HTTP 400 | `UNCONFIRMED` for the batch + ERROR log naming the cause (FR-021); never "absent" |

## Contract tests

`tests/contract/test_guesty_v3_contract.py`:

- the exact query serialisation
- no more than 10 IDs per call, and that the control is always included
- each outcome row
- an **ignored-parameter** fixture: the response contains unrelated account reservations, including the control, but omits a still-existing candidate. Every candidate must be `UNCONFIRMED`, and nothing is retired
- that no request is made when there are no candidates
