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
- Web-based administrative interface
- Home Assistant addon integration with Ingress authentication
- Multi-listing support with independent configurations
- Configurable sync intervals (1-60 minutes)
- Custom field selection for event descriptions

## Quick Start

See [specs/001-cloudbeds-ical-export/quickstart.md](specs/001-cloudbeds-ical-export/quickstart.md)
for standalone deployment instructions.

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
```

## License

Apache-2.0 - See [LICENSE](LICENSE) for details.
