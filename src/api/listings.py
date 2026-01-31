# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Listings management API endpoints."""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.models.booking import Booking
from src.models.listing import Listing
from src.models.oauth_credential import OAuthCredential
from src.repositories.listing_repository import MAX_LISTINGS, ListingRepository
from src.services.calendar_service import get_calendar_cache
from src.services.cloudbeds_service import CloudbedsService, CloudbedsServiceError
from src.services.sync_service import SyncService, SyncServiceError

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
    last_sync_at: str | None = Field(default=None, description="Last sync timestamp")
    last_sync_error: str | None = Field(default=None, description="Last sync error")
    updated_at: str | None = Field(
        default=None, description="Last configuration update timestamp"
    )


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


class BookingResponse(BaseModel):
    """Response model for a booking."""

    id: int = Field(description="Booking ID")
    cloudbeds_booking_id: str = Field(description="Cloudbeds booking ID")
    guest_name: str | None = Field(default=None, description="Guest name")
    guest_phone_last4: str | None = Field(
        default=None, description="Last 4 phone digits"
    )
    check_in_date: str = Field(description="Check-in date (ISO format)")
    check_out_date: str = Field(description="Check-out date (ISO format)")
    status: str = Field(description="Booking status")


class BookingsResponse(BaseModel):
    """Response model for bookings collection."""

    bookings: list[BookingResponse] = Field(description="List of bookings")
    total: int = Field(description="Total count")


class SyncPropertiesResponse(BaseModel):
    """Response model for sync properties operation."""

    success: bool = Field(description="Whether operation succeeded")
    created: int = Field(description="Number of new listings created")
    updated: int = Field(description="Number of existing listings updated")
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
        "last_sync_at": (
            listing.last_sync_at.isoformat() if listing.last_sync_at else None
        ),
        "last_sync_error": listing.last_sync_error,
        "updated_at": (listing.updated_at.isoformat() if listing.updated_at else None),
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


@router.post("/sync-properties", response_model=SyncPropertiesResponse)
async def sync_properties(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Sync properties from Cloudbeds to the database.

    Fetches all properties from Cloudbeds API and creates or updates
    corresponding listings in the database. This populates the listings
    that can then be enabled for iCal export.

    Returns:
        Summary of created and updated listings.

    Raises:
        HTTPException: 503 if authentication not configured or API fails.
    """
    # Get credentials
    result = await db.execute(select(OAuthCredential).limit(1))
    credential = result.scalar_one_or_none()

    # Check for either access_token or api_key
    if not credential or (not credential.access_token and not credential.api_key):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cloudbeds credentials not configured. Configure OAuth or API key.",
        )

    # Fetch properties from Cloudbeds
    try:
        service = CloudbedsService(
            access_token=credential.access_token,
            api_key=credential.api_key,
        )
        properties = await service.get_properties()
    except CloudbedsServiceError as e:
        logger.error("Failed to fetch properties from Cloudbeds: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to fetch properties from Cloudbeds: {e}",
        ) from e

    if not properties:
        return {
            "success": True,
            "created": 0,
            "updated": 0,
            "message": "No properties found in Cloudbeds account",
        }

    # Create or update listings
    repo = ListingRepository(db)
    created = 0
    updated = 0

    for prop in properties:
        cloudbeds_id = str(prop.get("propertyID", ""))
        if not cloudbeds_id:
            continue

        existing = await repo.get_by_cloudbeds_id(cloudbeds_id)

        if existing:
            # Update existing listing
            existing.name = prop.get("propertyName", existing.name)
            existing.timezone = prop.get("propertyTimezone", existing.timezone)
            updated += 1
        else:
            # Create new listing with generated slug
            name = prop.get("propertyName", f"Property {cloudbeds_id}")
            slug = await repo.generate_unique_slug(name)
            new_listing = Listing(
                cloudbeds_id=cloudbeds_id,
                name=name,
                ical_url_slug=slug,
                timezone=prop.get("propertyTimezone", "UTC"),
                enabled=False,
                sync_enabled=False,
            )
            db.add(new_listing)
            created += 1

    await db.commit()

    return {
        "success": True,
        "created": created,
        "updated": updated,
        "message": f"Synced {created + updated} properties from Cloudbeds",
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
        # Check if we've reached the maximum enabled listings
        enabled_count = await repo.count_enabled()
        if enabled_count >= MAX_LISTINGS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Maximum number of enabled listings ({MAX_LISTINGS}) reached",
            )

        listing.enabled = True
        listing.sync_enabled = True

        # Generate slug if not set
        if not listing.ical_url_slug:
            listing.ical_url_slug = await repo.generate_unique_slug(listing.name)

        try:
            await db.commit()
        except IntegrityError as e:
            await db.rollback()
            # Check if this was a MAX_LISTINGS race condition vs other integrity error
            enabled_count = await repo.count_enabled()
            if enabled_count >= MAX_LISTINGS:
                msg = f"Maximum number of enabled listings ({MAX_LISTINGS}) reached"
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=msg,
                ) from e
            # Other integrity error (e.g., slug collision)
            logger.error("Integrity error enabling listing %s: %s", listing_id, e)
            msg = "Failed to enable listing due to a data conflict."
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=msg,
            ) from e
        except Exception as e:
            await db.rollback()
            logger.error("Unexpected error enabling listing %s: %s", listing_id, e)
            msg = "Failed to enable listing due to a server error."
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=msg,
            ) from e

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


class BulkListingRequest(BaseModel):
    """Request model for bulk listing operations."""

    listing_ids: list[int] = Field(description="List of listing IDs to update")
    enabled: bool = Field(description="Enable or disable listings")


class BulkListingResponse(BaseModel):
    """Response model for bulk listing operation."""

    updated: int = Field(description="Number of listings updated")
    failed: int = Field(description="Number of listings that failed to update")
    details: list[dict[str, Any]] = Field(description="Details of each operation")


async def _process_bulk_listing(
    listing: Any,
    enabled: bool,
    repo: ListingRepository,
    generated_slugs: set[str],
    existing_slugs: set[str],
) -> tuple[bool, dict[str, Any]]:
    """Process a single listing in a bulk operation.

    Args:
        listing: Listing to process.
        enabled: Target enabled state.
        repo: Listing repository.
        generated_slugs: Set of slugs generated in this transaction.
        existing_slugs: Pre-fetched set of all existing slugs in database.

    Returns:
        Tuple of (changed, detail_dict).
    """
    changed = False
    if enabled and not listing.enabled:
        # Enable listing - generate slug if needed
        if not listing.ical_url_slug:
            # Regenerate slug until unique across both transaction and database
            # Uses same random suffix pattern as single-listing flow
            max_attempts = 100
            slug = None
            for _ in range(max_attempts):
                candidate = await repo.generate_unique_slug(listing.name)
                if candidate not in generated_slugs and candidate not in existing_slugs:
                    slug = candidate
                    break
            if slug is None:
                msg = f"Failed to generate unique slug after {max_attempts} attempts"
                raise ValueError(msg)
            generated_slugs.add(slug)
            listing.ical_url_slug = slug
        listing.enabled = True
        listing.sync_enabled = True
        changed = True
    elif not enabled and listing.enabled:
        # Disable listing
        listing.enabled = False
        listing.sync_enabled = False
        changed = True

    return changed, {
        "id": listing.id,
        "success": True,
        "enabled": listing.enabled,
        "changed": changed,
    }


@router.post(
    "/bulk",
    response_model=BulkListingResponse,
    status_code=status.HTTP_200_OK,
    responses={
        400: {"description": "Bad request"},
    },
)
async def bulk_update_listings(
    request: BulkListingRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Bulk enable or disable multiple listings.

    Args:
        request: Bulk update data with listing IDs and enabled state.
        db: Database session.

    Returns:
        Summary of bulk operation results.
    """
    repo = ListingRepository(db)
    updated = 0
    failed = 0
    details: list[dict[str, Any]] = []

    # Fetch all requested listings in a single query
    listings_map = await repo.get_by_ids(request.listing_ids)

    # Check max listings constraint if enabling
    if request.enabled:
        current_enabled = await repo.count_enabled()

        # Count how many of the requested listings are already enabled
        already_enabled = sum(
            1
            for lid in request.listing_ids
            if lid in listings_map and listings_map[lid].enabled
        )

        new_enabled = len(request.listing_ids) - already_enabled
        if current_enabled + new_enabled > MAX_LISTINGS:
            msg = f"Cannot enable: would exceed maximum of {MAX_LISTINGS} listings"
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=msg,
            )

    # Track generated slugs within this transaction to detect collisions
    # Pre-fetch all existing slugs to avoid N+1 queries during collision resolution
    generated_slugs: set[str] = set()
    existing_slugs = await repo.get_all_slugs() if request.enabled else set()

    for listing_id in request.listing_ids:
        listing = listings_map.get(listing_id)
        if not listing:
            failed += 1
            details.append(
                {"id": listing_id, "success": False, "error": "Listing not found"}
            )
            continue

        try:
            changed, detail = await _process_bulk_listing(
                listing, request.enabled, repo, generated_slugs, existing_slugs
            )
            if changed:
                updated += 1
            details.append(detail)
        except Exception as e:
            failed += 1
            details.append({"id": listing_id, "success": False, "error": str(e)})

    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.error("Bulk update commit failed: %s", e)
        msg = f"Failed to save {updated} listing(s). Please retry."
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=msg,
        ) from e

    logger.info("Bulk update: %d updated, %d failed", updated, failed)

    return {
        "updated": updated,
        "failed": failed,
        "details": details,
    }


class SyncResponse(BaseModel):
    """Response model for sync operation."""

    success: bool = Field(description="Whether sync succeeded")
    inserted: int = Field(description="Number of new bookings")
    updated: int = Field(description="Number of updated bookings")
    cancelled: int = Field(description="Number of cancelled bookings")
    message: str = Field(description="Status message")


@router.post(
    "/{listing_id}/sync",
    response_model=SyncResponse,
    status_code=status.HTTP_200_OK,
    responses={
        404: {"description": "Listing not found"},
        503: {"description": "Sync failed"},
    },
)
async def sync_listing(
    listing_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Manually trigger sync for a listing.

    Args:
        listing_id: Listing ID.
        db: Database session.

    Returns:
        Sync results with booking counts.
    """
    repo = ListingRepository(db)
    listing = await repo.get_by_id(listing_id)

    if not listing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Listing not found",
        )

    # Get OAuth credentials
    result = await db.execute(select(OAuthCredential).limit(1))
    credential = result.scalar_one_or_none()

    if not credential or (not credential.access_token and not credential.api_key):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cloudbeds credentials not configured. Configure OAuth or API key.",
        )

    # Run sync
    try:
        sync_service = SyncService(db, get_calendar_cache())
        counts = await sync_service.sync_listing(listing, credential)

        return {
            "success": True,
            "inserted": counts["inserted"],
            "updated": counts["updated"],
            "cancelled": counts["cancelled"],
            "message": "Sync completed successfully",
        }

    except SyncServiceError as e:
        logger.error("Manual sync failed for listing %s: %s", listing_id, e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Sync failed: {e}",
        ) from e


@router.get("/{listing_id}/bookings", response_model=BookingsResponse)
async def get_listing_bookings(
    listing_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Get cached bookings for a listing (debugging endpoint).

    Returns all cached bookings for the specified listing.
    Useful for debugging sync issues and verifying data.

    Args:
        listing_id: ID of the listing.
        db: Database session.

    Returns:
        List of cached bookings.

    Raises:
        HTTPException: 404 if listing not found.
    """
    repo = ListingRepository(db)
    listing = await repo.get_by_id(listing_id)

    if not listing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Listing {listing_id} not found",
        )

    # Get bookings for this listing
    result = await db.execute(
        select(Booking)
        .where(Booking.listing_id == listing_id)
        .order_by(Booking.check_in_date)
    )
    bookings = result.scalars().all()

    return {
        "bookings": [
            {
                "id": b.id,
                "cloudbeds_booking_id": b.cloudbeds_booking_id,
                "guest_name": b.guest_name,
                "guest_phone_last4": b.guest_phone_last4,
                "check_in_date": b.check_in_date.isoformat(),
                "check_out_date": b.check_out_date.isoformat(),
                "status": b.status,
            }
            for b in bookings
        ],
        "total": len(bookings),
    }
