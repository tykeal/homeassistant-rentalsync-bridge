# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Custom fields API endpoints."""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.models.custom_field import CustomField
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

    for i, field_data in enumerate(request.fields):
        field_name = field_data.get("field_name")
        display_label = field_data.get("display_label")
        enabled = field_data.get("enabled", True)
        sort_order = field_data.get("sort_order", i)

        if not field_name or not display_label:
            continue

        result = await db.execute(
            select(CustomField).where(
                CustomField.listing_id == listing_id,
                CustomField.field_name == field_name,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.display_label = display_label
            existing.enabled = enabled
            existing.sort_order = sort_order
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
