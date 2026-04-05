<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# RentalSync Bridge

Multi-PMS booking sync and iCal export bridge for Home Assistant.

## Overview

RentalSync Bridge syncs booking data from property management systems (PMS) and
exports it as RFC 5545 compliant iCal feeds. It provides a web-based admin
interface for managing PMS connections, selecting which properties and rooms to
export, and serves publicly accessible iCal URLs for calendar subscription.

## Supported PMS Providers

| Provider | Auth Method | Notes |
|----------|------------|-------|
| **Cloudbeds** | OAuth 2.0 or API Key | Full support for listings, rooms, bookings, and guest data |
| **Guesty** | OAuth 2.0 (client credentials) | Token rate limiting (5 requests/24h), custom fields via v3 API |

The provider abstraction layer makes it straightforward to add additional PMS
backends in the future.

## Features

- **Multi-PMS support** — Cloudbeds and Guesty with a pluggable provider
  architecture
- **Room-level iCal feeds** — individual calendar URLs per room for multi-unit
  properties
- **RFC 5545 compliant** iCal output compatible with Airbnb, Google Calendar,
  and other OTAs
- **Web admin interface** — PMS selection, dynamic credential forms,
  property/room management, custom field picker
- **Home Assistant add-on** with Ingress authentication and sidebar integration
- **Automatic background sync** with configurable intervals (1–60 minutes)
- **Custom field discovery** — automatically detects available fields from
  synced data (guest email, phone, notes, financial data, etc.)
- **Guest data enrichment** — resolves guest details (name, phone, email) from
  separate API endpoints when needed
- **Privacy-focused** — only phone last 4 digits stored on bookings by default;
  full phone and email are optional custom fields
- **Token management** — encrypted credential storage, automatic token refresh,
  Guesty token request rate limiting with 24-hour window tracking
- **Database migrations** — Alembic-managed schema evolution for safe upgrades

## Documentation

- [Quick Start](specs/001-cloudbeds-ical-export/quickstart.md) — Standalone
  deployment
- [Home Assistant Add-on Setup](docs/homeassistant-addon-setup.md) — HA
  installation guide
- [API Usage](docs/api-usage.md) — REST API reference
- [Deployment Guide](docs/deployment.md) — Production deployment and HTTPS

## Room-Level Calendars

RentalSync Bridge exports **room-level** iCal feeds for properties with
multiple rooms or units. Each room gets its own calendar URL, allowing you to
sync individual room availability to Airbnb and other OTAs.

### How It Works

1. **Sync Properties** — Click "Sync Properties" in the admin UI to import
   listings and rooms from your PMS
2. **Get Room URLs** — Expand a listing to see all rooms with their individual
   iCal URLs
3. **Subscribe** — Copy each room's iCal URL and add it to Airbnb, Google
   Calendar, or other calendar services
4. **Manage** — Enable/disable rooms individually and customize their URL slugs

### URL Format

Room-level iCal URLs follow the pattern:

```
/ical/{listing-slug}/{room-slug}.ics
```

Example: `/ical/beach-house/master-bedroom.ics`

**Note**: Property-level calendar URLs (`/ical/{listing-slug}.ics`) are no
longer supported. Each room must be configured separately for multi-room
properties.

## Quick Start

### Docker/Podman

```bash
# Create data directory
mkdir -p ./data

# Run with Cloudbeds (API key auth)
docker run -d \
  --name rentalsync-bridge \
  -p 8099:8099 \
  -v ./data:/data \
  -e STANDALONE_MODE=true \
  -e DATABASE_URL=sqlite:///data/rentalsync.db \
  ghcr.io/tykeal/rentalsync-bridge:latest

# Access admin UI at http://localhost:8099/admin
```

PMS credentials are configured through the admin UI — select your provider,
enter credentials, and save. Alternatively, set environment variables:

```bash
# Cloudbeds (auto-detected as default)
-e CLOUDBEDS_CLIENT_ID=your-client-id \
-e CLOUDBEDS_CLIENT_SECRET=your-secret

# Guesty (auto-detected when GUESTY_CLIENT_ID is set)
-e GUESTY_CLIENT_ID=your-client-id \
-e GUESTY_CLIENT_SECRET=your-secret

# Or explicitly set the provider type
-e PMS_TYPE=guesty
```

### Home Assistant Add-on

1. Add repository: `https://github.com/tykeal/homeassistant-rentalsync-bridge`
2. Install "RentalSync Bridge" add-on
3. Start the add-on and open the web UI from the sidebar
4. Select your PMS provider, enter credentials, and sync properties

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `PMS_TYPE` | auto-detect | PMS provider: `cloudbeds` or `guesty` |
| `DATABASE_URL` | `sqlite:///./data/rentalsync.db` | SQLite database URL |
| `SYNC_INTERVAL_MINUTES` | `5` | Background sync interval (1–60) |
| `ENCRYPTION_KEY` | (generated) | Fernet key for credential encryption |
| `STANDALONE_MODE` | `false` | Disable HA auth for standalone use |
| `ICAL_BASE_URL` | (empty) | External base URL for iCal feeds |
| `LOG_LEVEL` | `INFO` | Logging level |

PMS type is auto-detected: explicit `PMS_TYPE` wins, then `GUESTY_CLIENT_ID`
implies Guesty, otherwise defaults to Cloudbeds.

## Database Backup

The SQLite database stores all configuration and cached bookings.

### Backup Location

- **Container**: `/data/rentalsync.db`
- **Home Assistant**: `/config/addons_data/rentalsync-bridge/rentalsync.db`

### Manual Backup

```bash
# Stop for consistent backup (optional - WAL mode allows hot backup)
docker stop rentalsync-bridge

# Copy database files
cp /path/to/data/rentalsync.db ./backup/
cp /path/to/data/rentalsync.db-wal ./backup/ 2>/dev/null || true

docker start rentalsync-bridge
```

### Online Backup (No Downtime)

```bash
sqlite3 /path/to/data/rentalsync.db ".backup /backup/rentalsync-$(date +%Y%m%d).db"
```

### Restore

```bash
docker stop rentalsync-bridge
cp /backup/rentalsync-20260131.db /path/to/data/rentalsync.db
docker start rentalsync-bridge
```

## Development

### Prerequisites

- Python 3.13 or 3.14
- [uv](https://docs.astral.sh/uv/) package manager
- Pre-commit hooks

### Setup

```bash
# Install dependencies
uv sync --all-extras

# Install pre-commit hooks
uv run pre-commit install

# Run tests
cd rentalsync-bridge
uv run pytest

# Run development server
uv run uvicorn src.main:app --reload
```

### Building Container from Source

```bash
# Build standalone container (from repo root)
docker build -t rentalsync-bridge:local .

# Run the locally built container
docker run -d \
  --name rentalsync-bridge \
  -p 8099:8099 \
  -v ./data:/data \
  -e STANDALONE_MODE=true \
  rentalsync-bridge:local
```

### Project Structure

```
rentalsync-bridge/          # Add-on folder (contains all source)
├── src/                    # Application source code
│   ├── api/                # FastAPI route handlers
│   ├── middleware/          # Authentication and error handling
│   ├── models/             # SQLAlchemy ORM models
│   ├── providers/          # PMS provider abstraction layer
│   │   ├── base.py         # PMSProvider ABC and DTOs
│   │   ├── registry.py     # Provider registry and factory
│   │   ├── cloudbeds/      # Cloudbeds provider implementation
│   │   └── guesty/         # Guesty provider implementation
│   ├── repositories/       # Database access layer
│   ├── services/           # Business logic (sync, calendar, scheduler)
│   ├── static/             # CSS and JavaScript assets
│   └── templates/          # HTML templates for admin UI
├── tests/                  # Test suite (unit, integration, contract)
├── alembic/                # Database migrations
├── scripts/                # Startup scripts
├── Dockerfile              # HA add-on Dockerfile
├── config.yaml             # HA add-on configuration
└── pyproject.toml          # Python dependencies
specs/                      # Feature specifications
Dockerfile                  # Standalone/Podman Dockerfile (at repo root)
repository.json             # HA add-on repository metadata
```

## License

Apache-2.0 - See [LICENSE](LICENSE) for details.
