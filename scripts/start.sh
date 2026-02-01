#!/bin/bash
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
#
# Startup script for RentalSync Bridge container
# Runs database migrations before starting the application

set -e

# Ensure data directory exists and is writable
DATA_DIR="/data"
if [ ! -d "$DATA_DIR" ]; then
    echo "Creating data directory..."
    mkdir -p "$DATA_DIR"
fi

if [ ! -w "$DATA_DIR" ]; then
    echo "ERROR: Data directory $DATA_DIR is not writable"
    echo "Please ensure the volume mount has correct permissions"
    exit 1
fi

echo "Data directory: $DATA_DIR (writable: yes)"

echo "Running database migrations..."
alembic upgrade head

echo "Starting RentalSync Bridge..."
exec uvicorn src.main:app --host 0.0.0.0 --port 8099
