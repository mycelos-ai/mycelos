"""Static file serving for the Mycelos web frontend.

Multi-page HTML architecture: serves files from src/mycelos/frontend/.
Unknown routes fall back to index.html (which redirects to /pages/chat.html).
API routes (starting with /api/) are excluded from the fallback.
"""

from __future__ import annotations

from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException


class SPAStaticFiles(StaticFiles):
    """StaticFiles with fallback and cache-busting.

    - Unknown non-API routes fall back to index.html.
    - HTML/JS/CSS files are served with no-cache to avoid stale assets during development.
    """

    async def get_response(self, path: str, scope):
        try:
            response = await super().get_response(path, scope)
        except HTTPException as ex:
            if ex.status_code == 404:
                # Don't intercept API routes — let them return proper 404
                if path.startswith("api/") or path.startswith("api"):
                    raise
                # For all other unknown paths, serve index.html (redirect to chat)
                response = await super().get_response("index.html", scope)
            else:
                raise

        # Prevent browser caching of HTML/JS/CSS — ensures users always get latest code
        if path.endswith((".html", ".js", ".css")) or path == "index.html":
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response
