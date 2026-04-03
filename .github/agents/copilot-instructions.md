<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# rentalsync-bridge Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-01-24

## Active Technologies
- Python 3.13 (target-version in ruff/mypy config) + FastAPI ≥0.115, SQLAlchemy[asyncio] ≥2.0, aiosqlite ≥0.21, httpx ≥0.28, APScheduler ≥3.11, icalendar ≥6.1, cryptography ≥44.0, Pydantic ≥2.10, pydantic-settings ≥2.7 (003-guesty-pms-provider)
- SQLite via aiosqlite, WAL mode, Alembic migrations (003-guesty-pms-provider)

- Python 3.13 or 3.14 (001-cloudbeds-ical-export)

## Project Structure

```text
src/
tests/
```

## Commands

cd src [ONLY COMMANDS FOR ACTIVE TECHNOLOGIES][ONLY COMMANDS FOR ACTIVE TECHNOLOGIES] pytest [ONLY COMMANDS FOR ACTIVE TECHNOLOGIES][ONLY COMMANDS FOR ACTIVE TECHNOLOGIES] ruff check .

## Code Style

Python 3.13 or 3.14: Follow standard conventions

## Recent Changes
- 003-guesty-pms-provider: Added Python 3.13 (target-version in ruff/mypy config) + FastAPI ≥0.115, SQLAlchemy[asyncio] ≥2.0, aiosqlite ≥0.21, httpx ≥0.28, APScheduler ≥3.11, icalendar ≥6.1, cryptography ≥44.0, Pydantic ≥2.10, pydantic-settings ≥2.7

- 001-cloudbeds-ical-export: Added Python 3.13 or 3.14

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
