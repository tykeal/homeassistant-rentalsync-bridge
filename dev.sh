#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

# Development wrapper script for running RentalSync Bridge from repo root
#
# Usage:
#   ./dev.sh              # Run development server
#   ./dev.sh --debug      # Run with debug logging enabled
#   DEBUG=1 ./dev.sh      # Alternative debug mode via environment
#   ./dev.sh test         # Run tests
#   ./dev.sh test -v      # Run tests with extra args

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADDON_DIR="${SCRIPT_DIR}/rentalsync-bridge"

# Check for debug mode
DEBUG_MODE="${DEBUG:-0}"
if [[ "$1" == "--debug" || "$1" == "-d" ]]; then
    DEBUG_MODE=1
    shift
fi

# Set up environment
export STANDALONE_MODE="${STANDALONE_MODE:-true}"
export DATABASE_URL="${DATABASE_URL:-sqlite:///${SCRIPT_DIR}/data/dev.db}"

if [[ "$DEBUG_MODE" == "1" ]]; then
    export LOG_LEVEL="DEBUG"
    echo "🐛 Debug mode enabled (LOG_LEVEL=DEBUG)"
fi

# Ensure data directory exists
mkdir -p "${SCRIPT_DIR}/data"

# Handle commands
case "${1:-run}" in
    run|server|start)
        shift 2>/dev/null || true
        echo "🚀 Starting development server..."
        echo "   URL: http://localhost:8099"
        echo "   Admin: http://localhost:8099/admin"
        echo ""

        # Auto-run migrations before starting
        echo "📦 Running database migrations..."
        cd "${ADDON_DIR}"
        uv run alembic upgrade head
        echo ""

        UVICORN_ARGS=(
            "src.main:app"
            "--reload"
            "--host" "0.0.0.0"
            "--port" "${PORT:-8099}"
        )

        if [[ "$DEBUG_MODE" == "1" ]]; then
            UVICORN_ARGS+=("--log-level" "debug")
        fi

        exec uv run uvicorn "${UVICORN_ARGS[@]}" "$@"
        ;;

    test|tests)
        shift
        echo "🧪 Running tests..."
        cd "${ADDON_DIR}"
        exec uv run pytest tests/ "$@"
        ;;

    migrate|migration)
        shift
        echo "🗄️  Running database migrations..."
        cd "${ADDON_DIR}"
        exec uv run alembic upgrade head "$@"
        ;;

    shell)
        echo "🐍 Starting Python shell..."
        cd "${ADDON_DIR}"
        exec uv run python
        ;;

    sync)
        echo "📦 Syncing dependencies..."
        cd "${ADDON_DIR}"
        exec uv sync "$@"
        ;;

    lint)
        echo "🔍 Running linters..."
        exec uv run pre-commit run --all-files "$@"
        ;;

    help|--help|-h)
        cat << EOF
RentalSync Bridge Development Script

Usage: ./dev.sh [OPTIONS] [COMMAND] [ARGS...]

Options:
  --debug, -d     Enable debug logging (or set DEBUG=1)

Commands:
  run, server     Start development server (default)
  test            Run test suite
  migrate         Run database migrations
  shell           Start Python REPL
  sync            Sync dependencies with uv
  lint            Run pre-commit linters
  help            Show this help message

Environment Variables:
  DEBUG=1              Enable debug mode
  PORT=8099            Server port (default: 8099)
  DATABASE_URL=...     Database connection string
  STANDALONE_MODE=...  Enable standalone mode (default: true)
  LOG_LEVEL=...        Logging level (DEBUG, INFO, WARNING, ERROR)

Examples:
  ./dev.sh                    # Start server
  ./dev.sh --debug            # Start server with debug logging
  ./dev.sh test -v            # Run tests verbosely
  ./dev.sh test -k "test_ical" # Run specific tests
  DEBUG=1 ./dev.sh migrate    # Run migrations with debug output
EOF
        ;;

    *)
        # Pass through to uv run for any other command
        cd "${ADDON_DIR}"
        exec uv run "$@"
        ;;
esac
