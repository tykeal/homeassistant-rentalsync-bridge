<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Room-Level Calendar Export and Custom Fields UI

**Feature ID**: 002-room-level-calendars
**Date**: 2026-02-01
**Status**: Draft

## 1. Overview

This feature implements two related enhancements to RentalSync Bridge:

1. **Room-Level Calendars**: Generate separate iCal feeds for each room/unit within a property, rather than a single calendar per property
2. **Custom Fields UI Enhancement**: Improve the admin UI to show available custom fields in a dropdown when adding new fields

## 2. Problem Statement

### 2.1 Room-Level Calendars

**Current Behavior**: The system creates one iCal calendar per Cloudbeds property. All reservations for the property appear in a single calendar, making it impossible to sync individual room availability to external platforms like Airbnb.

**Impact**: Property managers with multi-unit properties (e.g., 10 rooms) cannot use the iCal export to sync individual room calendars to Airbnb or other OTAs.

**Required Behavior**: Each room/unit within a property needs its own dedicated iCal calendar. For a property with 10 units, there should be 10 separate calendars, each showing only reservations for that specific room.

### 2.2 Custom Fields UI

**Current Behavior**: The admin UI only shows custom fields that are already configured in the database. Users cannot see what fields are available to add without consulting documentation.

**Impact**: Users must guess or remember field names, leading to typos and confusion.

**Required Behavior**: When adding a custom field, users should see a dropdown of available fields (from `AVAILABLE_FIELDS`) that are not yet configured for that listing.

## 3. Technical Context

### 3.1 Current Architecture

#### Listing Model
```python
class Listing(Base):
    id: int
    cloudbeds_id: str  # Property ID from Cloudbeds
    name: str
    ical_url_slug: str
    # ... other fields
```

#### Booking Model
```python
class Booking(Base):
    id: int
    listing_id: int  # FK to Listing
    cloudbeds_booking_id: str
    # No room association currently
```

#### Cloudbeds API Endpoints
- `GET /api/v1.3/getHotels` - Returns properties
- `GET /api/v1.3/getRooms` - Returns rooms for a property (not currently used)
- `GET /api/v1.3/getReservations` - Returns reservations with room data

#### Custom Fields
```python
AVAILABLE_FIELDS = {
    "booking_notes": "Booking Notes",
    "arrival_time": "Arrival Time",
    "departure_time": "Departure Time",
    "num_guests": "Number of Guests",
    "room_type_name": "Room Type",
    "source_name": "Booking Source",
    "special_requests": "Special Requests",
    "estimated_arrival": "Estimated Arrival",
}
```

### 3.2 Cloudbeds API Room Data

The `getReservations` endpoint returns room information:
```json
{
  "reservationID": "12345",
  "roomTypeName": "Deluxe Suite",
  "roomTypeID": "101",
  "rooms": [
    {
      "roomID": "201",
      "roomName": "Room 201"
    }
  ]
}
```

The `getRooms` endpoint returns available rooms:
```json
{
  "success": true,
  "data": [
    {
      "roomID": "201",
      "roomName": "Room 201",
      "roomTypeName": "Deluxe Suite",
      "roomTypeID": "101"
    }
  ]
}
```

## 4. Proposed Solution

### 4.1 Room-Level Calendars

#### 4.1.1 New Room Model

```python
class Room(Base):
    __tablename__ = "rooms"

    id: int  # Primary key
    listing_id: int  # FK to Listing
    cloudbeds_room_id: str  # Room ID from Cloudbeds
    room_name: str  # Display name
    room_type_name: str  # Room type (e.g., "Deluxe Suite")
    ical_url_slug: str  # Auto-generated, user-customizable
    enabled: bool = True  # Enabled by default
    created_at: datetime
    updated_at: datetime

    # Relationships
    listing: Listing
    bookings: list[Booking]
```

**Slug Generation**: Auto-generated from room name (e.g., "Room 201" → "room-201") but user-editable via admin UI.
```

#### 4.1.2 Updated Booking Model

```python
class Booking(Base):
    # Existing fields...
    room_id: int | None  # FK to Room (nullable for migration)
```

#### 4.1.3 URL Structure

- Room-level: `/ical/{listing_slug}/{room_slug}.ics`

Note: Property-level iCal is removed (not deprecated) since this is pre-production.

#### 4.1.4 Sync Changes

1. "Sync Properties from Cloudbeds" button renamed to "Sync Rooms from Cloudbeds"
2. Sync process:
   - Fetch properties via `getHotels`
   - For each property, fetch rooms via `getRooms`
   - Create/update Room records
   - Link existing bookings to rooms based on reservation data

#### 4.1.5 Admin UI Changes

- Listing cards show expandable room list
- Each room has its own iCal URL copy button
- Room enable/disable toggle
- Room-level custom fields (optional, future enhancement)

### 4.2 Custom Fields UI Enhancement

#### 4.2.1 New API Endpoint

```
GET /api/listings/{listing_id}/available-custom-fields
```

Returns available fields that are NOT already configured for the listing:
```json
{
  "arrival_time": "Arrival Time",
  "departure_time": "Departure Time",
  "num_guests": "Number of Guests"
}
```

#### 4.2.2 Admin UI Changes

- Replace free-text `field_name` input with `<select>` dropdown
- Dropdown populated with available (unconfigured) fields
- Already-configured fields excluded from dropdown
- Display label auto-populates from AVAILABLE_FIELDS but remains editable

## 5. Data Migration

### 5.1 Room Migration Strategy

1. Create `rooms` table with new schema
2. Add nullable `room_id` FK to `bookings` table
3. No automatic backfill - rooms created on next sync
4. Existing bookings remain linked only to listing until re-synced

### 5.2 URL Migration

Property-level iCal URLs (`/ical/{slug}.ics`) are removed. Users must use room-level URLs after migration.

## 6. API Changes

### 6.1 New Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/listings/{id}/rooms` | List rooms for a listing |
| GET | `/api/rooms/{id}` | Get room details |
| PATCH | `/api/rooms/{id}` | Update room (enable/disable) |
| GET | `/api/listings/{id}/available-custom-fields` | Get unconfigured fields |
| GET | `/ical/{listing_slug}/{room_slug}.ics` | Room-level iCal feed |

### 6.2 Modified Endpoints

| Method | Endpoint | Change |
|--------|----------|--------|
| POST | `/api/sync/properties` | Now syncs rooms too |
| GET | `/api/listings/{id}` | Include rooms in response |

## 7. Security Considerations

- Room iCal URLs use same slug-based access as property URLs
- No additional authentication required (iCal feeds are designed for unauthenticated access)
- Room data contains no additional PII beyond what's already in bookings

## 8. Testing Requirements

### 8.1 Unit Tests
- Room model CRUD operations
- Room-listing relationship
- Booking-room association
- Available custom fields filtering
- Room iCal generation

### 8.2 Integration Tests
- Cloudbeds room sync
- Room-level iCal feed generation
- Custom fields dropdown population

### 8.3 Manual Tests
- Multi-room property sync
- Room iCal import to Airbnb
- Custom fields UI workflow

## 9. Success Criteria

1. Properties with multiple rooms show separate iCal URLs per room
2. Each room's iCal contains only that room's bookings
3. "Sync from Cloudbeds" fetches and creates room records
4. Custom fields UI shows dropdown of available fields
5. All existing functionality continues to work (backward compatible)

## 10. Out of Scope

- Room-level custom fields (use listing-level for all rooms)
- Room pricing/availability management
- Direct Airbnb API integration
- Room photos or detailed descriptions

## 11. Open Questions

1. ~~Should property-level iCal be deprecated or maintained indefinitely?~~ **Resolved: Removed**
2. ~~Should room slugs be auto-generated or user-editable?~~ **Resolved: Auto-generated with user override**
3. ~~Should rooms be enabled by default?~~ **Resolved: Yes, enabled by default**
4. Should we support room-level custom field overrides in the future?

## 12. Dependencies

- Cloudbeds API `getRooms` endpoint access
- Existing Listing and Booking models
- Admin UI JavaScript framework

## 13. Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Cloudbeds API rate limits on room fetch | Medium | Medium | Batch requests, cache results |
| Large properties with 100+ rooms | Low | Medium | Pagination, lazy loading in UI |
| Migration breaks existing bookings | Low | High | Nullable FK, no data loss |
