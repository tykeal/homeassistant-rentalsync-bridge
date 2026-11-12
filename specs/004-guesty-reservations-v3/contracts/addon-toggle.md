<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->
# Contract: Reservation-Retrieval Toggle (add-on option)

**Spec**: FR-041, SC-011, US7-2 · **Research**: R11

## Add-on option (`rentalsync-bridge/config.yaml`)

```yaml
options:
  log_level: info
  guesty_reservations_api: v3
schema:
  log_level: list(debug|info|warning|error|critical)?
  guesty_reservations_api: list(v3|legacy)?
```

## Translations (`rentalsync-bridge/translations/en.yaml`, new)

```yaml
configuration:
  log_level:
    name: Log level
    description: Verbosity of the add-on log.
  guesty_reservations_api:
    name: Guesty reservations API
    description: >-
      Which Guesty API is used to read reservations. "v3" (default) is
      required after Guesty retires its legacy reservations API on
      2027-03-01. "legacy" restores the previous behaviour for rollback
      only and will be removed in a release after that date. Ignored for
      Cloudbeds.
```

The file carries an SPDX header comment, or is covered in `REUSE.toml` if the loader rejects comments. The implementer verifies this.

## Run script (`rootfs/etc/s6-overlay/s6-rc.d/rentalsync/run`)

- If `bashio::config.has_value 'guesty_reservations_api'`, export `GUESTY_RESERVATIONS_API` from the value. Otherwise export `v3`.
- Log `Guesty reservations API: <value>` at INFO.

## Settings (`src/config.py`)

`guesty_reservations_api: Literal["v3", "legacy"] = Field(default="v3", …)`

- A validator lower-cases and strips the value.
- An unknown value raises `ValueError` at startup, the same pattern as `pms_type`.
- Standalone mode reads the `GUESTY_RESERVATIONS_API` environment variable.

## Provider wiring

```text
create_provider_for_credential(credential, session)
  └─ pms_type == "guesty"
       └─ create_provider("guesty", …, reservations_api=get_settings().guesty_reservations_api)
            └─ GuestyProvider(reservations_api="v3"|"legacy")
                 ├─ "v3"     → V3ReservationSource   (reservations_v3.py)
                 └─ "legacy" → LegacyReservationSource (legacy_reservations.py)
```

## Behavioural contract

| Requirement | Guarantee |
|---|---|
| Default | `v3`, in both the pre-release and the stable release |
| Switching | takes effect at the next add-on start; no DB change in either direction |
| Identity | single-segment booking IDs, UIDs and `Booking ID:` lines are unchanged by a switch (SC-011) |
| Multi-segment on rollback | `#n` intervals are retired by the room-change prune path; the first interval keeps `{rid}::{room}` (spec FR-041) |
| Legacy after retirement | a 404/410 from `/v1/reservations` raises an error telling the user to switch to `v3`; last-good is kept (FR-010) |
| Removal | a release after 2027-03-01 deletes `legacy_reservations.py`, its tests, the `legacy` value and the branch |

## Tests

- `tests/unit/test_config.py`: default, normalisation, rejection
- `tests/unit/test_provider_registry.py`: the factory passes the value
- `tests/integration/test_guesty_v3_toggle.py`: V3 → legacy → V3 against one DB. Asserts UIDs are unchanged for single-segment reservations and that `#n` rows retire on legacy
