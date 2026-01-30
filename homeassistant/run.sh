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
fi

# Set database path for Home Assistant data persistence
export DATABASE_URL="${DATABASE_URL:-sqlite:////data/rentalsync.db}"

# Disable standalone mode when running as addon
export STANDALONE_MODE="${STANDALONE_MODE:-false}"

# Start the application
exec uvicorn src.main:app --host 0.0.0.0 --port 8099
