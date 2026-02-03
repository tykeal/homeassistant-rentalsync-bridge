<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# RentalSync Bridge

Cloudbeds to Airbnb iCal export bridge for Home Assistant.

## Overview

RentalSync Bridge transforms Cloudbeds booking data into Airbnb-compatible iCal
feeds. It provides an administrative web interface for configuring which
listings to export and serves publicly accessible iCal URLs for calendar
subscription.

## Features

- Export Cloudbeds bookings as RFC 5545 compliant iCal feeds
- **Room-level calendar feeds** for multi-unit properties
- Web-based administrative interface
- Home Assistant addon integration with Ingress authentication
- Multi-listing support with independent configurations
- Automatic background sync with configurable intervals
- Custom field selection for event descriptions
- Privacy-focused: only phone last 4 digits exposed

## Documentation

- [Quick Start](specs/001-cloudbeds-ical-export/quickstart.md) - Standalone
  deployment
- [Home Assistant Add-on Setup](docs/homeassistant-addon-setup.md) - HA
  installation guide
- [API Usage](docs/api-usage.md) - REST API reference
- [Deployment Guide](docs/deployment.md) - Production deployment and HTTPS

## Room-Level Calendars

RentalSync Bridge exports **room-level** iCal feeds for properties with multiple rooms or units. Each room gets its own calendar URL, allowing you to sync individual room availability to Airbnb and other OTAs.

### How It Works

1. **Sync Rooms**: Click "Sync Rooms from Cloudbeds" in the admin UI to import all rooms for your properties
2. **Get Room URLs**: Expand a listing to see all rooms with their individual iCal URLs
3. **Subscribe**: Copy each room's iCal URL and add it to Airbnb, Google Calendar, or other calendar services
4. **Manage**: Enable/disable rooms individually and customize their URL slugs

### URL Format

Room-level iCal URLs follow the pattern:
```
/ical/{listing-slug}/{room-slug}.ics
```

Example: `/ical/beach-house/master-bedroom.ics`

**Note**: Property-level calendar URLs (`/ical/{listing-slug}.ics`) are no longer supported. Each room must be configured separately for multi-room properties.

## Quick Start

### Docker/Podman

```bash
# Create data directory
mkdir -p ./data

# Run container
docker run -d \
  --name rentalsync-bridge \
  -p 8099:8099 \
  -v ./data:/data \
  -e STANDALONE_MODE=true \
  -e DATABASE_URL=sqlite:///data/rentalsync.db \
  -e CLOUDBEDS_API_KEY=your-api-key \
  ghcr.io/tykeal/rentalsync-bridge:latest

# Access admin UI at http://localhost:8099/admin
```

### Home Assistant Add-on

1. Add repository: `https://github.com/tykeal/homeassistant-addons`
2. Install "RentalSync Bridge" add-on
3. Configure API key and start
4. Access via Home Assistant sidebar

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
uv run pytest

# Run development server
uv run uvicorn src.main:app --reload
```

### Project Structure

```
src/
├── api/          # FastAPI route handlers
├── middleware/   # Authentication and error handling
├── models/       # SQLAlchemy ORM models
├── repositories/ # Database access layer
├── services/     # Business logic
└── templates/    # HTML templates for admin UI
```

## License

Apache-2.0 - See [LICENSE](LICENSE) for details.
