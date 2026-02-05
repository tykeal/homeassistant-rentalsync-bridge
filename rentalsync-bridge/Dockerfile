# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

# Home Assistant Add-on Dockerfile
# Uses s6-overlay for process supervision (required by HA base images)

ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.21
FROM ${BUILD_FROM}

# Install uv for dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install build dependencies
RUN apk add --no-cache \
    jq \
    gcc \
    musl-dev \
    libffi-dev

WORKDIR /app

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies only (no dev dependencies)
RUN uv sync --frozen --no-dev --no-install-project

# Copy source code
COPY src/ ./src/
COPY README.md ./

# Install the project itself
RUN uv sync --frozen --no-dev

# Copy alembic for database migrations
COPY alembic/ ./alembic/
COPY alembic.ini ./

# Copy s6-overlay service definitions
COPY rootfs /

# Ensure run script is executable
RUN chmod +x /etc/s6-overlay/s6-rc.d/rentalsync/run

# Set environment variables
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Create data directory
RUN mkdir -p /data

# Expose port
EXPOSE 8099

# No CMD needed - s6-overlay handles service startup
