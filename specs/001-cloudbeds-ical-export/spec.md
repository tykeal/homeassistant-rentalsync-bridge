<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Cloudbeds to Airbnb iCal Export Bridge

**Feature Branch**: `001-cloudbeds-ical-export`
**Created**: 2025-01-13
**Status**: Draft
**Input**: User description: "Create an application to translate Cloudbeds bookings into an Airbnb style iCal. The application will have an administrative interface that allows configuration of selecting which listings are to re-exported and to provide the relevant exported iCal URL. The calendars will have proper start and end dates in the events and they will have the guest name or booking ID as part of the event subject line. The last 4 digits of the guest's phone number will be provided in the event description. An example ical will be provided during the planning and research phases along with the Cloudbeds API documentation. Additional detail fields may be defined for the event descriptions and the administrative UI shall allow for adding or removing these additional fields. The administrative UI will be a web portal that will be used as a Home Assistant addon and it will use Home Assistant authentication for determining access."

## User Scenarios & Testing *(mandatory)*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each user story/journey must be INDEPENDENTLY TESTABLE - meaning if you implement just ONE of them,
  you should still have a viable MVP (Minimum Viable Product) that delivers value.

  Assign priorities (P1, P2, P3, etc.) to each story, where P1 is the most critical.
  Think of each story as a standalone slice of functionality that can be:
  - Developed independently
  - Tested independently
  - Deployed independently
  - Demonstrated to users independently
-->

### User Story 1 - Basic iCal Export for Single Listing (Priority: P1)

As a property manager, I want to export a single Cloudbeds listing as an Airbnb-compatible iCal feed so that I can sync my bookings with external calendar systems.

**Why this priority**: This is the core value proposition - transforming Cloudbeds booking data into a consumable iCal format. Without this, the entire feature is non-functional.

**Independent Test**: Can be fully tested by configuring one listing in the admin UI, retrieving the generated iCal URL, and verifying that calendar applications can import bookings with proper dates, guest names/booking IDs as event titles, and phone number last 4 digits in descriptions.

**Acceptance Scenarios**:

1. **Given** a Cloudbeds property with active bookings, **When** administrator configures a listing for export and retrieves the iCal URL, **Then** the URL returns a valid iCal file with all bookings as events
2. **Given** a booking exists in Cloudbeds, **When** the iCal feed is accessed, **Then** each event includes the check-in date as start time, check-out date as end time, and guest name or booking ID in the event title
3. **Given** a booking with guest phone number, **When** viewing the event details, **Then** the description field contains the last 4 digits of the phone number
4. **Given** an iCal URL has been generated, **When** importing into Airbnb, Google Calendar, or Apple Calendar, **Then** the calendar successfully imports and displays all booking events correctly

---

### User Story 2 - Administrative Configuration Interface (Priority: P2)

As a property manager, I want to access a web-based administrative portal to configure which Cloudbeds listings are exported and customize the iCal output fields so that I can control what information is shared externally.

**Why this priority**: Without configuration capabilities, users cannot select listings or customize output, making the tool inflexible. This enables users to tailor the solution to their needs.

**Independent Test**: Can be tested by logging into the admin portal through Home Assistant authentication, adding/removing listings from the export configuration, and toggling optional data fields for event descriptions.

**Acceptance Scenarios**:

1. **Given** a user has Home Assistant access, **When** accessing the admin portal URL, **Then** they are prompted to authenticate via Home Assistant credentials
2. **Given** successful authentication, **When** viewing the admin dashboard, **Then** user sees a list of all available Cloudbeds listings with toggle controls for export enablement
3. **Given** a listing is selected for export, **When** saving the configuration, **Then** the system generates and displays the unique iCal URL for that listing
4. **Given** the admin interface for a listing, **When** user adds or removes optional data fields (e.g., booking notes, special requests), **Then** the changes are immediately applied to the iCal output
5. **Given** unauthenticated access attempt, **When** trying to access the admin portal, **Then** user is redirected to Home Assistant authentication and denied access without valid credentials

---

### User Story 3 - Multi-Listing Support (Priority: P3)

As a property manager with multiple listings, I want to configure and export iCal feeds for all my properties simultaneously so that I can efficiently manage calendars across my entire portfolio.

**Why this priority**: Scales the solution for users with multiple properties, but the core functionality works with single listings first.

**Independent Test**: Can be tested by enabling multiple listings in the admin UI, verifying each generates a unique iCal URL, and confirming that bookings from different properties do not cross-contaminate feeds.

**Acceptance Scenarios**:

1. **Given** multiple Cloudbeds listings are available, **When** administrator enables export for multiple listings, **Then** each listing receives a unique, independently accessible iCal URL
2. **Given** bookings exist across multiple properties, **When** accessing a specific listing's iCal feed, **Then** only bookings for that specific property appear in the calendar
3. **Given** custom field configurations per listing, **When** different listings have different optional fields enabled, **Then** each iCal feed respects its individual configuration

---

### User Story 4 - Real-Time Calendar Sync (Priority: P4)

As a property manager, I want my exported iCal feeds to reflect booking changes in near real-time so that external calendars stay current with my Cloudbeds data.

**Why this priority**: Enhances usability by ensuring calendar accuracy, but basic export functionality is more critical than sync frequency.

**Independent Test**: Can be tested by creating or modifying a booking in Cloudbeds, then accessing the iCal URL within a reasonable timeframe to verify the change is reflected.

**Acceptance Scenarios**:

1. **Given** a new booking is created in Cloudbeds, **When** the iCal feed is accessed within 5 minutes, **Then** the new booking appears as an event
2. **Given** an existing booking is modified (dates, guest info), **When** the iCal feed is refreshed, **Then** the event reflects the updated information
3. **Given** a booking is cancelled in Cloudbeds, **When** the iCal feed is accessed, **Then** the cancelled booking is removed from the calendar

---

### Edge Cases

- What happens when a booking has no guest name? (Per FR-004: Use booking ID as fallback in event title)
- What happens when a guest has no phone number? (Per FR-005: Omit phone number field from description)
- How does the system handle bookings with missing check-in or check-out dates? (Per FR-021: Skip booking, log error with booking ID for administrator review)
- What happens if Cloudbeds API is unavailable when generating iCal? (Return cached version if available, otherwise return HTTP 503 with Retry-After header)
- How does the system handle extremely long guest names or booking IDs? (Truncate to 255 characters per iCal RFC 5545 summary field limit)
- What happens when Home Assistant authentication service is down? (Admin portal returns HTTP 503; iCal URLs remain accessible per FR-014)
- How does the system handle time zones for check-in/check-out dates? (Per FR-022: Use property's configured timezone from Cloudbeds; reject invalid IANA timezone identifiers and default to UTC with warning log)
- What happens when multiple administrators configure the same listing simultaneously? (Per FR-019: Last save wins with timestamp display in admin UI)

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST retrieve booking data from Cloudbeds via API including check-in date, check-out date, guest name, booking ID, and guest phone number
- **FR-002**: System MUST generate valid iCal (RFC 5545 compliant) calendar files containing booking events
- **FR-003**: System MUST include check-in date as event start time (DTSTART) and check-out date as event end time (DTEND) in proper iCal format
- **FR-004**: System MUST use guest name as event title when available, falling back to booking ID if guest name is unavailable
- **FR-005**: System MUST include the last 4 digits of guest phone number in the event description field when phone number is available
- **FR-006**: System MUST provide a web-based administrative interface accessible as a Home Assistant addon
- **FR-007**: System MUST authenticate all administrative access via Home Assistant authentication mechanism
- **FR-008**: System MUST allow administrators to select which Cloudbeds listings are enabled for iCal export
- **FR-009**: System MUST generate unique, persistent iCal URLs for each enabled listing
- **FR-010**: System MUST allow administrators to configure, add, and remove optional additional data fields for inclusion in event descriptions through the admin interface
- **FR-011**: System MUST persist configuration settings (enabled listings, custom fields, iCal URLs) across system restarts
- **FR-012**: System MUST handle multiple listings independently, with separate configurations and iCal URLs for each
- **FR-013**: System MUST refresh booking data from Cloudbeds using a hybrid sync strategy: webhook notifications (when available), configurable polling interval (1-60 minutes, default 5 minutes), and on-demand refresh with timeout fallback to cached data
- **FR-014**: System MUST serve iCal feeds via publicly accessible HTTP/HTTPS URLs that do not require authentication, enabling subscription by external calendar applications
- **FR-015**: System MUST validate that generated iCal files are compatible with Airbnb, Google Calendar, and Apple Calendar
- **FR-016**: System MUST handle API rate limits and errors from Cloudbeds gracefully without exposing sensitive error details to iCal consumers
- **FR-017**: System MUST log configuration changes and errors for troubleshooting purposes
- **FR-018**: System MUST support at least 50 listings with up to 365 bookings per listing without performance degradation (iCal generation <2s, API response <500ms)
- **FR-019**: System MUST handle concurrent administrator configuration updates using last-write-wins semantics with timestamp display in the admin UI
- **FR-020**: System MUST encrypt OAuth credentials (access tokens, refresh tokens) at rest using industry-standard encryption (AES-256 or equivalent)
- **FR-021**: System MUST skip bookings with missing or invalid check-in/check-out dates and log an error with booking ID for administrator review
- **FR-022**: System MUST validate timezone identifiers against IANA timezone database and default to UTC with a warning log for invalid or missing timezones
- **FR-023**: System MUST serve iCal feeds over HTTPS when deployed in production; HTTP is permitted only for local development or when behind a TLS-terminating reverse proxy

### Key Entities *(include if feature involves data)*

- **Listing**: Represents a Cloudbeds listing (property/unit) that can be configured for iCal export. Attributes include listing ID, listing name, export enabled status, iCal URL slug, and custom field configuration.
- **Booking**: Represents a reservation in Cloudbeds. Attributes include booking ID, listing reference, guest name, guest phone number, check-in date, check-out date, booking status, and optional custom data fields.
- **iCal Feed**: Represents the generated calendar output for a specific listing. Contains collection of booking events formatted per RFC 5545 standard.
- **Configuration**: Represents administrator settings. Includes which listings are enabled, which optional fields are included per listing, and Cloudbeds API credentials.
- **Calendar Event**: Represents a single booking as an iCal event. Attributes include event summary (title), start date/time, end date/time, description, unique identifier, and timestamp.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Administrators can configure a new listing and obtain a working iCal URL within 5 minutes (UX simplicity goal: minimal steps, clear interface)
- **SC-002**: Generated iCal feeds successfully import into Airbnb, Google Calendar, and Apple Calendar without errors in 100% of test cases
- **SC-003**: Calendar events contain accurate check-in and check-out dates matching Cloudbeds data with zero date discrepancies
- **SC-004**: System supports at least 50 listings with up to 365 bookings per listing without performance degradation
- **SC-005**: iCal feed updates reflect Cloudbeds changes within 5 minutes of booking creation or modification when using default polling interval
- **SC-006**: Admin interface successfully authenticates via Home Assistant for 100% of authorized users
- **SC-007**: Custom field configurations apply correctly to iCal output with zero configuration errors
- **SC-008**: System uptime for iCal feed delivery exceeds 99.5% availability
- **SC-009**: Property managers reduce manual calendar synchronization time by at least 80% compared to manual entry
- **SC-010**: Zero instances of sensitive data leakage in iCal feeds. Classified PII that MUST NOT appear: full phone numbers (only last 4 digits permitted), guest email addresses, guest physical addresses, payment information, government IDs, or internal booking notes marked as private

## Glossary

| Term | Definition |
|------|------------|
| **Listing** | A Cloudbeds property or unit configured for iCal export. Synonymous with "property" or "room" in Cloudbeds terminology. |
| **iCal URL** | The HTTP/HTTPS endpoint that serves an iCal feed for a specific listing. This is the URL users subscribe to in their calendar applications. |
| **iCal Feed** | The calendar content (RFC 5545 formatted data) returned when accessing an iCal URL. Contains VEVENT entries for each booking. |
| **Check-in Date** | The guest arrival date from Cloudbeds. Mapped to DTSTART in iCal events. |
| **Check-out Date** | The guest departure date from Cloudbeds. Mapped to DTEND in iCal events. |
| **DTSTART/DTEND** | RFC 5545 iCal properties representing event start and end times. |
| **Sync** | The process of fetching updated booking data from Cloudbeds and refreshing the local cache. |
| **Polling Interval** | The configurable time between automatic sync operations (1-60 minutes). |
| **Slug** | A URL-safe identifier generated for each listing's iCal URL (e.g., `/ical/beach-house-123.ics`). |
| **PII** | Personally Identifiable Information. Data that must be protected or excluded from iCal feeds per SC-010. |
