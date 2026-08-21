"""The HR interface's own application, on its own port.

**Two ports, one codebase, one container** (SPEC §14). The receiver answers the
device on its port; this app answers people on another. They share a process
and a database and nothing else — and in particular:

  * **there is no `/iclock/` route here.** A request for one is a `404`, not a
    redirect and not the single-page app's fallback. That is what lets a
    Tailscale tunnel point at this port without carrying the device routes with
    it: the tunnel cannot reach what this app does not serve.
  * **there is no HR route on the device's port.** The receiver's absolute
    rules are written for a device that retries forever on anything but a plain
    `200` (§12), and an interface has no business inside them.

**There is no login before Milestone 5.** Access control is network position,
exactly as it is for the device routes: the LAN for the guard, Tailscale for
HR. A shared passphrase would be the device's shared password one layer up
(§10, §13).

Piece 1 serves the built page and a health answer. Every screen after it is a
face on a function that already exists — nothing here computes a figure or
writes a row that a CLI command cannot.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import DATABASE_URL
from app.db import engine

# Where the Dockerfile's build stage leaves the compiled interface. On a
# developer's machine `npm run dev` serves it instead and proxies /api here.
UI_DIST = Path(os.environ.get("UI_DIST", "/srv/ui"))

# Nothing under this prefix exists on this port, and asking for it says so.
DEVICE_PREFIX = "/iclock"


def database_state() -> str:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return f"reachable at {DATABASE_URL.rsplit('@', 1)[-1]}"
    except Exception as exc:  # the page says so rather than failing to load
        return f"unreachable: {exc.__class__.__name__}"


def create_hr_app() -> FastAPI:
    app = FastAPI(
        title="HR Attendance — HR interface",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/api/health")
    def health() -> JSONResponse:
        return JSONResponse({
            "service": "hr",
            "serves": "the HR interface, on its own port",
            "device_routes": (
                "not served here — /iclock/ is a 404 on this port (SPEC §14)"
            ),
            "database": database_state(),
        })

    @app.api_route(
        "/iclock/{rest:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
        include_in_schema=False,
    )
    def no_device_routes(rest: str):
        """Answer a device path with a plain 404, deliberately.

        Without this the single-page fallback below would hand back `index.html`
        with a `200`, and a device pointed at the wrong port would read an HTML
        page as a protocol answer — the exact thing §12 says makes firmware
        retry forever or drop a batch. **Say no, clearly.**
        """
        raise HTTPException(
            status_code=404,
            detail=("the device routes are not served on this port. The "
                    "receiver has its own (SPEC §12, §14)"),
        )

    if (UI_DIST / "assets").is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=UI_DIST / "assets"),
            name="assets",
        )

    @app.get("/{path:path}", include_in_schema=False)
    def interface(path: str):
        """The built interface. Unknown paths fall back to it, because the
        screens route in the browser — except device paths, which are refused
        above, and API paths, which are handled before this."""
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="no such API route")
        index = UI_DIST / "index.html"
        if not index.is_file():
            raise HTTPException(
                status_code=503,
                detail=(f"the interface has not been built into {UI_DIST}. "
                        "The Dockerfile builds it; `npm run build` in ui/ does "
                        "the same thing by hand"),
            )
        return FileResponse(index)

    return app


hr_app = create_hr_app()
