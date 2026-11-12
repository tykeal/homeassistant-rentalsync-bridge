<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->
# Contract: Guesty Guest Lookup, guests-crud (as consumed)

**Resource**: `GET https://open-api.guesty.com/v1/guests-crud/{guestId}`
**Used by**: `GuestyProvider.get_guest()` in V3 mode (legacy mode keeps `GET /v1/guests/{id}` unchanged)
**Retiring**: no. It is the migration guide's target for guest reads (research R1)

## Request

| Parameter | Value | Notes |
|---|---|---|
| path `guestId` | the reservation's `guestId` | |
| `fields` | `fullName phone email` | **required** by the resource; space-separated |
| Authorization | `Bearer <token>` | |

- The call is made **at most once per distinct guest per sync**, and only for reservations that are not reusable (research R9).
- It is never made for a reservation without `guestId`.

## Response fields consumed

```jsonc
{
  "_id": "string",
  "fullName": "string",   // → PMSGuest.full_name; empty/missing → "" (placeholder logic unchanged)
  "phone": "string|null", // → guest_phone, guest_phone_last4
  "email": "string|null"  // → guest_email
}
```

## Outcomes

| Result | `get_guest` returns | Sync behaviour |
|---|---|---|
| 200 with a body | `PMSGuest` | write guest keys plus `_guest_source_id` and `_guest_name` |
| 404 | `None` | no guest data, not a failure (as today) |
| other 4xx/5xx, transport error, 429 after retries | raises `PMSProviderError` | the guest ID is marked failed. Last-good is used only when `_guest_source_id` matches, otherwise nothing (FR-031, FR-032) |
| 401/403 | raises `PMSAuthenticationError` | as today |

## Privacy

Names, phones and emails are never logged at any level (FR-043). This is enforced by the existing `test_logging_privacy.py` pattern, which is extended to V3.

## Contract tests

- the exact path and `fields` value
- that legacy mode still calls `/v1/guests/{id}`
- one call per distinct guest
- each outcome row
