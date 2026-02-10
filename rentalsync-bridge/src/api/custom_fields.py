# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Custom fields API endpoints."""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.ical import get_calendar_cache
from src.database import get_db
from src.models.available_field import AvailableField
from src.models.custom_field import CustomField
from src.repositories.available_field_repository import format_allowed_fields_message
from src.repositories.custom_field_repository import CustomFieldRepository
from src.repositories.listing_repository import ListingRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/listings", tags=["Custom Fields"])


class CustomFieldResponse(BaseModel):
    """Response model for a custom field."""

    id: int = Field(description="Field ID")
    field_name: str = Field(description="Cloudbeds field name")
    display_label: str = Field(description="Display label in iCal")
    enabled: bool = Field(description="Whether field is enabled")
    sort_order: int = Field(description="Display order")


class CustomFieldsResponse(BaseModel):
    """Response model for custom field collection."""

    fields: list[CustomFieldResponse] = Field(description="List of custom fields")
    listing_id: int = Field(description="Listing ID")


class CustomFieldUpdateRequest(BaseModel):
    """Request model for updating custom fields."""

    fields: list[dict[str, Any]] = Field(
        description="Fields to update",
        examples=[
            [
                {"field_name": "guestName", "display_label": "Guest", "enabled": True},
                {"field_name": "notes", "display_label": "Notes", "enabled": False},
            ]
        ],
    )


@router.get("/{listing_id}/custom-fields", response_model=CustomFieldsResponse)
async def get_custom_fields(
    listing_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Get custom fields for a listing.

    Args:
        listing_id: Listing ID.
        db: Database session.

    Returns:
        Custom fields for the listing.

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

    result = await db.execute(
        select(CustomField)
        .where(CustomField.listing_id == listing_id)
        .order_by(CustomField.sort_order, CustomField.field_name)
    )
    fields = result.scalars().all()

    return {
        "fields": [
            {
                "id": field.id,
                "field_name": field.field_name,
                "display_label": field.display_label,
                "enabled": field.enabled,
                "sort_order": field.sort_order,
            }
            for field in fields
        ],
        "listing_id": listing_id,
    }


@router.put("/{listing_id}/custom-fields", response_model=CustomFieldsResponse)
async def update_custom_fields(
    listing_id: int,
    request: CustomFieldUpdateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Update custom fields for a listing.

    Creates new fields if they don't exist, updates existing ones.

    Args:
        listing_id: Listing ID.
        request: Fields to update.
        db: Database session.

    Returns:
        Updated custom fields.

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

    # Get valid field names for validation (dynamically discovered + built-in)
    custom_repo = CustomFieldRepository(db)
    available_fields = await custom_repo.get_available_fields_for_listing(listing_id)

    # Collect and validate field names from request BEFORE making any changes
    # Fail fast on malformed entries to prevent unintended deletions
    requested_field_names: set[str] = set()
    for i, field_data in enumerate(request.fields):
        field_name = field_data.get("field_name")
        display_label = field_data.get("display_label")

        # Reject entries missing required keys instead of silently skipping
        if not field_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Field at index {i} missing required 'field_name'",
            )
        if not display_label:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Field '{field_name}' missing required 'display_label'",
            )

        # Reject duplicate field names in the same request
        if field_name in requested_field_names:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Duplicate field_name '{field_name}' at index {i}",
            )

        # Validate field_name against available fields for this listing
        if field_name not in available_fields:
            allowed = format_allowed_fields_message(available_fields.keys())
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid field_name '{field_name}'. Must be one of: {allowed}",
            )
        requested_field_names.add(field_name)

    # Delete fields not in the request (validation passed, safe to modify)
    existing_result = await db.execute(
        select(CustomField).where(CustomField.listing_id == listing_id)
    )
    existing_fields = existing_result.scalars().all()

    # Build lookup for existing fields to avoid N+1 queries
    existing_by_name: dict[str, CustomField] = {
        f.field_name: f for f in existing_fields
    }

    for existing in existing_fields:
        if existing.field_name not in requested_field_names:
            await db.delete(existing)

    # Create or update fields using the lookup
    # All fields are validated above, so we can safely process all entries
    for i, field_data in enumerate(request.fields):
        field_name = field_data["field_name"]
        display_label = field_data["display_label"]
        enabled = field_data.get("enabled", True)
        sort_order = field_data.get("sort_order", i)

        existing_field = existing_by_name.get(field_name)

        if existing_field:
            existing_field.display_label = display_label
            existing_field.enabled = enabled
            existing_field.sort_order = sort_order
        else:
            field = CustomField(
                listing_id=listing_id,
                field_name=field_name,
                display_label=display_label,
                enabled=enabled,
                sort_order=sort_order,
            )
            db.add(field)

    await db.commit()
    logger.info("Updated custom fields for listing %s", listing_id)

    # Invalidate calendar cache for this listing (all room caches)
    if listing.ical_url_slug:
        cache = get_calendar_cache()
        cache.invalidate_prefix(listing.ical_url_slug)
        logger.debug("Invalidated calendar cache for listing %s", listing.ical_url_slug)

    result = await db.execute(
        select(CustomField)
        .where(CustomField.listing_id == listing_id)
        .order_by(CustomField.sort_order, CustomField.field_name)
    )
    fields = result.scalars().all()

    return {
        "fields": [
            {
                "id": field.id,
                "field_name": field.field_name,
                "display_label": field.display_label,
                "enabled": field.enabled,
                "sort_order": field.sort_order,
            }
            for field in fields
        ],
        "listing_id": listing_id,
    }


class AvailableFieldResponse(BaseModel):
    """Response model for an available custom field."""

    field_key: str = Field(description="Field key from Cloudbeds")
    display_name: str = Field(description="Human-readable display name")
    sample_value: str | None = Field(description="Sample value from last sync")
    source: str = Field(
        description="Field source: 'default', 'discovered', or 'builtin'"
    )


class AvailableCustomFieldsResponse(BaseModel):
    """Response model for available custom fields."""

    available_fields: list[AvailableFieldResponse] = Field(
        description="List of available fields"
    )
    listing_id: int = Field(description="Listing ID")


@router.get(
    "/{listing_id}/available-custom-fields",
    response_model=AvailableCustomFieldsResponse,
)
async def get_available_custom_fields(
    listing_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Get available custom fields that can be configured for a listing.

    Returns dynamically discovered fields from Cloudbeds sync plus built-in
    fields. Fields are discovered during sync from actual reservation data.

    Args:
        listing_id: Listing ID.
        db: Database session.

    Returns:
        List of available custom fields with their display names and sample values.

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

    # Get dynamically discovered fields
    result = await db.execute(
        select(AvailableField)
        .where(AvailableField.listing_id == listing_id)
        .order_by(AvailableField.display_name)
    )
    discovered_fields = result.scalars().all()
    discovered_keys = {f.field_key for f in discovered_fields}

    # Start with default Cloudbeds fields (always available)
    defaults = CustomFieldRepository.get_default_cloudbeds_fields()
    available: list[dict[str, Any]] = [
        {
            "field_key": key,
            "display_name": name,
            "sample_value": None,
            "source": "default",
        }
        for key, name in defaults.items()
        if key not in discovered_keys  # Don't duplicate discovered fields
    ]

    # Add discovered fields (may override defaults with sample values)
    for f in discovered_fields:
        available.append(
            {
                "field_key": f.field_key,
                "display_name": f.display_name,
                "sample_value": f.sample_value,
                "source": "discovered",
            }
        )

    # Add built-in computed fields
    builtin = CustomFieldRepository.get_builtin_fields()
    existing_keys = {f["field_key"] for f in available}
    for key, name in builtin.items():
        if key not in existing_keys:
            available.append(
                {
                    "field_key": key,
                    "display_name": name,
                    "sample_value": None,
                    "source": "builtin",
                }
            )

    # Sort by display name
    available.sort(key=lambda x: x["display_name"])

    return {
        "available_fields": available,
        "listing_id": listing_id,
    }
