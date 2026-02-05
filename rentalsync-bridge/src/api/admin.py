# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Admin UI routes."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse
from starlette.responses import Response

router = APIRouter(tags=["Admin UI"])

# Paths to static files and templates
BASE_DIR = Path(__file__).parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


@router.get("/", response_class=HTMLResponse)
async def admin_ui() -> HTMLResponse:
    """Serve the admin UI.

    Returns:
        HTML admin interface.
    """
    template_path = TEMPLATES_DIR / "admin.html"
    if template_path.exists():
        return HTMLResponse(content=template_path.read_text())
    return HTMLResponse(
        content="<h1>Admin UI not found</h1>",
        status_code=404,
    )


@router.get("/static/css/{filename}")
async def serve_css(filename: str) -> Response:
    """Serve CSS files.

    Args:
        filename: CSS filename to serve.

    Returns:
        CSS file response.
    """
    file_path = STATIC_DIR / "css" / filename
    if file_path.exists() and file_path.suffix == ".css":
        return FileResponse(file_path, media_type="text/css")
    return HTMLResponse(content="Not found", status_code=404)


@router.get("/static/js/{filename}")
async def serve_js(filename: str) -> Response:
    """Serve JavaScript files.

    Args:
        filename: JavaScript filename to serve.

    Returns:
        JavaScript file response.
    """
    file_path = STATIC_DIR / "js" / filename
    if file_path.exists() and file_path.suffix == ".js":
        return FileResponse(file_path, media_type="application/javascript")
    return HTMLResponse(content="Not found", status_code=404)
