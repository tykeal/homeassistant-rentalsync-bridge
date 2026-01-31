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
3. Add the repository URL: `https://github.com/tykeal/homeassistant-addons`
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
```

#### Configuration Options

| Option | Description | Default |
|--------|-------------|---------|
| `cloudbeds_api_key` | Your Cloudbeds API key | Required |
| `timezone` | IANA timezone identifier | `UTC` |
| `log_level` | Logging verbosity (DEBUG, INFO, WARNING, ERROR) | `INFO` |

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

```
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
https://github.com/tykeal/rentalsync-bridge
