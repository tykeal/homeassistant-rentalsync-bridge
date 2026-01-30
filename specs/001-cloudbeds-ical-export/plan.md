<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Cloudbeds to Airbnb iCal Export Bridge

**Branch**: `001-cloudbeds-ical-export` | **Date**: 2026-01-24 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/001-cloudbeds-ical-export/spec.md`

## Summary

This feature implements a web application that transforms Cloudbeds booking data into Airbnb-compatible iCal feeds. The system provides an administrative web interface for configuring which listings to export, manages OAuth authentication with Cloudbeds, and serves publicly accessible iCal URLs. It operates as a Home Assistant addon with HA authentication, while also supporting standalone container deployment for local testing.

**Technical Approach**: FastAPI web application with SQLite storage, using the official cloudbeds-pms SDK for API integration and the icalendar library for RFC 5545-compliant calendar generation. Hybrid sync strategy combines webhook notifications with configurable polling (1-60 minutes) and on-demand refresh with timeout fallback.

## Technical Context

**Language/Version**: Python 3.13 or 3.14
**Primary Dependencies**: FastAPI, SQLAlchemy (async), icalendar, cloudbeds-pms (official SDK), APScheduler, Jinja2, cryptography (for AES-256)
**Storage**: SQLite with WAL mode (single-file, ACID-compliant, container-friendly)
**Testing**: pytest with async support, contract tests planned
**Target Platform**: Home Assistant addon (Linux), standalone Podman container for testing
**Project Type**: Web application (backend API + minimal HTML admin UI)
**Performance Goals**: <2s iCal generation, support 50 listings × 365 bookings, <500ms API response time
**Constraints**: Minimal frontend (basic HTML), uv package manager required, pre-commit hooks mandatory
**Scale/Scope**: Single-instance deployment, 10-50 listings typical, 99.5% uptime target

See [research.md](./research.md) for detailed technology decisions and rationale.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Verify feature design compliance with `.specify/memory/constitution.md`:

- [x] **Code Quality**: SPDX headers planned for all new source files (per tasks.md T001-T094)
- [x] **Testing Standards**: Test strategy defined - pytest with contract/integration/unit tests (Phase 7: T092 performance, T093 security)
- [x] **UX Consistency**: User-facing interfaces follow consistent patterns (basic HTML forms, REST API, standard error messages per research.md)
- [x] **Performance**: Measurable performance goals defined in Technical Context (<2s iCal gen, 50×365 bookings, <500ms API)
- [x] **Commit Discipline**: Team aware of atomic commit and pre-commit requirements (constitution.md principles, pre-commit hooks configured)

*All constitutional requirements satisfied. No violations to document.*

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
rentalsync-bridge/
├── src/                              # Application source code
│   ├── api/                          # FastAPI route handlers
│   │   ├── __init__.py
│   │   ├── health.py                 # Health check endpoints
│   │   ├── oauth.py                  # OAuth configuration
│   │   ├── listings.py               # Listing management
│   │   ├── custom_fields.py          # Custom field config
│   │   ├── bookings.py               # Booking debug endpoints
│   │   └── ical.py                   # Public iCal feeds
│   ├── models/                       # SQLAlchemy ORM models
│   │   ├── __init__.py
│   │   ├── oauth_credential.py
│   │   ├── listing.py
│   │   ├── custom_field.py
│   │   └── booking.py
│   ├── schemas/                      # Pydantic request/response schemas
│   │   ├── __init__.py
│   │   ├── oauth.py
│   │   ├── listing.py
│   │   ├── custom_field.py
│   │   └── booking.py
│   ├── services/                     # Business logic layer
│   │   ├── __init__.py
│   │   ├── cloudbeds_service.py      # Cloudbeds SDK wrapper
│   │   ├── sync_service.py           # Background sync orchestration
│   │   └── calendar_service.py       # iCal generation
│   ├── repositories/                 # Data access layer
│   │   ├── __init__.py
│   │   ├── listing_repository.py
│   │   ├── booking_repository.py
│   │   └── custom_field_repository.py
│   ├── middleware/                   # FastAPI middleware
│   │   ├── __init__.py
│   │   ├── auth.py                   # Home Assistant auth
│   │   └── error_handler.py          # Global error handling
│   ├── templates/                    # Jinja2 HTML templates
│   │   ├── base.html
│   │   ├── dashboard.html
│   │   ├── listings.html
│   │   ├── oauth.html
│   │   └── custom_fields.html
│   ├── static/                       # Static assets (minimal)
│   │   └── styles.css
│   ├── config.py                     # Configuration management
│   ├── database.py                   # SQLAlchemy async engine
│   └── main.py                       # FastAPI app entry point
│
├── tests/                            # Test suite
│   ├── contract/                     # API contract tests
│   ├── integration/                  # Integration tests
│   └── unit/                         # Unit tests
│
├── alembic/                          # Database migrations
│   ├── versions/
│   └── env.py
│
├── homeassistant/                    # Home Assistant addon config
│   ├── config.yaml
│   ├── Dockerfile
│   └── run.sh
│
├── pyproject.toml                    # uv project config
├── Dockerfile                        # Standalone container
└── .env.example                      # Environment template
```

**Structure Decision**: Single web application structure with clear separation of concerns (API routes, models, services, repositories). The service layer pattern isolates business logic from FastAPI routes and database access, enabling independent testing of each layer. Minimal frontend (HTML templates + basic CSS) keeps the project simple per constitutional requirements.

See [data-model.md](./data-model.md) for entity relationships and [contracts/api-specification.md](./contracts/api-specification.md) for API endpoints.

## Complexity Tracking

*No constitutional violations detected. This section intentionally left empty.*

## Planning Artifacts

This plan references the following detailed design documents:

- **[research.md](./research.md)** - Technology selection rationale (FastAPI, cloudbeds-pms SDK, icalendar, SQLite)
- **[data-model.md](./data-model.md)** - Database schema with 4 entities: OAuthCredential, Listing, CustomField, Booking
- **[contracts/api-specification.md](./contracts/api-specification.md)** - REST API contracts for 15+ endpoints
- **[quickstart.md](./quickstart.md)** - Standalone deployment guide for Podman testing
- **[tasks.md](./tasks.md)** - 94 dependency-ordered implementation tasks across 7 phases

## Implementation Phases

**Phase 1: Setup** (Tasks T001-T008)
- Project initialization, uv dependencies, Docker configuration

**Phase 2: Foundational** (Tasks T009-T023) - **CRITICAL GATE**
- Database models, migrations, service layer, FastAPI setup
- **BLOCKS** all user story work until complete

**Phase 3: User Story 1 - Basic iCal Export** (Tasks T024-T037) - **MVP**
- Repositories, calendar generation, public iCal endpoint, sync service
- **First deliverable increment**

**Phase 4: User Story 2 - Admin Configuration** (Tasks T038-T055)
- OAuth endpoints, listing management, custom fields, HTML UI
- Independent of US1, can run in parallel after Phase 2

**Phase 5: User Story 3 - Multi-Listing** (Tasks T056-T062)
- Multiple listing support, slug generation, conflict detection
- Requires US1 + US2 complete

**Phase 6: User Story 4 - Real-Time Sync** (Tasks T063-T071)
- Webhook handlers, on-demand refresh, sync status API
- Requires US1 complete

**Phase 7: Polish** (Tasks T072-T094)
- Edge cases, security hardening, documentation, performance testing
- Final productionization

See [tasks.md](./tasks.md) for complete task breakdown with dependencies and parallel execution markers.
