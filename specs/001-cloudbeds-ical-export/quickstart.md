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
podman run -d \
  --name rentalsync-bridge \
  -p 8099:8099 \
  -v ~/rentalsync-data:/data:Z \
  -e STANDALONE_MODE=true \
  rentalsync-bridge:latest
```

**Environment Variables Explained**:
- `STANDALONE_MODE=true` - Disables Home Assistant authentication requirement

> **Note**: The encryption key and database are auto-configured on first run.
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

### 1. Sync Properties and Rooms from Cloudbeds

```bash
# Sync all properties and their rooms
curl -X POST http://127.0.0.1:8099/api/listings/sync-properties
```

**Expected Response:**
```json
{
  "message": "Synced 3 properties from Cloudbeds",
  "synced_count": 3
}
```

This command fetches:
- All properties from your Cloudbeds account
- All rooms for each property
- Creates/updates listing and room records in the database

### 2. View Rooms for a Listing

```bash
# Get all rooms for a specific listing
curl http://127.0.0.1:8099/api/listings/1/rooms | jq
```

**Expected Response:**
```json
{
  "rooms": [
    {
      "id": 1,
      "listing_id": 1,
      "cloudbeds_room_id": "12345",
      "room_name": "Master Bedroom",
      "room_type_name": "Deluxe King",
      "ical_url_slug": "master-bedroom",
      "enabled": true,
      "created_at": "2026-01-31T10:00:00Z",
      "updated_at": "2026-01-31T10:00:00Z"
    },
    {
      "id": 2,
      "listing_id": 1,
      "cloudbeds_room_id": "12346",
      "room_name": "Guest Room",
      "room_type_name": "Queen",
      "ical_url_slug": "guest-room",
      "enabled": true,
      "created_at": "2026-01-31T10:00:00Z",
      "updated_at": "2026-01-31T10:00:00Z"
    }
  ]
}
```

### 3. Configure Room Settings (Optional)

```bash
# Disable a room from iCal export
curl -X PATCH http://127.0.0.1:8099/api/rooms/1 \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'

# Customize room URL slug
curl -X PATCH http://127.0.0.1:8099/api/rooms/1 \
  -H "Content-Type: application/json" \
  -d '{"ical_url_slug": "master-suite"}'
```

### 4. Enable a Listing for Export

```bash
# Get all available listings
curl http://127.0.0.1:8099/api/listings

# Enable a specific listing
curl -X PUT http://127.0.0.1:8099/api/listings/1 \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "sync_enabled": true
  }'
```

**Note**: Custom fields are configured separately through the `/api/listings/{id}/custom-fields` endpoint or the admin UI.

### 5. Trigger Manual Sync

```bash
# Sync bookings for a specific listing
curl -X POST http://127.0.0.1:8099/api/listings/1/sync
```

### 6. Retrieve Room iCal Feeds

```bash
# Get listing details with rooms
curl http://127.0.0.1:8099/api/listings/1 | jq

# Download iCal feed for a specific room
curl http://127.0.0.1:8099/ical/beach-house/master-bedroom.ics -o master-bedroom.ics

# Download iCal feed for a specific room
curl http://127.0.0.1:8099/ical/beach-house/master-bedroom.ics -o master-bedroom.ics

# Verify iCal format
cat master-bedroom.ics
```

**Room-Level iCal URLs:**
- Each room has its own iCal feed
- URL format: `/ical/{listing-slug}/{room-slug}.ics`
- Only enabled rooms will have accessible iCal feeds
- Property-level URLs (e.g., `/ical/beach-house.ics`) are no longer supported

### 7. Import iCal into Airbnb or Google Calendar

### 7. Import iCal into Airbnb or Google Calendar

1. Copy the room's iCal URL: `http://127.0.0.1:8099/ical/beach-house/master-bedroom.ics`
2. **For Airbnb**:
   - Go to your Airbnb listing settings
   - Navigate to **Availability** → **Calendar Sync**
   - Click **Import Calendar**
   - Paste the iCal URL (use public URL, not localhost)
   - Name it (e.g., "Cloudbeds - Master Bedroom")
3. **For Google Calendar**:
   - Note: **Local URLs won't work with Google Calendar**. Use a service like ngrok for testing:
   ```bash
   # Install ngrok: https://ngrok.com/
   ngrok http 8099
   # Use the ngrok URL instead: https://abc123.ngrok.io/ical/beach-house/master-bedroom.ics
   ```
   - In Google Calendar: **Settings** → **Add Calendar** → **From URL**
   - Paste the ngrok URL and click **Add Calendar**

**Multi-Room Properties**:
- Each room must be added separately to your calendar service
- Use the room-specific iCal URL for each room
- Disabled rooms will not appear in calendar exports

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

**Error**: `Room not found`

**Possible causes**:
- Room not enabled (`enabled=false`)
- Listing not enabled
- Incorrect iCal URL slug (listing or room)
- No bookings synced yet for that room

**Solution**:
```bash
# Check listing and rooms status
curl http://127.0.0.1:8099/api/listings/1

# Verify room enabled status
curl http://127.0.0.1:8099/api/listings/1/rooms | jq '.rooms[] | {id, room_name, ical_url_slug, enabled}'

# Enable a specific room if needed
curl -X PATCH http://127.0.0.1:8099/api/rooms/1 \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}'

# Trigger manual sync
curl -X POST http://127.0.0.1:8099/api/listings/1/sync

# Wait 10 seconds, then retry room iCal URL
curl http://127.0.0.1:8099/ical/beach-house/master-bedroom.ics
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
