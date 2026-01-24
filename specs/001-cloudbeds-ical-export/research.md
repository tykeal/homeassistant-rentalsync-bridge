<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research & Design Decisions: Cloudbeds to Airbnb iCal Export Bridge

**Feature**: 001-cloudbeds-ical-export
**Date**: 2025-01-24
**Status**: In Progress

## Overview

This document captures research findings and architectural decisions for implementing a Cloudbeds booking data to Airbnb-compatible iCal export bridge. The system must operate as a Home Assistant addon with HA authentication while also supporting standalone container deployment for local testing.

## Research Areas

### 1. Web Framework Selection

**Decision**: FastAPI

**Rationale**:
- Lightweight and modern Python async framework
- Built-in OpenAPI documentation generation
- Excellent performance for API endpoints
- Native async/await support for Cloudbeds API calls
- Minimal dependencies compared to Django
- Strong typing support with Pydantic (aligns with mypy requirements)
- Simple to serve static HTML files for admin UI
- Well-suited for container deployment

**Alternatives Considered**:
- **Flask**: More mature but lacks native async support, would require additional extensions for OpenAPI
- **Django**: Too heavyweight for this use case, unnecessary ORM complexity
- **aiohttp**: Lower-level, would require more boilerplate for routing and validation

**Implementation Notes**:
- Use FastAPI with Jinja2 templates for minimal HTML admin UI
- Serve static files for CSS/JS (if any)
- Mount iCal endpoint as public route, admin UI as authenticated route

---

### 2. iCal Generation Library

**Decision**: icalendar (python-icalendar)

**Rationale**:
- Pure Python implementation of RFC 5545
- Well-maintained and widely used
- Simple API for creating events, calendars
- Handles timezone complexity correctly
- Proven compatibility with major calendar systems (Google Calendar, Apple Calendar, Airbnb)
- No external C dependencies (container-friendly)

**Alternatives Considered**:
- **vobject**: Less maintained, more complex API
- **ics.py**: Simpler but less feature-complete, potential edge case issues
- **Manual iCal string generation**: Error-prone, would require extensive testing for RFC compliance

**Implementation Notes**:
- Use `icalendar.Calendar()` for feed container
- Use `icalendar.Event()` for individual bookings
- Set proper PRODID to identify the application
- Include VTIMEZONE components for accurate timezone handling

---

### 3. Cloudbeds API Integration

**Decision**: cloudbeds-pms (Official Python SDK)

**Rationale**:
- Cloudbeds provides an official Python SDK (`cloudbeds-pms`) for their REST API
- SDK handles OAuth 2.0 authentication flow automatically
- Built-in support for:
  - OAuth token refresh and lifecycle management
  - Rate limiting and retry logic with exponential backoff
  - Proper error handling and exceptions
  - Type hints for better IDE support and mypy compatibility
- Maintained by Cloudbeds, ensuring compatibility with API changes
- Reduces custom code maintenance burden
- Async support via standard Python async/await patterns

**Alternatives Considered**:
- **Custom httpx wrapper**: Would duplicate functionality already in official SDK, increases maintenance burden
- **requests**: Synchronous, would block event loop, and still requires custom OAuth handling
- **aiohttp**: Lower-level, requires extensive custom wrapper code

**Implementation Notes**:
- Install via `uv add cloudbeds-pms`
- Initialize client with OAuth credentials from environment/database
- SDK manages token refresh automatically before expiry
- Use SDK methods for common operations:
  - `client.get_reservations()` for fetching bookings
  - `client.get_properties()` for listing properties
- Cache booking data with TTL to reduce API calls (outside SDK)
- SDK respects rate limit headers automatically

**API Documentation Reference**:
- SDK: https://pypi.org/project/cloudbeds-pms/
- API: https://hotels.cloudbeds.com/api/docs/

---

### 4. Home Assistant Authentication Integration

**Decision**: Home Assistant Ingress + Long-Lived Access Token verification

**Rationale**:
- Home Assistant addons use Ingress proxy for authentication
- Ingress adds `X-Ingress-User` header with authenticated username
- Addon can verify requests came through Ingress by checking headers
- Long-lived access tokens allow API calls to HA for user validation
- Fallback for standalone mode: environment variable to disable auth requirement

**Alternatives Considered**:
- **OAuth2 flow with HA**: Overly complex for addon use case
- **Basic auth**: Doesn't integrate with HA user management
- **JWT tokens**: Would require custom HA integration

**Implementation Notes**:
- Check `X-Ingress-User` header on admin routes
- In HA addon mode: trust the proxy (Ingress handles auth)
- In standalone mode: set `STANDALONE_MODE=true` env var to bypass auth checks
- Document standalone mode setup in quickstart.md for local testing

**Reference**: https://developers.home-assistant.io/docs/add-ons/communication/#ingress

---

### 5. Configuration Storage

**Decision**: SQLite with SQLAlchemy async

**Rationale**:
- Lightweight, zero-configuration database
- Single file storage suitable for container volumes
- SQLAlchemy provides clean async ORM with type safety
- Easy to backup and restore (single file)
- Sufficient for single-tenant deployment scale (50 listings)
- ACID guarantees for configuration consistency

**Alternatives Considered**:
- **JSON files**: No transaction support, race conditions on concurrent writes
- **PostgreSQL**: Overkill for this scale, requires separate container
- **In-memory + periodic dump**: Risk of data loss on crash

**Implementation Notes**:
- Database file location: `/data/rentalsync.db` (Home Assistant standard)
- Use Alembic for schema migrations
- Tables: `listings`, `listing_config`, `field_config`, `oauth_tokens`
- Enable WAL mode for better concurrent read performance

---

### 6. Data Synchronization Strategy

**Decision**: Scheduled polling with configurable interval (default: 5 minutes)

**Rationale**:
- Cloudbeds API does not support webhooks for booking changes
- Polling is the only reliable method to detect updates
- 5-minute interval balances freshness with API rate limits
- In-memory cache with TTL reduces redundant API calls when iCal accessed
- Background task scheduler (APScheduler) handles polling

**Alternatives Considered**:
- **Webhooks**: Not supported by Cloudbeds API
- **On-demand only**: Would cause slow iCal responses and rate limit issues
- **Continuous long-polling**: Unnecessary complexity, no latency requirement

**Implementation Notes**:
- Use APScheduler with AsyncIOScheduler
- Store last successful sync timestamp in database
- On sync: fetch bookings from last_sync - 24h to now + 365 days
- Cache parsed bookings in memory with 5-minute TTL
- Expose sync status in admin UI (last sync time, next sync time, errors)
- Allow manual trigger from admin UI

**Configuration**:
- Environment variable: `SYNC_INTERVAL_MINUTES` (default: 5)
- Min value: 1 minute (respect rate limits)
- Max value: 60 minutes (ensure reasonable freshness)

---

## Architecture Summary

```
┌─────────────────────────────────────────────────────────────┐
│                     Home Assistant                           │
│  ┌───────────────────────────────────────────────────────┐  │
│  │              Ingress Proxy (Auth)                     │  │
│  └───────────────────┬───────────────────────────────────┘  │
│                      │                                       │
│  ┌───────────────────▼───────────────────────────────────┐  │
│  │         RentalSync Bridge Addon                       │  │
│  │                                                        │  │
│  │  ┌──────────────┐      ┌─────────────────┐           │  │
│  │  │  FastAPI     │      │  Background     │           │  │
│  │  │  Web Server  │◄────►│  Sync Service   │           │  │
│  │  └──────┬───────┘      └────────┬────────┘           │  │
│  │         │                       │                     │  │
│  │  ┌──────▼───────┐      ┌────────▼────────┐           │  │
│  │  │  Admin UI    │      │  Cloudbeds      │           │  │
│  │  │  (HTML)      │      │  SDK Client     │           │  │
│  │  └──────────────┘      └────────┬────────┘           │  │
│  │                                 │                     │  │
│  │  ┌──────────────┐      ┌────────▼────────┐           │  │
│  │  │  iCal Feed   │◄─────┤  SQLite         │           │  │
│  │  │  Generator   │      │  Database       │           │  │
│  │  └──────────────┘      └─────────────────┘           │  │
│  │                                                        │  │
│  │  Volume: /data (persistent)                           │  │
│  └────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘

External: Cloudbeds API (hotels.cloudbeds.com)
          └─ OAuth 2.0 Authentication
          └─ Rate Limited REST API
          └─ Accessed via cloudbeds-pms SDK
```

---

## Technology Stack Summary

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| Language | Python | 3.13+ | Application runtime |
| Package Manager | uv | latest | Dependency management |
| Web Framework | FastAPI | ~0.109 | HTTP server & routing |
| Cloudbeds SDK | cloudbeds-pms | latest | Official Cloudbeds API client |
| Database | SQLite | 3.x | Configuration storage |
| ORM | SQLAlchemy | ~2.0 (async) | Database abstraction |
| Migrations | Alembic | ~1.13 | Schema versioning |
| iCal Library | icalendar | ~5.0 | RFC 5545 compliance |
| Scheduler | APScheduler | ~3.10 | Background sync tasks |
| Templating | Jinja2 | ~3.1 | HTML admin UI |
| Testing | pytest | ~7.4 | Test framework |
| Validation | Pydantic | ~2.5 | Data validation (via FastAPI) |

---

## Key Design Patterns

### 1. Repository Pattern for Data Access
- Abstract database operations behind repository interfaces
- Enables easier testing with mock repositories
- Clean separation between business logic and persistence

### 2. Service Layer for Business Logic
- `CloudbedsService`: API interaction via cloudbeds-pms SDK wrapper
- `CalendarService`: iCal generation logic
- `SyncService`: Background synchronization orchestration
- `ConfigService`: Configuration management

### 3. Dependency Injection
- Use FastAPI's dependency injection for services, database sessions
- Improves testability and loose coupling

### 4. Configuration Management
- Environment variables for deployment-specific config (API keys, URLs)
- Database for user-managed config (enabled listings, custom fields)
- Pydantic Settings for type-safe environment loading

---

## Security Considerations

1. **Authentication**:
   - Admin UI protected by Home Assistant Ingress in addon mode
   - Standalone mode requires explicit opt-in via environment variable
   - iCal URLs intentionally public (per Airbnb calendar subscription model)

2. **Data Privacy**:
   - Only last 4 digits of phone numbers exposed in iCal
   - No payment information, full addresses, or other PII in feeds
   - OAuth tokens stored encrypted at rest (SQLAlchemy encryption)

3. **API Security**:
   - Rate limiting awareness to avoid throttling
   - OAuth token refresh to maintain valid credentials
   - HTTPS required for Cloudbeds API communication

4. **Input Validation**:
   - Pydantic models validate all API inputs
   - SQLAlchemy ORM prevents SQL injection
   - Sanitize user-provided custom field selections

---

## Container Deployment Strategy

### Home Assistant Addon
- Follow HA addon development guidelines
- Use `config.json` for addon metadata
- Mount `/data` volume for persistent storage
- Respect HA architecture (amd64, arm64, armv7)
- Use HA Ingress for web UI access

### Standalone Podman Container
- Provide `Dockerfile` based on python:3.13-slim
- Environment variables for configuration
- Expose port 8000 for web access
- Volume mount for `/data` directory
- Health check endpoint for container orchestration

**Documentation Required**:
- `quickstart.md`: How to run standalone with podman
- `README.md`: Overview and HA addon installation
- `docs/standalone-testing.md`: Local development setup

---

## Open Questions & Future Considerations

1. **Sync Interval Tuning**: May need per-listing sync intervals for high-volume properties
2. **Multi-Tenant Support**: Current design is single-tenant; multi-tenant would require authentication rework
3. **Custom Field Extensibility**: Future enhancement could allow custom JavaScript field transformations
4. **Monitoring & Observability**: Consider metrics export (Prometheus) for HA users
5. **Backup & Restore**: Document database backup procedures for HA users

---

## Next Steps

1. ✅ Research complete - all NEEDS CLARIFICATION items resolved
2. → Generate `data-model.md` defining entities and relationships
3. → Generate API contracts in `contracts/` directory
4. → Generate `quickstart.md` for standalone setup
5. → Update agent context with technology choices
6. → Proceed to Phase 2 task generation

---

## References

- [Cloudbeds API Documentation](https://hotels.cloudbeds.com/api/docs/)
- [Home Assistant Add-on Development](https://developers.home-assistant.io/docs/add-ons/)
- [RFC 5545 - iCalendar Specification](https://tools.ietf.org/html/rfc5545)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [icalendar Python Library](https://icalendar.readthedocs.io/)
- [Airbnb Calendar Sync Requirements](https://www.airbnb.com/help/article/99)
