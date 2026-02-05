# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for error handling middleware."""

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from src.middleware.error_handler import (
    ErrorHandlerMiddleware,
    create_error_response,
    service_unavailable_response,
)


class TestCreateErrorResponse:
    """Tests for create_error_response function."""

    def test_creates_json_response(self):
        """Test creates a valid JSON response."""
        response = create_error_response(400, "Bad request", "validation_error")

        assert response.status_code == 400
        assert response.media_type == "application/json"

    def test_response_body_structure(self):
        """Test response body has correct structure."""
        response = create_error_response(404, "Not found", "not_found")

        # Response body is bytes, decode to check content
        body_bytes = response.body
        body = body_bytes.decode() if isinstance(body_bytes, bytes) else str(body_bytes)
        assert "Not found" in body
        assert "not_found" in body


class TestServiceUnavailableResponse:
    """Tests for service_unavailable_response function."""

    def test_creates_503_response(self):
        """Test creates 503 status response."""
        response = service_unavailable_response()

        assert response.status_code == 503

    def test_custom_message(self):
        """Test custom message is included."""
        response = service_unavailable_response("Database unavailable")

        body_bytes = response.body
        body = body_bytes.decode() if isinstance(body_bytes, bytes) else str(body_bytes)
        assert "Database unavailable" in body

    def test_retry_after_header(self):
        """Test Retry-After header is set when provided."""
        response = service_unavailable_response(retry_after=60)

        assert response.headers.get("retry-after") == "60"

    def test_no_retry_after_when_not_provided(self):
        """Test no Retry-After header when not specified."""
        response = service_unavailable_response()

        assert response.headers.get("retry-after") is None


class TestErrorHandlerMiddleware:
    """Tests for ErrorHandlerMiddleware."""

    @pytest.fixture
    def test_app(self):
        """Create a test app with error handling middleware."""
        app = FastAPI()
        app.add_middleware(ErrorHandlerMiddleware)

        @app.get("/success")
        async def success():
            return {"status": "ok"}

        @app.get("/http-error")
        async def http_error():
            raise HTTPException(status_code=400, detail="Bad request")

        @app.get("/unhandled-error")
        async def unhandled_error():
            raise RuntimeError("Something went wrong")

        return app

    @pytest.fixture
    async def test_client(self, test_app):
        """Create test client."""
        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    @pytest.mark.asyncio
    async def test_successful_request_passes_through(self, test_client):
        """Test successful requests pass through unchanged."""
        response = await test_client.get("/success")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_http_exception_passes_through(self, test_client):
        """Test HTTPExceptions are passed through to FastAPI handler."""
        response = await test_client.get("/http-error")

        assert response.status_code == 400
        assert "Bad request" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_unhandled_exception_returns_500(self, test_client):
        """Test unhandled exceptions return 500 with generic message."""
        response = await test_client.get("/unhandled-error")

        assert response.status_code == 500
        data = response.json()
        assert "internal error" in data["detail"].lower()
        assert data["type"] == "internal_error"
        # Should not expose the actual error message
        assert "Something went wrong" not in data["detail"]
