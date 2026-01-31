# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""FastAPI application entry point for RentalSync Bridge."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api import health, ical, oauth
from src.config import get_settings
from src.middleware.auth import AuthenticationMiddleware
from src.middleware.error_handler import ErrorHandlerMiddleware
from src.utils.logging import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Application lifespan handler for startup/shutdown events.

    Args:
        app: FastAPI application instance.

    Yields:
        None during application runtime.
    """
    # Startup
    setup_logging()

    # Store settings in app state
    app.state.settings = get_settings()

    yield

    # Shutdown
    # Add cleanup here if needed


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        Configured FastAPI application.
    """
    settings = get_settings()

    app = FastAPI(
        title="RentalSync Bridge",
        description="Cloudbeds to Airbnb iCal export bridge for Home Assistant",
        version="0.1.0",
        docs_url="/docs" if settings.standalone_mode else None,
        redoc_url="/redoc" if settings.standalone_mode else None,
        lifespan=lifespan,
    )

    # Add middleware (order matters - first added is outermost)
    app.add_middleware(ErrorHandlerMiddleware)
    app.add_middleware(AuthenticationMiddleware)

    # CORS for iCal endpoints (calendar apps need access)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    # Include routers
    app.include_router(health.router)
    app.include_router(ical.router)
    app.include_router(oauth.router)

    return app


# Application instance
app = create_app()
