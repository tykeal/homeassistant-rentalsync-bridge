# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for health check endpoint."""

import pytest
from fastapi import status


class TestHealthEndpoint:
    """Tests for /health endpoint."""

    @pytest.mark.asyncio
    async def test_health_returns_200(self, client):
        """Test health endpoint returns 200 OK."""
        response = await client.get("/health")

        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.asyncio
    async def test_health_returns_healthy_status(self, client):
        """Test health endpoint returns healthy status."""
        response = await client.get("/health")
        data = response.json()

        assert data["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_health_includes_timestamp(self, client):
        """Test health endpoint includes timestamp."""
        response = await client.get("/health")
        data = response.json()

        assert "timestamp" in data
        # Should be ISO format with T separator
        assert "T" in data["timestamp"]

    @pytest.mark.asyncio
    async def test_health_includes_version(self, client):
        """Test health endpoint includes version."""
        response = await client.get("/health")
        data = response.json()

        assert "version" in data
        assert data["version"] == "0.1.0"

    @pytest.mark.asyncio
    async def test_health_no_auth_required(self, client):
        """Test health endpoint is publicly accessible."""
        # No auth headers provided
        response = await client.get("/health")

        # Should still succeed
        assert response.status_code == status.HTTP_200_OK
