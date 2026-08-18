"""The application.

Step 1 is capture only. There is no HR interface, no employee lookup and no
authentication here — the device protocol has no credential mechanism, access
control is network position, and the device routes are never routed through the
tunnel (SPEC §12, §14).
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.routes_iclock import router as iclock_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

log = logging.getLogger("hr")

app = FastAPI(title="HR Attendance — capture", docs_url=None, redoc_url=None)

# Trailing-slash redirects are on by default and a redirect makes the firmware
# retry forever or drop the batch. Turned off here, *and* a catch-all route in
# routes_iclock.py — neither alone is enough (SPEC §12).
app.router.redirect_slashes = False

app.include_router(iclock_router)


def _device_route(request: Request) -> bool:
    return request.url.path.startswith("/iclock")


def _plain(text: str, status: int) -> PlainTextResponse:
    return PlainTextResponse(text, status_code=status, media_type="text/plain")


# No exception handler that returns JSON, and nothing but 200 on a device route
# (SPEC §12). FastAPI's defaults return JSON for both of the below, so both are
# replaced rather than left in place.
@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> PlainTextResponse:
    log.exception("unhandled error on %s", request.url)
    return _plain("OK", 200) if _device_route(request) else _plain("error", 500)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException) -> PlainTextResponse:
    log.warning("%s on %s: %s", exc.status_code, request.url, exc.detail)
    if _device_route(request):
        return _plain("OK", 200)
    return _plain(str(exc.detail), exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> PlainTextResponse:
    log.warning("validation error on %s: %s", request.url, exc)
    return _plain("OK", 200) if _device_route(request) else _plain("bad request", 400)
