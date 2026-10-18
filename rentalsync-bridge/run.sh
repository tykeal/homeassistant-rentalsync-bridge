#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

set -e

# Read options from Home Assistant addon config
CONFIG_PATH=/data/options.json

if [ -f "$CONFIG_PATH" ]; then
    export CLOUDBEDS_CLIENT_ID=$(jq -r '.cloudbeds_client_id // empty' "$CONFIG_PATH")
    export CLOUDBEDS_CLIENT_SECRET=$(jq -r '.cloudbeds_client_secret // empty' "$CONFIG_PATH")
    export SYNC_INTERVAL_MINUTES=$(jq -r '.sync_interval_minutes // 5' "$CONFIG_PATH")
    LOG_LEVEL=$(jq -r '.log_level // "info"' "$CONFIG_PATH")
    export LOG_LEVEL="${LOG_LEVEL^^}"
fi

# Fall back to INFO when no add-on options are present
export LOG_LEVEL="${LOG_LEVEL:-INFO}"

# Set database path for Home Assistant data persistence
export DATABASE_URL="${DATABASE_URL:-sqlite:////data/rentalsync.db}"

# Disable standalone mode when running as addon
export STANDALONE_MODE="${STANDALONE_MODE:-false}"

# Generate or load encryption key for credential storage
ENCRYPTION_KEY_FILE="/data/.encryption_key"
if [ -f "$ENCRYPTION_KEY_FILE" ]; then
    export ENCRYPTION_KEY=$(cat "$ENCRYPTION_KEY_FILE")
else
    echo "Generating new encryption key..."
    export ENCRYPTION_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
    echo "$ENCRYPTION_KEY" > "$ENCRYPTION_KEY_FILE"
    chmod 600 "$ENCRYPTION_KEY_FILE"
fi

# Run database migrations
echo "Running database migrations..."
alembic upgrade head

# Start the application
echo "Starting RentalSync Bridge..."
exec uvicorn src.main:app --host 0.0.0.0 --port 8099
