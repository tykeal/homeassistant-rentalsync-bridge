<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->
# Contract: Admin Custom-Field APIs, Retired-Field Indication

**Spec**: FR-037, FR-038, SC-009 · **Research**: R10

These changes are additive and backward compatible. No field is removed, and no request shape changes.

## `GET /api/listings/{listing_id}/custom-fields`

`CustomFieldResponse` gains two fields:

| Field | Type | Meaning |
|---|---|---|
| `retired` | `bool` | `true` when `field_name` is in the active provider's `retired_description_fields` |
| `retired_reason` | `string \| null` | human-readable reason when retired |

```json
{
  "fields": [
    {"id": 7, "field_name": "money_totalPaid", "display_label": "Paid",
     "enabled": true, "sort_order": 3,
     "retired": true,
     "retired_reason": "Guesty Reservations V3 no longer includes payment totals on reservations"}
  ],
  "listing_id": 1
}
```

## `GET /api/listings/{listing_id}/available-custom-fields`

Each `AvailableFieldResponse` entry gains the same `retired` and `retired_reason` fields.

## `PUT /api/listings/{listing_id}/custom-fields`

- The request is unchanged.
- GET and PUT share `CustomFieldsResponse` (`src/api/custom_fields.py`). The PUT response therefore carries the same `retired` and `retired_reason` values, computed the same way, for every returned field.
- A retired field can still be saved, disabled or deleted by the user. It is never removed automatically (FR-038).

## Source of truth

- The retired set comes from a static lookup on the credential's `pms_type` and `get_settings().guesty_reservations_api`. No network call and no provider instance are needed.
- The retired set is empty for Cloudbeds and for Guesty in legacy mode.

## Admin UI (`src/static/js/admin.js`)

- A retired field shows a "Retired" badge next to its label.
- The tooltip or title text is `retired_reason`.
- The field stays editable.

## Log notice

- Logged once per process, at the first V3 sync.
- One WARNING per retired field that is enabled on at least one listing:

  `Description field 'money_totalPaid' is no longer supplied by Guesty Reservations V3 and will render empty; enabled on listings: <ids>. Remove or disable it in the admin UI.`

## Tests

`tests/integration/test_custom_fields_api.py`:

- the flag is set in V3 mode and absent in legacy mode and for Cloudbeds
- `PUT` round-trips a retired field, and its response carries `retired: true` and the reason

A unit test covers the one-time log notice.
