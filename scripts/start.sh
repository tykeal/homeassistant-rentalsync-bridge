#!/bin/bash
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
#
# Startup script for RentalSync Bridge container
# Runs database migrations before starting the application

set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting RentalSync Bridge..."
exec uvicorn src.main:app --host 0.0.0.0 --port 8099
