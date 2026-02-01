<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart Guide: RentalSync Bridge Standalone Mode

**Feature**: 001-cloudbeds-ical-export
**Date**: 2025-01-24
**Audience**: Developers and testers

## Overview

This guide covers running RentalSync Bridge in **standalone mode** using Podman for local testing and development. Standalone mode bypasses Home Assistant authentication, making it ideal for development without a full HA installation.

---

## Prerequisites

### Required Software

- **Podman** (v4.0+) or Docker
- **Python** 3.13 or 3.14 (for local development without containers)
- **uv** package manager (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

### Optional

- **Git** (for cloning the repository)
- **curl** or **Postman** (for API testing)

---

## Quick Start with Podman

### 1. Build the Container Image

```bash
# Clone the repository
git clone https://github.com/yourusername/rentalsync-bridge.git
cd rentalsync-bridge

# Build the container image
podman build -t rentalsync-bridge:latest .
```

### 2. Create a Data Directory

```bash
# Create persistent storage for database and configuration
mkdir -p ~/rentalsync-data

# Ensure proper permissions (UID 1000 is used inside container)
# This is needed for rootless Podman to work correctly
chmod 777 ~/rentalsync-data
```

### 3. Run the Container

```bash
# Generate an encryption key first
ENCRYPTION_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

podman run -d \
  --name rentalsync-bridge \
  -p 8099:8099 \
  -v ~/rentalsync-data:/data:Z \
  -e STANDALONE_MODE=true \
  -e DATABASE_URL=sqlite:////data/rentalsync.db \
  -e ENCRYPTION_KEY="$ENCRYPTION_KEY" \
  rentalsync-bridge:latest
```

**Environment Variables Explained**:
- `STANDALONE_MODE=true` - Disables Home Assistant authentication requirement
- `DATABASE_URL` - SQLite database path (mounted volume)
- `ENCRYPTION_KEY` - Required for encrypting API keys/OAuth tokens stored in the database

> **Note**: The encryption key is required in both standalone and Home Assistant modes.
> For Home Assistant addon deployments, the key is auto-generated on first run.
> Cloudbeds credentials are configured through the Admin UI after startup.

### 4. Verify the Container is Running

```bash
# Check container status
podman ps

# View logs
podman logs -f rentalsync-bridge

# Test health endpoint
curl http://127.0.0.1:8099/health
```

Expected response:
```json
{
  "status": "healthy",
  "version": "0.1.0"
}
```

### 5. Access the Admin UI

Open your browser and navigate to:
```
http://127.0.0.1:8099/
```

You should see the RentalSync Bridge admin interface. In standalone mode, no authentication is required.

---

## Local Development Setup (Without Containers)

### 1. Install Dependencies with uv

```bash
# Navigate to the project directory
cd rentalsync-bridge

# Create virtual environment and install dependencies
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install project dependencies
uv sync
```

### 2. Configure Environment Variables

Create a `.env` file in the project root:

```bash
# .env
STANDALONE_MODE=true
DATABASE_URL=sqlite:///./data/rentalsync.db
ENCRYPTION_KEY=your-32-byte-fernet-key-here
CLOUDBEDS_CLIENT_ID=your_client_id
CLOUDBEDS_CLIENT_SECRET=your_client_secret
SYNC_INTERVAL_MINUTES=5
LOG_LEVEL=DEBUG
```

**Generate Encryption Key**:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 3. Initialize the Database

```bash
# Run Alembic migrations to create database schema
uv run alembic upgrade head
```

### 4. Start the Development Server

```bash
# Run with hot reload for development
uv run uvicorn src.main:app --reload --host 0.0.0.0 --port 8099
```

### 5. Verify the Server is Running

```bash
# Test health endpoint
curl http://127.0.0.1:8099/health

# View API documentation
open http://127.0.0.1:8099/api/docs
```

---

## Getting Cloudbeds API Credentials

### Step 1: Create a Cloudbeds Account

If you don't have a Cloudbeds account, sign up at https://www.cloudbeds.com/

### Step 2: Register an API Application

1. Log in to Cloudbeds
2. Navigate to **Settings** → **Integrations** → **API Access**
3. Click **Create New Application**
4. Fill in application details:
   - **Name**: RentalSync Bridge
   - **Redirect URI**: `http://127.0.0.1:8099/oauth/callback` (for standalone testing)
5. Click **Save**
6. Copy the **Client ID** and **Client Secret**

### Step 3: Configure OAuth Credentials

**Using Admin UI** (preferred):
1. Navigate to http://127.0.0.1:8099/
2. Go to **Settings** → **OAuth Configuration**
3. Enter your Client ID and Client Secret
4. Click **Save and Authorize**
5. Complete OAuth authorization flow with Cloudbeds

**Using API** (for automation):
```bash
curl -X POST http://127.0.0.1:8099/api/oauth/configure \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": "your_client_id",
    "client_secret": "your_client_secret"
  }'
```

---

## Testing the Application

### 1. Enable a Listing for Export

```bash
# Get all available listings
curl http://127.0.0.1:8099/api/listings

# Enable a specific listing
curl -X POST http://127.0.0.1:8099/api/listings/1/enable \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "sync_enabled": true,
    "custom_fields": [
      {
        "field_name": "guest_phone_last4",
        "display_label": "Phone (Last 4)",
        "enabled": true,
        "sort_order": 0
      },
      {
        "field_name": "booking_notes",
        "display_label": "Special Requests",
        "enabled": true,
        "sort_order": 1
      }
    ]
  }'
```

### 2. Trigger Manual Sync

```bash
# Sync a specific listing
curl -X POST http://127.0.0.1:8099/api/listings/1/sync
```

### 3. Retrieve iCal Feed

```bash
# Get iCal URL from listing details
curl http://127.0.0.1:8099/api/listings/1 | jq -r '.ical_url'

# Download the iCal file
curl http://127.0.0.1:8099/ical/your-listing-slug.ics -o calendar.ics

# Verify iCal format
cat calendar.ics
```

### 4. Import iCal into Google Calendar

1. Copy the iCal URL: `http://127.0.0.1:8099/ical/your-listing-slug.ics`
2. Note: **Local URLs won't work with Google Calendar**. Use a service like ngrok for testing:
   ```bash
   # Install ngrok: https://ngrok.com/
   ngrok http 8099
   # Use the ngrok URL instead: https://abc123.ngrok.io/ical/your-listing-slug.ics
   ```
3. In Google Calendar: **Settings** → **Add Calendar** → **From URL**
4. Paste the ngrok URL and click **Add Calendar**

---

## Running Tests

### Unit Tests

```bash
# Run all unit tests
uv run pytest tests/unit/ -v

# Run with coverage
uv run pytest tests/unit/ --cov=src --cov-report=html
```

### Integration Tests

```bash
# Run integration tests (requires database)
uv run pytest tests/integration/ -v

# Run specific test file
uv run pytest tests/integration/test_api.py -v
```

### Contract Tests

```bash
# Run contract tests against OpenAPI spec
uv run pytest tests/contract/ -v
```

---

## Troubleshooting

### Issue: Container fails to start

**Check logs**:
```bash
podman logs rentalsync-bridge
```

**Common causes**:
- Missing environment variables (CLOUDBEDS_CLIENT_ID, CLOUDBEDS_CLIENT_SECRET)
- Invalid DATABASE_URL path
- Port 8000 already in use (change `-p 8100:8099`)

**Solution**:
```bash
# Stop and remove container
podman stop rentalsync-bridge
podman rm rentalsync-bridge

# Verify environment variables
podman run --rm \
  -e STANDALONE_MODE=true \
  rentalsync-bridge:latest env | grep STANDALONE_MODE

# Restart with correct variables
```

---

### Issue: Database migration fails

**Error**: `alembic.util.exc.CommandError: Can't locate revision identified by 'head'`

**Solution**:
```bash
# Remove existing database
rm -f data/rentalsync.db

# Reinitialize database
uv run alembic upgrade head
```

---

### Issue: OAuth authorization fails

**Error**: `Invalid client_id or client_secret`

**Solution**:
1. Verify credentials in Cloudbeds dashboard
2. Check for trailing spaces or special characters
3. Re-enter credentials via admin UI
4. Review OAuth configuration:
   ```bash
   curl http://127.0.0.1:8099/api/oauth/status
   ```

---

### Issue: iCal feed returns 404

**Error**: `Calendar not found or not enabled`

**Possible causes**:
- Listing not enabled (`enabled=false`)
- Incorrect iCal URL slug
- No bookings synced yet

**Solution**:
```bash
# Check listing status
curl http://127.0.0.1:8099/api/listings/1

# Verify enabled status
# Expected: "enabled": true

# Trigger manual sync
curl -X POST http://127.0.0.1:8099/api/listings/1/sync

# Wait 10 seconds, then retry iCal URL
curl http://127.0.0.1:8099/ical/your-listing-slug.ics
```

---

### Issue: Background sync not running

**Symptoms**: `last_sync_at` not updating

**Check scheduler status**:
```bash
# View application logs
podman logs -f rentalsync-bridge | grep "Background sync"

# Expected output:
# [INFO] Background sync scheduler started
# [INFO] Syncing listing 'Downtown Loft' (ID: 1)
```

**Solution**:
1. Verify `SYNC_INTERVAL_MINUTES` environment variable
2. Check OAuth token validity:
   ```bash
   curl http://127.0.0.1:8099/api/oauth/status
   ```
3. Restart container if scheduler is stuck

---

## Advanced Configuration

### Custom Sync Interval

Adjust sync frequency via environment variable:

```bash
# Sync every 1 minute (aggressive, use for testing)
-e SYNC_INTERVAL_MINUTES=1

# Sync every 15 minutes (conservative, reduces API calls)
-e SYNC_INTERVAL_MINUTES=15
```

### Persistent Logs

Mount a volume for logs:

```bash
podman run -d \
  --name rentalsync-bridge \
  -p 8099:8099 \
  -v ~/rentalsync-data:/data:Z \
  -v ~/rentalsync-logs:/logs:Z \
  -e STANDALONE_MODE=true \
  -e LOG_FILE=/logs/rentalsync.log \
  rentalsync-bridge:latest
```

### Database Backup

```bash
# Backup SQLite database
podman exec rentalsync-bridge sqlite3 /data/rentalsync.db ".backup /data/backup.db"

# Copy backup to host
podman cp rentalsync-bridge:/data/backup.db ~/rentalsync-backup-$(date +%Y%m%d).db
```

---

## Stopping and Cleaning Up

### Stop the Container

```bash
podman stop rentalsync-bridge
```

### Remove the Container

```bash
podman rm rentalsync-bridge
```

### Clean Up Data (Optional)

```bash
# Remove persistent data (WARNING: deletes all configuration and bookings)
rm -rf ~/rentalsync-data
```

---

## Next Steps

1. **Read the API documentation**: http://127.0.0.1:8099/api/docs
2. **Review the data model**: `specs/001-cloudbeds-ical-export/data-model.md`
3. **Explore integration tests**: `tests/integration/`
4. **Deploy to Home Assistant**: See `docs/homeassistant-addon-setup.md`

---

## Getting Help

- **GitHub Issues**: https://github.com/yourusername/rentalsync-bridge/issues
- **Documentation**: `docs/` directory in the repository
- **Logs**: Check container logs for detailed error messages

---

## Security Notes for Standalone Mode

⚠️ **IMPORTANT**: Standalone mode disables authentication and should **NEVER** be used in production or exposed to the public internet.

- Use standalone mode only for local development and testing
- When exposing to the internet (e.g., ngrok), use strong firewall rules
- Transition to Home Assistant addon mode for production deployment
- Never commit `.env` files with real credentials to version control

---

## Example .env File

```bash
# RentalSync Bridge Configuration
# DO NOT COMMIT THIS FILE TO VERSION CONTROL

# Standalone mode (true for local testing, false for HA addon)
STANDALONE_MODE=true

# Database
DATABASE_URL=sqlite:///./data/rentalsync.db

# Encryption (generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
ENCRYPTION_KEY=your-generated-fernet-key-here

# Cloudbeds API Credentials
CLOUDBEDS_CLIENT_ID=your_cloudbeds_client_id
CLOUDBEDS_CLIENT_SECRET=your_cloudbeds_client_secret

# Sync Configuration
SYNC_INTERVAL_MINUTES=5

# Logging
LOG_LEVEL=INFO  # DEBUG for development, INFO for production
LOG_FILE=./logs/rentalsync.log

# Server Configuration
HOST=0.0.0.0
PORT=8099
```

---

## License

This project is licensed under the Apache-2.0 License. See LICENSE file for details.
