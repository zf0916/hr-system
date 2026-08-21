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

**Every route here is one call into `app.screens`.** That module is the only
part of the application this file imports, and it hands back finished answers —
so there is nothing on this side of the wall to compute a figure from. A screen
is a face on a function that already exists; it never becomes a second place
where the answer is worked out.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import screens
from app.db import Session, database_state

# Where the Dockerfile's build stage leaves the compiled interface. On a
# developer's machine `npm run dev` serves it instead and proxies /api here.
UI_DIST = Path(os.environ.get("UI_DIST", "/srv/ui"))

# Nothing under this prefix exists on this port, and asking for it says so.
DEVICE_PREFIX = "/iclock"


def _read(function, session, **arguments):
    """Call one screen function and turn its refusal into a 400.

    A refusal is a sentence a person can read — "give a month as YYYY-MM" —
    and it is written where the rule lives, not here.
    """
    try:
        return function(session, **arguments)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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

    # ---- the read-only screens (piece 2) --------------------------------
    #
    # **Each of these is one call into `app.screens` and nothing else.** No
    # loop over cells, no date arithmetic, no formatting, no totals. That is
    # what holds up §7's claim that the screen and the Excel file cannot
    # disagree: this layer is never given the ingredients to compute a second
    # answer, only the answer.

    @app.get("/api/employees")
    def employees(on: str | None = None, section: str | None = None):
        with Session() as session:
            return JSONResponse(_read(
                screens.roster, session, on_date=on, section=section))

    @app.get("/api/sheet")
    def sheet(month: str | None = None,
              start: str | None = Query(None, alias="from"),
              end: str | None = Query(None, alias="to"),
              section: str | None = None):
        with Session() as session:
            return JSONResponse(_read(
                screens.sheet_screen, session, month=month, start=start,
                end=end, section=section))

    @app.get("/api/sheet.xlsx")
    def sheet_download(month: str | None = None,
                       start: str | None = Query(None, alias="from"),
                       end: str | None = Query(None, alias="to"),
                       section: str | None = None):
        """The Excel file, exactly as `hr sheet export` writes it.

        The bytes are not made here and are not touched on the way out.
        """
        with Session() as session:
            name, body = _read(screens.sheet_file, session, month=month,
                               start=start, end=end, section=section)
        return Response(
            content=body,
            media_type=("application/vnd.openxmlformats-officedocument"
                        ".spreadsheetml.sheet"),
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.get("/api/employees/{employee_number}/detail")
    def employee_detail(employee_number: str, month: str | None = None,
                        start: str | None = Query(None, alias="from"),
                        end: str | None = Query(None, alias="to"),
                        punches: bool = True):
        with Session() as session:
            return JSONResponse(_read(
                screens.day_detail, session, employee_number=employee_number,
                month=month, start=start, end=end, with_punches=punches))

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
