<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Home Assistant Add-on Setup Guide

This guide covers installing and configuring RentalSync Bridge as a Home
Assistant add-on.

## Prerequisites

- Home Assistant OS or Supervised installation
- Add-on repository access
- Cloudbeds API credentials (API key or OAuth)

## Installation

### 1. Add the Repository

1. Navigate to **Settings** → **Add-ons** → **Add-on Store**
2. Click the menu (⋮) in the top right and select **Repositories**
3. Add the repository URL: `https://github.com/tykeal/homeassistant-rentalsync-bridge`
4. Click **Add** and then **Close**

### 2. Install the Add-on

1. Find "RentalSync Bridge" in the add-on store
2. Click **Install**
3. Wait for installation to complete

### 3. Configure the Add-on

Navigate to the add-on's **Configuration** tab and set:

```yaml
cloudbeds_api_key: "your-api-key-here"
timezone: "America/New_York"
log_level: info
```

#### Configuration Options

| Option | Description | Default |
| -------- | ------------- | --------- |
| `cloudbeds_api_key` | Your Cloudbeds API key | Required |
| `timezone` | IANA timezone identifier | `UTC` |
| `log_level` | Logging verbosity | `info` |
| `guesty_reservations_api` | Guesty API used to read reservations: `v3` or `legacy` | `v3` |

The `log_level` option accepts `debug`, `info`, `warning`, `error` or
`critical` and is passed to the application as the `LOG_LEVEL` environment
variable. Set it to `debug` when gathering diagnostics (for example, to see how
many reservations the PMS returned for each listing); any unrecognised value
falls back to `info`.

##### What `debug` does and does not log

`debug` adds application diagnostics only: reservation identifiers, booking
statuses, check-in/check-out dates, room identifiers and sync counts. Guest
names, email addresses and phone numbers are not emitted by these diagnostics,
and SQL statements, database driver activity and HTTP client traffic stay
suppressed at `debug`, so the add-on log does not echo stored guest records or
API credentials.

This is a best-effort guarantee covering the add-on's own logging. Logs can
still contain property names, listing slugs and provider error messages
returned verbatim by your PMS. Always skim a log before pasting it into a
public GitHub issue and redact anything you would not want published.

#### Guesty Reservations API

Guesty retires its legacy reservations API (`/v1/reservations`) on
2027-03-01. Upgrade the add-on before that date. The
`guesty_reservations_api` option selects which API the add-on reads
reservations from; Cloudbeds ignores it.

- `v3` (default) uses Guesty Reservations V3: one combined, listing-filtered
  search per sync cycle, plus a by-IDs lookup only when a stored reservation
  disappears from the search. A reservation that changes room or spans
  several units of a multi-unit property is exported as one event per room
  interval, in each room's own feed.
- `legacy` restores the previous behaviour. It is for rollback only and is
  removed in a release after 2027-03-01.

The startup log shows the selected value, for example
`Guesty reservations API: v3`. Changing the option takes effect when the
add-on restarts; there is no database change in either direction.

V3 also lowers the Guesty request budget. Measured against a synthetic
reference account, the first sync cycle used 122 requests instead of 237,
and each later cycle 17 instead of 31. Every cycle logs its request
counts by resource at INFO, for example
`Guesty requests this cycle: search=1 by_ids=0 guests=2 custom_fields=0`.

##### Retired payment description fields

Reservations V3 no longer carries payment totals, so the `money_totalPaid`,
`money_balanceDue` and `money_currency` description fields render empty
on `v3`. Their configuration is kept, and switching back to `legacy`
renders them again. The admin UI marks them with a **Retired** badge, and
the add-on logs one WARNING per enabled retired field, once per add-on
start, naming the listings that use it. Remove or disable these fields to
silence the warning.

##### Validating V3 before the stable release (pre-release)

On a pre-release, check V3 against your own account before relying on it:

1. Set `log_level: debug`, keep `guesty_reservations_api: v3` and restart.
2. Let three or more sync cycles run, then check the log and feeds:
   - **Identity.** The first sync's INFO line reports that every stored
     reservation ID was matched, and no identity-mismatch error appears.
   - **Feeds.** For every single-segment reservation, the event UID,
     `SUMMARY` and `Booking ID:` line match the last legacy feed. Save
     copies of each feed before upgrading and compare them with `diff`.
     Multi-segment reservations are expected to differ: V3 adds
     room-specific and `#n` events that legacy never produced.
   - **Codes.** Door codes are unchanged for every current and upcoming
     stay.
   - **Multi-segment stays.** Each room interval's event appears in exactly
     that room's feed, with the room and dates assigned in Guesty.
   - **Health.** No completeness-guard or rate-limit warnings appear, and
     the per-cycle request counts stay at or below the legacy level.
3. Report the results, or anything unexpected, on
   [issue #145](https://github.com/tykeal/homeassistant-rentalsync-bridge/issues/145).

At `debug`, V3 logs the search's filter-ID and chunk counts, each page's
`hasMore`, status and segment-count histograms, every reservation's
segment listing IDs, per-listing counts and the outcome of each absence
check. Like the other `debug` diagnostics, these lines carry identifiers,
counts and statuses only, never guest details, door codes or notes.

##### Rolling back to legacy

1. On the **Configuration** tab set **Guesty reservations API** to
   `legacy`, then restart the add-on. No add-on downgrade is needed.
2. Single-segment bookings keep their UIDs.
3. A multi-segment reservation collapses back to one booking in its first
   unit; its extra `#n` room intervals are retired at the next sync.
4. Legacy works only until Guesty retires `/v1/reservations` on
   2027-03-01. After that, legacy syncs fail with an error naming the HTTP
   status and asking you to switch to `v3`; the last good feeds are kept.

##### Known V3 limitations

- Guesty's listing filter matches only a reservation's first stay segment.
  A reservation that starts in a property the add-on does not track, and
  was never captured before, is not discovered even when a later segment
  moves into a tracked listing. The legacy API has the same gap. The
  `debug` log lists every reservation's segment listing IDs so you can
  spot it.
- If Guesty re-arranges a stored reservation so it starts in an untracked
  property, the add-on keeps its existing room bookings through the by-IDs
  lookup and logs the re-attribution at INFO. A tracked room the
  reservation occupies for the first time is logged rather than published.
- A guest record that Guesty keeps answering with HTTP 404 costs one
  lookup on every sync cycle.

### 4. Start the Add-on

1. Click **Start**
2. Check the **Log** tab for any startup errors
3. Once started, click **Open Web UI** to access the admin interface

## Configuring Cloudbeds Integration

### Option A: API Key Authentication (Recommended)

1. Log in to your Cloudbeds account
2. Navigate to **Settings** → **API Keys**
3. Create a new API key with read access to reservations
4. Copy the API key to the add-on configuration

### Option B: OAuth Authentication

1. In the RentalSync Bridge web UI, click **Configure OAuth**
2. Enter your Cloudbeds OAuth Client ID and Secret
3. Click **Authorize** and complete the OAuth flow
4. The add-on will automatically refresh tokens

## Syncing Properties

1. Open the RentalSync Bridge web UI
2. Click **Sync Properties from Cloudbeds**
3. Review the synced properties
4. Enable the properties you want to export to iCal
5. Click **Sync Now** to fetch bookings

## Using iCal Feeds

Once configured, each listing has a unique iCal URL:

```text
http://<homeassistant-ip>:8099/ical/<slug>.ics
```

### Adding to External Calendars

**Airbnb:**

1. Go to your Airbnb hosting calendar
2. Click **Availability Settings** → **Connect Calendars**
3. Paste the iCal URL and import

**Google Calendar:**

1. Open Google Calendar settings
2. Click **Add Calendar** → **From URL**
3. Paste the iCal URL

**Apple Calendar:**

1. File → New Calendar Subscription
2. Paste the iCal URL

## Troubleshooting

### Add-on Won't Start

Check the logs for error messages:

- Database permission issues
- Port conflicts (default 8099)
- Invalid configuration

### Guesty Sync Fails After 2027-03-01

If the log reports that the Guesty legacy reservations API returned HTTP
404 or 410, set `guesty_reservations_api` to `v3` and restart the add-on.

### No Properties Syncing

- Verify API key is valid
- Check Cloudbeds account has properties
- Review API error messages in logs

### iCal Feed Returns 404

- Ensure listing is enabled
- Verify the URL slug is correct
- Check if sync has completed

## Support

For issues and feature requests, visit the GitHub repository:
<https://github.com/tykeal/rentalsync-bridge>
