# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""FastAPI application entry point for RentalSync Bridge."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from src.api import (
    admin,
    custom_fields,
    health,
    ical,
    listings,
    oauth,
    rooms,
    status,
)
from src.api import (
    settings as settings_api,
)
from src.config import get_settings
from src.database import get_session_factory
from src.middleware.auth import AuthenticationMiddleware
from src.middleware.error_handler import ErrorHandlerMiddleware
from src.services.calendar_service import get_calendar_cache
from src.services.scheduler import init_scheduler
from src.utils.logging import setup_logging

logger = logging.getLogger(__name__)


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

    # Initialize and start the background sync scheduler
    session_factory = get_session_factory()
    calendar_cache = get_calendar_cache()
    scheduler = init_scheduler(session_factory, calendar_cache)
    await scheduler.start()
    logger.info("Background sync scheduler started")

    yield

    # Shutdown
    scheduler.stop()
    logger.info("Background sync scheduler stopped")


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
    app.include_router(admin.router, prefix="/admin")
    app.include_router(health.router)
    app.include_router(ical.router)
    app.include_router(custom_fields.router)
    app.include_router(listings.router)
    app.include_router(oauth.router)
    app.include_router(rooms.router)
    app.include_router(settings_api.router)
    app.include_router(status.router)

    # Redirect root to admin UI
    @app.get("/", include_in_schema=False)
    async def root_redirect() -> RedirectResponse:
        """Redirect root to admin UI."""
        return RedirectResponse(url="/admin/")

    return app


# Application instance
app = create_app()
