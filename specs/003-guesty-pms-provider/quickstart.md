<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->
# Quickstart: Guesty PMS Provider Development

**Feature Branch**: `003-guesty-pms-provider`
**Date**: 2025-07-15

## Prerequisites

- Python 3.13+
- uv package manager
- Git with pre-commit hooks configured

## Getting Started

### 1. Switch to Feature Branch

```bash
cd rentalsync-bridge
git checkout 003-guesty-pms-provider
```

### 2. Install Dependencies

```bash
cd rentalsync-bridge
uv sync --all-extras
```

### 3. Configure Environment

Create a `.env` file manually and configure for Guesty development:

```bash
touch .env
```

Edit `.env` with Guesty-specific settings:

```bash
# Database
DATABASE_URL=sqlite:///./data/rentalsync.db

# PMS Provider Selection
PMS_TYPE=guesty

# Guesty API Credentials
GUESTY_CLIENT_ID=your_guesty_client_id
GUESTY_CLIENT_SECRET=your_guesty_client_secret

# Legacy Cloudbeds vars (kept for backward compat testing)
# CLOUDBEDS_CLIENT_ID=
# CLOUDBEDS_CLIENT_SECRET=

# Sync
SYNC_INTERVAL_MINUTES=5

# Security
ENCRYPTION_KEY=your_fernet_key_here
# Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Development mode
STANDALONE_MODE=true

# Server
HOST=0.0.0.0
PORT=8099
LOG_LEVEL=DEBUG
```

### 4. Run Database Migrations

```bash
cd rentalsync-bridge
uv run alembic upgrade head
```

This will apply the `generalize_pms_columns` migration that:
- Renames `cloudbeds_id` → `pms_id` on listings
- Renames `cloudbeds_booking_id` → `pms_booking_id` on bookings
- Renames `cloudbeds_room_id` → `pms_room_id` on rooms
- Adds `pms_type`, `token_request_count`, `token_request_window_start` to oauth_credentials

### 5. Start the Development Server

```bash
cd rentalsync-bridge
uv run uvicorn src.main:app --reload --host 0.0.0.0 --port 8099
```

### 6. Access the Admin UI

Open `http://localhost:8099/admin/` in your browser.

You should see:
- PMS type selector (Cloudbeds / Guesty)
- Provider-specific credential form when Guesty is selected
- Connection test button

## Running Tests

### All Tests

```bash
cd rentalsync-bridge
uv run pytest
```

### Specific Test Categories

```bash
# Unit tests only
uv run pytest tests/unit/

# Integration tests only
uv run pytest tests/integration/

# Provider-specific tests
uv run pytest tests/unit/test_guesty_service.py
uv run pytest tests/unit/test_guesty_auth.py
uv run pytest tests/unit/test_pms_provider_base.py
uv run pytest tests/unit/test_provider_registry.py

# Migration tests
uv run pytest tests/integration/test_pms_migration.py

# Sync tests (updated for multi-provider)
uv run pytest tests/unit/test_sync_service.py
uv run pytest tests/integration/test_guesty_sync.py
```

### With Coverage

```bash
uv run pytest --cov=src --cov-report=html
```

## Testing with Mock Guesty API

Unit and integration tests mock Guesty API responses. Example test data patterns:

### Mock Listing Response

```python
GUESTY_LISTING_RESPONSE = {
    "results": [
        {
            "_id": "507f1f77bcf86cd799439011",
            "title": "Beach House Suite",
            "address": {
                "full": "123 Ocean Drive, Miami, FL"
            },
            "timezone": "America/New_York",
            "type": "single",
        }
    ],
    "count": 1,
    "limit": 100,
    "skip": 0,
}
```

### Mock Reservation Response

```python
GUESTY_RESERVATION_RESPONSE = {
    "results": [
        {
            "_id": "60a7c3f5e4b0a1234567890a",
            "listingId": "507f1f77bcf86cd799439011",
            "checkIn": "2025-07-20T14:00:00.000Z",
            "checkOut": "2025-07-25T11:00:00.000Z",
            "status": "confirmed",
            "guestId": "60a7c3f5e4b0a1234567890b",
            "nightsCount": 5,
        }
    ],
    "count": 1,
    "limit": 100,
    "skip": 0,
}
```

### Mock Guest Response

```python
GUESTY_GUEST_RESPONSE = {
    "_id": "60a7c3f5e4b0a1234567890b",
    "fullName": "Jane Doe",
    "firstName": "Jane",
    "lastName": "Doe",
    "phone": "+1-555-123-4567",
    "email": "jane.doe@example.com",
}
```

### Mock Custom Fields Response (V3)

```python
GUESTY_CUSTOM_FIELDS_RESPONSE = {
    "reservationId": "60a7c3f5e4b0a1234567890a",
    "customFields": [
        {
            "_id": "68f9fa360d5e34bd09f2ca91",
            "fieldId": "637bad36eea326005171289c",
            "value": "VIP Guest",
        },
        {
            "_id": "68f9fa360d5e34bd09f2ca92",
            "fieldId": "65d8828978f63800130b19ae",
            "value": True,
        },
    ],
}
```

## Testing Backward Compatibility

To test that existing Cloudbeds installations work after the migration:

1. Create a test database with pre-migration schema:
```bash
# Option A: Start fresh — remove existing DB and migrate to the pre-migration revision
rm -f rentalsync_bridge.db
uv run alembic upgrade <pre-migration-revision>

# Option B: If you have an existing DB, check out the pre-migration code and upgrade forward
# git checkout <pre-migration-commit> -- alembic/
# uv run alembic upgrade <pre-migration-revision>
```
> **Note**: `uv run alembic downgrade base` will NOT work because the migration's
> `downgrade()` raises `NotImplementedError` — the schema change is one-way.

2. Insert test Cloudbeds data manually or via old test fixtures

3. Run the migration:
```bash
uv run alembic upgrade head
```

4. Verify:
- All data preserved (listings, bookings, rooms)
- `pms_type` = `"cloudbeds"` on credential
- iCal feeds still work with existing slugs
- API endpoints return expected data

## Pre-Commit Hooks

All commits must pass pre-commit hooks:

```bash
# Run manually
uv run pre-commit run --all-files

# Hooks include:
# - reuse (SPDX headers)
# - ruff (linting + formatting)
# - mypy (type checking)
# - interrogate (docstring coverage, 100%)
# - yamllint
# - gitlint (commit message format)
```

## Commit Guidelines

Per AGENTS.md and the project constitution:

- **Atomic commits**: One logical change per commit
- **Subject format**: `Type(scope): description` (≤50 chars)
- **Types**: Feat, Fix, Refactor, Test, Docs, Chore, Style, Perf, Build, CI, Revert
- **Sign-off**: `git commit -s` (DCO required)
- **Co-authorship**: `Co-Authored-By: <Model> <email>` for AI-assisted commits
- **No --no-verify**: Pre-commit hooks must never be bypassed

## Key Architecture Notes

### Provider Abstraction

```text
Admin UI / API Endpoints
        │
        ▼
   Provider Registry  ←── get_provider(pms_type, credential)
        │
   ┌────┴─────┐
   │          │
   ▼          ▼
Cloudbeds   Guesty
Provider    Provider
   │          │
   ▼          ▼
Cloudbeds   Guesty
   API       API
```

### File Locations

| Component | Path |
|-----------|------|
| Provider ABC | `src/providers/base.py` |
| Provider Registry | `src/providers/registry.py` |
| Cloudbeds Provider | `src/providers/cloudbeds/service.py` |
| Guesty Provider | `src/providers/guesty/service.py` |
| Guesty Token Manager | `src/providers/guesty/auth.py` |
| Config (new env vars) | `src/config.py` |
| Migration | `alembic/versions/xxxx_generalize_pms_columns.py` |
