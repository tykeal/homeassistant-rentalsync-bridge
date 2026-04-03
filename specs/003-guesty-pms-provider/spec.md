<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->
# Feature Specification: Guesty PMS Provider Integration

**Feature Branch**: `003-guesty-pms-provider`
**Created**: 2025-07-15
**Status**: Draft
**Input**: User description: "Add Guesty as a second PMS provider to the rentalsync-bridge project"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Guesty Property Owner Sets Up Booking Sync (Priority: P1)

A property manager who uses Guesty as their PMS wants to connect rentalsync-bridge to their Guesty account so that their booking data is automatically synced and available as iCal feeds in Home Assistant. They navigate to the add-on's admin UI, select Guesty as their PMS provider, enter their Guesty API credentials, and authorize the connection. Once connected, their Guesty listings, reservations, and room data begin syncing on schedule.

**Why this priority**: This is the core value proposition — without the ability to connect, authenticate, and sync Guesty data, no other Guesty features are usable. This story validates the entire end-to-end flow from credential entry to data availability.

**Independent Test**: Can be fully tested by configuring Guesty credentials in the admin UI, triggering a sync, and verifying that listings and reservations appear in the system and are available as iCal feeds.

**Acceptance Scenarios**:

1. **Given** a fresh installation with no PMS configured, **When** the user selects "Guesty" as their PMS type and enters valid Guesty client credentials, **Then** the system obtains an access token and confirms a successful connection.
2. **Given** a connected Guesty account with active listings, **When** an automatic sync runs, **Then** all Guesty listings, their rooms, and current reservations are imported and stored.
3. **Given** synced Guesty reservation data, **When** the user accesses an iCal feed URL for a listing, **Then** the feed contains calendar events matching the Guesty reservations with correct check-in/check-out dates and guest names.
4. **Given** a Guesty account with reservations that have custom fields, **When** a sync runs, **Then** custom field data is retrieved and stored alongside the reservation.

---

### User Story 2 - Existing Cloudbeds User Upgrades Without Disruption (Priority: P2)

A property manager who already has a working Cloudbeds setup upgrades the add-on to the version that includes Guesty support. Their existing Cloudbeds configuration, credentials, synced data, and iCal feed URLs all continue to work without any manual intervention. The database is migrated transparently, and all existing functionality remains intact.

**Why this priority**: Backward compatibility protects existing users. A breaking upgrade would erode trust and create support burden. This must work seamlessly to ensure adoption confidence.

**Independent Test**: Can be fully tested by upgrading an existing Cloudbeds-configured installation and verifying that all existing iCal feeds remain accessible with the same URLs and data.

**Acceptance Scenarios**:

1. **Given** an existing installation with Cloudbeds configured and synced data, **When** the add-on is upgraded to the multi-PMS version, **Then** all existing data is preserved and accessible without re-configuration.
2. **Given** an upgraded installation with migrated data, **When** a Cloudbeds sync runs, **Then** it completes successfully using the existing credentials and updates data as before.
3. **Given** an upgraded installation, **When** a user accesses a previously bookmarked iCal feed URL, **Then** the feed returns correct calendar data without any URL changes.
4. **Given** an upgraded installation, **When** the user opens the admin UI, **Then** their existing Cloudbeds configuration is displayed correctly with "Cloudbeds" shown as the active PMS type.

---

### User Story 3 - Admin Manages PMS Provider Selection (Priority: P3)

An administrator wants to view and manage which PMS provider is active. They open the admin UI and see clear indication of the currently configured PMS type. If they need to switch providers or set up a new one, the UI presents provider-specific credential forms with appropriate fields and help text for the selected PMS.

**Why this priority**: Good admin UX ensures users can self-serve setup and troubleshooting. However, the core sync functionality (P1) and migration safety (P2) must work first.

**Independent Test**: Can be fully tested by navigating the admin UI, selecting different PMS types, and verifying that appropriate credential forms and validation messages appear.

**Acceptance Scenarios**:

1. **Given** an unconfigured installation, **When** the user opens the admin UI, **Then** they see a PMS type selection with at least "Cloudbeds" and "Guesty" as options.
2. **Given** the PMS type selection screen, **When** the user selects "Guesty", **Then** a Guesty-specific credential form is displayed with fields for Client ID and Client Secret.
3. **Given** the PMS type selection screen, **When** the user selects "Cloudbeds", **Then** a Cloudbeds-specific credential form is displayed with the appropriate OAuth fields.
4. **Given** invalid Guesty credentials entered in the form, **When** the user attempts to connect, **Then** a clear error message explains the authentication failure.

---

### User Story 4 - System Handles Guesty API Constraints Gracefully (Priority: P4)

The system respects Guesty's specific API constraints — particularly the strict token request limit (5 requests per key per 24 hours) and rate limiting (429 responses). Tokens are cached and reused for their full 24-hour validity period. When rate limits are hit, the system backs off and retries automatically without data loss.

**Why this priority**: Reliability and correctness under Guesty's constraints are essential for production use, but can be refined after the basic integration works.

**Independent Test**: Can be tested by simulating token expiry and rate-limit scenarios and verifying that the system handles them correctly without excessive token requests or data loss.

**Acceptance Scenarios**:

1. **Given** a valid cached Guesty access token, **When** a sync is triggered, **Then** the system reuses the cached token without requesting a new one.
2. **Given** an expired Guesty access token, **When** a sync is triggered, **Then** the system requests exactly one new token and caches it for reuse.
3. **Given** the Guesty API returns a 429 rate-limit response, **When** a sync is in progress, **Then** the system pauses, waits for the retry-after period, and retries the request automatically.
4. **Given** the system has already made 4 token requests in 24 hours, **When** a token refresh is needed, **Then** the system logs a warning about approaching the daily limit, proceeds with the token request, and if the 5-request limit would be exceeded, logs an error and defers further token requests until the 24-hour window resets.

---

### User Story 5 - Guesty Guest Details and Multi-Endpoint Data Assembly (Priority: P5)

The system correctly assembles complete reservation records from Guesty's multi-endpoint data model, where guest details and custom fields require separate API calls from the reservations endpoint. The synced data is normalized into the same format used by Cloudbeds so that downstream features (iCal generation, room-level calendars) work identically regardless of the source PMS.

**Why this priority**: Data completeness and normalization ensure feature parity between PMS providers, but the basic reservation sync (P1) must work first.

**Independent Test**: Can be tested by syncing a Guesty reservation and verifying that guest names, custom fields, and all booking details are populated in the stored records.

**Acceptance Scenarios**:

1. **Given** a Guesty reservation with a linked guest record, **When** a sync runs, **Then** the guest's full name is retrieved from the separate guests endpoint and stored with the reservation.
2. **Given** a Guesty reservation with associated custom fields, **When** a sync runs, **Then** custom field values are retrieved from the v3 custom-fields endpoint and stored with the reservation.
3. **Given** synced Guesty data, **When** an iCal feed is generated, **Then** the calendar events contain the same types of information (guest name, dates, property) as Cloudbeds-sourced events.
4. **Given** a Guesty listing with paginated results (more than one page of reservations), **When** a sync runs, **Then** all pages are retrieved and all reservations are stored.

---

### Edge Cases

- What happens when Guesty returns a listing with no rooms? The system treats the listing as a single-unit property with one implicit room.
- What happens when a Guesty reservation references a guest ID that returns a 404? The system stores the reservation with a placeholder name (e.g., "Guest [guestId]") and logs a warning.
- What happens when the Guesty token request limit (5/day) is exhausted? The system stops attempting token refreshes until the 24-hour window resets, logs an error, and continues serving cached data.
- What happens when a sync is in progress and the add-on restarts? The next sync cycle completes a full sync successfully without creating duplicate records, as the upsert logic on unique PMS booking identifiers prevents duplicates.
- What happens when Guesty field formats differ from Cloudbeds (e.g., date formats, ID types)? The PMS provider layer normalizes all data to the system's internal format before passing it to the sync orchestrator.
- What happens when a user tries to switch PMS type on an installation that already has synced data? The system warns the user that switching providers will not delete existing data but new syncs will use the selected provider, and existing iCal feeds will reflect the last synced data until replaced by new provider data.

## Requirements *(mandatory)*

### Functional Requirements

#### PMS Provider Abstraction

- **FR-001**: System MUST support a pluggable PMS provider model where each provider implements a standard set of operations: retrieve listings, retrieve reservations, retrieve rooms, retrieve guest details, retrieve custom fields, and refresh authentication tokens.
- **FR-002**: System MUST route all PMS data operations through the provider abstraction, ensuring that the sync orchestrator, calendar generation, and API endpoints are PMS-agnostic.
- **FR-003**: System MUST allow exactly one active PMS provider per installation at any given time.

#### Guesty Integration

- **FR-004**: System MUST authenticate with the Guesty API using OAuth 2.0 client credentials (Client ID and Client Secret) to obtain a bearer access token.
- **FR-005**: System MUST cache Guesty access tokens and reuse them for their full validity period (24 hours) to minimize token requests.
- **FR-006**: System MUST enforce a safety limit on Guesty token requests to avoid exceeding the 5-requests-per-key-per-24-hours limit.
- **FR-007**: System MUST retrieve Guesty listings with support for pagination (limit/skip parameters).
- **FR-008**: System MUST retrieve Guesty reservations with support for filtering by status, date range, and listing ID.
- **FR-009**: System MUST retrieve guest details from Guesty's dedicated guests endpoint and associate them with reservations.
- **FR-010**: System MUST retrieve custom field values from Guesty's v3 custom-fields endpoint (one request per reservation) and associate them with reservations. The v2 custom-fields endpoint is deprecated (April 2026) and MUST NOT be used.
- **FR-011**: System MUST handle Guesty's 429 rate-limit responses with automatic backoff and retry using the Retry-After header when provided.
- **FR-012**: System MUST normalize Guesty data formats (MongoDB-style string IDs, localized date fields, separate guest records) into the system's internal data model. Guesty multi-unit listings with sub-units MUST map to the existing Room model; single-unit Guesty listings create one implicit Room record.

#### Database and Data Model

- **FR-013**: System MUST store a `pms_type` discriminator column on the OAuthCredential record (values: `"cloudbeds"`, `"guesty"`) to distinguish authentication flows. Guesty credentials use the client_credentials grant (clientId + clientSecret → bearer token). Cloudbeds credentials use the authorization_code grant (access_token + refresh_token) or API key authentication. All sensitive fields remain Fernet-encrypted.
- **FR-014**: System MUST use provider-agnostic field names with the `pms_` prefix for PMS-originated identifiers in all data records. Specifically: `cloudbeds_id` → `pms_id` (Listing), `cloudbeds_booking_id` → `pms_booking_id` (Booking), `cloudbeds_room_id` → `pms_room_id` (Room). All identifier columns use String type to accommodate both numeric (Cloudbeds) and MongoDB-style string (Guesty) identifiers.
- **FR-015**: System MUST migrate existing data from provider-specific field names to generic `pms_*` field names via a one-way Alembic migration while preserving all values. Rollback to the pre-migration schema is not supported.
- **FR-016**: System MUST support both short numeric IDs (Cloudbeds) and long string IDs (Guesty) in all PMS identifier fields.

#### Configuration

- **FR-017**: System MUST accept PMS provider type as a configuration option.
- **FR-018**: System MUST accept provider-specific credentials through configuration (environment variables or UI) for whichever PMS type is active.
- **FR-019**: System MUST validate that required credentials are present for the selected PMS type before attempting a sync.

#### Backward Compatibility

- **FR-020**: System MUST automatically migrate existing Cloudbeds installations to the new multi-PMS data model on upgrade without requiring user intervention.
- **FR-021**: System MUST preserve all existing iCal feed URLs and their content through the migration.
- **FR-022**: System MUST continue to accept legacy Cloudbeds-specific environment variable names (CLOUDBEDS_CLIENT_ID, CLOUDBEDS_CLIENT_SECRET) as valid configuration for backward compatibility.

#### Admin UI

- **FR-023**: System MUST present a PMS type selection in the admin UI when configuring credentials.
- **FR-024**: System MUST display provider-specific credential forms based on the selected PMS type.
- **FR-025**: System MUST provide connection testing feedback in the admin UI after credential entry.

### Key Entities

- **PMS Provider**: Represents a supported property management system (e.g., Cloudbeds, Guesty). Defined by a type identifier, authentication method, and data mapping rules.
- **Credential (OAuthCredential)**: Stores authentication information for a PMS provider in the `oauth_credentials` table. Discriminated by `pms_type` column (`"cloudbeds"` | `"guesty"`). Guesty records populate: `client_id`, `client_secret`, `access_token`, `token_expires_at`. Cloudbeds records populate: `client_id`, `client_secret`, `access_token`, `refresh_token`, `token_expires_at`, plus optional `api_key`. All token and secret fields are Fernet-encrypted. One credential set per installation.
- **Listing**: A rental property retrieved from the PMS. Identified by `pms_id` (PMS-agnostic external ID). Contains property name, address, timezone, and descriptive attributes. Each listing has a stable `ical_url_slug` used in iCal feed URLs.
- **Booking**: A guest reservation for a listing ("Booking" is the canonical internal term; Guesty API uses "reservation"). Identified by `pms_booking_id` (PMS-agnostic external ID). Contains check-in date, check-out date, guest name, status, and associated listing. Multi-room bookings use composite IDs (`{reservationID}::{roomID}`).
- **Room**: A bookable unit within a listing. Identified by `pms_room_id` (PMS-agnostic external ID). Linked to a parent listing. Each room has an `ical_url_slug` for room-level iCal feeds. Guesty sub-units within multi-unit listings map to Room records.
- **Guest**: A person associated with a reservation. In some PMS systems (like Guesty), guest details are stored separately and linked by reference. Normalized to include full name at minimum.
- **Custom Field**: Provider-specific additional data associated with a reservation. Key-value pairs retrieved from the PMS and stored generically.

## Assumptions

- Each rentalsync-bridge installation connects to exactly one PMS provider at a time (no multi-provider installations).
- Guesty's OAuth 2.0 token endpoint uses standard client_credentials grant type with client_id and client_secret in the request body.
- Guesty's 24-hour token validity and 5-request-per-key daily limit are enforced server-side and may change; the system should be configurable for these thresholds.
- The existing Cloudbeds integration will be refactored to implement the same provider abstraction, not maintained as a separate code path.
- Guesty listing and reservation pagination follows a limit/skip pattern where the system must iterate until all results are retrieved.
- Guest name is the minimum required guest detail; additional guest fields (email, phone) are optional and stored if available.
- The database migration from Cloudbeds-specific to generic field names is a one-way migration; rollback to pre-migration schema is not supported.
- Existing iCal feed URLs use listing/room identifiers that are preserved through the migration, so feed URLs remain stable.

## Clarifications

### Session 2026-04-02

- Q: What naming convention for provider-agnostic database columns? → A: `pms_*` prefix — `cloudbeds_id` → `pms_id`, `cloudbeds_booking_id` → `pms_booking_id`, `cloudbeds_room_id` → `pms_room_id`.
- Q: How are different PMS auth models stored in a single credential table? → A: `pms_type` discriminator on OAuthCredential — Guesty uses client_credentials grant; Cloudbeds uses authorization_code grant or API key.
- Q: How do Guesty multi-unit listings with sub-units map to the data model? → A: Sub-units map to existing Room model; single-unit listings create one implicit Room.
- Q: Is the database schema migration reversible? → A: No — one-way Alembic migration from `cloudbeds_*` to `pms_*` columns; no rollback support.
- Q: What is the canonical internal term for guest reservations? → A: "Booking" — Guesty API uses "reservation" but the internal model, table, and APIs use "Booking"
- Q: Which Guesty custom fields API version is required? → A: V3 endpoint only; V2 is deprecated as of April 2026 and must not be used.
- Q: What does "proceeds cautiously" mean for Guesty token limit? → A: Log warning at 4th request; proceed with request; at 5th, log warning and proceed (final allowed request); at 6th+, log error and defer until 24h window resets.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can complete Guesty PMS setup (credential entry to first successful sync) in under 5 minutes.
- **SC-002**: Guesty booking data appears in iCal feeds within one sync cycle after initial setup, with 100% of active reservations represented.
- **SC-003**: Existing Cloudbeds installations upgrade to the multi-PMS version with zero manual configuration changes and zero data loss.
- **SC-004**: All previously bookmarked iCal feed URLs continue to return valid calendar data after upgrade without URL changes.
- **SC-005**: The system makes no more than 2 Guesty token requests per 24-hour period under normal operating conditions (well within the 5-request limit).
- **SC-006**: When Guesty rate limits are encountered, the system recovers automatically and completes the sync without user intervention.
- **SC-007**: Guesty-sourced iCal feeds contain the same categories of information (guest name, check-in/out dates, property name) as Cloudbeds-sourced feeds, achieving feature parity in calendar output.
- **SC-008**: Adding a future third PMS provider requires only implementing the provider interface and adding UI/config support — no changes to the sync orchestrator, calendar generation, or data access layers.
