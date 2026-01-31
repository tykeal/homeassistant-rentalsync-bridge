# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Listings management API endpoints."""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.repositories.listing_repository import ListingRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/listings", tags=["Listings"])


class ListingResponse(BaseModel):
    """Response model for a listing."""

    id: int = Field(description="Listing ID")
    cloudbeds_id: str = Field(description="Cloudbeds property ID")
    name: str = Field(description="Property name")
    enabled: bool = Field(description="Whether iCal export is enabled")
    sync_enabled: bool = Field(description="Whether sync is enabled")
    ical_url_slug: str | None = Field(default=None, description="iCal URL slug")
    timezone: str | None = Field(default=None, description="Property timezone")


class ListingsResponse(BaseModel):
    """Response model for listing collection."""

    listings: list[ListingResponse] = Field(description="List of properties")
    total: int = Field(description="Total count")


class ListingUpdateRequest(BaseModel):
    """Request model for updating a listing."""

    name: str | None = Field(default=None, description="Property name")
    enabled: bool | None = Field(default=None, description="Enable iCal export")
    sync_enabled: bool | None = Field(default=None, description="Enable sync")
    timezone: str | None = Field(default=None, description="Property timezone")
    ical_url_slug: str | None = Field(default=None, description="Custom iCal URL slug")


class EnableResponse(BaseModel):
    """Response model for enable operation."""

    success: bool = Field(description="Whether operation succeeded")
    ical_url: str = Field(description="iCal URL for the listing")
    message: str = Field(description="Status message")


def _listing_to_response(listing: Any) -> dict[str, Any]:
    """Convert listing model to response dict."""
    return {
        "id": listing.id,
        "cloudbeds_id": listing.cloudbeds_id,
        "name": listing.name,
        "enabled": listing.enabled,
        "sync_enabled": listing.sync_enabled,
        "ical_url_slug": listing.ical_url_slug,
        "timezone": listing.timezone,
    }


@router.get("", response_model=ListingsResponse)
async def list_listings(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Get all listings.

    Returns:
        All listings with their configuration.
    """
    repo = ListingRepository(db)
    listings = await repo.get_all()

    return {
        "listings": [_listing_to_response(listing) for listing in listings],
        "total": len(listings),
    }


@router.get("/{listing_id}", response_model=ListingResponse)
async def get_listing(
    listing_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Get a specific listing.

    Args:
        listing_id: Listing ID.
        db: Database session.

    Returns:
        Listing details.

    Raises:
        HTTPException: 404 if listing not found.
    """
    repo = ListingRepository(db)
    listing = await repo.get_by_id(listing_id)

    if not listing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Listing not found",
        )

    return _listing_to_response(listing)


@router.post("/{listing_id}/enable", response_model=EnableResponse)
async def enable_listing(
    listing_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Enable iCal export for a listing.

    Args:
        listing_id: Listing ID.
        db: Database session.

    Returns:
        Enable status and iCal URL.

    Raises:
        HTTPException: 404 if listing not found.
    """
    repo = ListingRepository(db)
    listing = await repo.get_by_id(listing_id)

    if not listing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Listing not found",
        )

    if not listing.enabled:
        listing.enabled = True
        listing.sync_enabled = True

        # Generate slug if not set
        if not listing.ical_url_slug:
            listing.ical_url_slug = await repo.generate_unique_slug(listing.name)

        await db.commit()
        await db.refresh(listing)
        logger.info("Enabled listing %s", listing.cloudbeds_id)

    return {
        "success": True,
        "ical_url": f"/ical/{listing.ical_url_slug}.ics",
        "message": "Listing enabled successfully",
    }


@router.put("/{listing_id}", response_model=ListingResponse)
async def update_listing(
    listing_id: int,
    request: ListingUpdateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Update a listing configuration.

    Args:
        listing_id: Listing ID.
        request: Update data.
        db: Database session.

    Returns:
        Updated listing.

    Raises:
        HTTPException: 404 if listing not found.
    """
    repo = ListingRepository(db)
    listing = await repo.get_by_id(listing_id)

    if not listing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Listing not found",
        )

    # Update fields if provided
    if request.name is not None:
        listing.name = request.name
    if request.enabled is not None:
        listing.enabled = request.enabled
    if request.sync_enabled is not None:
        listing.sync_enabled = request.sync_enabled
    if request.timezone is not None:
        listing.timezone = request.timezone
    if request.ical_url_slug is not None:
        # Verify slug is unique
        existing = await repo.get_by_slug(request.ical_url_slug)
        if existing and existing.id != listing_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Slug already in use",
            )
        listing.ical_url_slug = request.ical_url_slug

    await db.commit()
    await db.refresh(listing)
    logger.info("Updated listing %s", listing.cloudbeds_id)

    return _listing_to_response(listing)
