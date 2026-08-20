"""The receiver. Every route in SPEC.md §12, and its absolute rules.

§12 is what the firmware does, not a design. It is unverified against real
hardware and is implemented exactly as written anyway: the raw layer is what
makes that safe, because a wrong assumption costs a replay rather than lost
punches.

The rules this file exists to obey:

  * 200 and plain text only. No redirect, no 401, no HTML, no JSON.
  * No request-body validation. Bodies are tab-separated text or raw binary.
  * No auth middleware. Access control is network position.
  * Never decode the body at capture. Store bytes.
  * A parse failure never affects the response: store, respond OK, log it.
  * Never deduplicate, normalise or drop anything at the raw layer.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from app.db import Session
from app.commands import hand_out, next_for, record_results
from app.models import Device, DeviceOption, RawRequest
from app.parser import parse_raw_request

log = logging.getLogger("hr.iclock")

router = APIRouter()

# Every method on every device route. A method the firmware sends that we did
# not anticipate must not become a 405 (SPEC §12: only 200).
ALL_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

OK = "OK"


def _ok(text: str = OK) -> PlainTextResponse:
    return PlainTextResponse(text, status_code=200, media_type="text/plain")


def _param(request: Request, name: str) -> str | None:
    """Read a query parameter verbatim. Firmware casing varies between builds,
    so the *name* is matched case-insensitively; the value is never touched."""
    lowered = name.lower()
    for key, value in request.query_params.multi_items():
        if key.lower() == lowered:
            return value
    return None


def _headers(request: Request) -> list[list[str]]:
    """Whole, in order, duplicates kept."""
    return [
        [k.decode("latin-1"), v.decode("latin-1")] for k, v in request.headers.raw
    ]


def count_lines(body: bytes) -> int:
    """The n in `OK: {n}`. Counted on bytes — the body is never decoded at
    capture, and this number must not depend on the parser succeeding."""
    return sum(1 for line in body.split(b"\n") if line.strip(b"\r \t"))


def device_options(session, serial: str | None) -> list[str]:
    """The Key=Value lines of the handshake, from rows (SPEC §9 A26).

    A row naming this serial wins over the row with the same key and no serial.
    """
    rows = session.scalars(
        select(DeviceOption)
        .where(
            (DeviceOption.serial_number.is_(None))
            | (DeviceOption.serial_number == serial)
        )
        .order_by(DeviceOption.sort_order, DeviceOption.id)
    ).all()
    chosen: dict[str, DeviceOption] = {}
    for row in rows:
        current = chosen.get(row.key)
        if current is None or (current.serial_number is None and row.serial_number):
            chosen[row.key] = row
    ordered = sorted(chosen.values(), key=lambda r: (r.sort_order, r.id))
    return [f"{r.key}={r.value}" for r in ordered]


def response_for(request: Request, session, body: bytes, serial: str | None,
                 table: str | None) -> str:
    """What §12 says to answer. Nothing here inspects the body's meaning."""
    path = request.url.path.rstrip("/")

    if path.endswith("/iclock/cdata") and request.method == "GET":
        lines = [f"GET OPTION FROM: {serial or ''}"] + device_options(session, serial)
        return "\n".join(lines)

    if path.endswith("/iclock/cdata") and (table or "").strip().lower() == "attlog":
        return f"OK: {count_lines(body)}"

    if path.endswith("/iclock/getrequest") and serial:
        # At most one command per poll, oldest first, for this serial only.
        # One at a time is the conservative reading of a format nobody has
        # tested (SPEC §9 A46): a device that only acts on the first line loses
        # nothing, because the rest are still queued for the next poll, which
        # is seconds away.
        #
        # The hand-out is marked in this same transaction as the raw request
        # that asked for it, so a command cannot leave without the request that
        # took it being on the record.
        command = next_for(session, serial)
        if command is not None:
            return hand_out(session, command)

    # OPERLOG, ATTPHOTO, fdata, devicecmd, getrequest with nothing queued, and
    # every route the firmware has that we have not seen: plain OK. A device
    # with no command waiting gets exactly what it got before this step.
    return OK


async def handle(request: Request) -> PlainTextResponse:
    """One funnel for every /iclock/ route: store first, answer second, parse
    third. The order is the point."""
    body = await request.body()
    serial = _param(request, "SN")
    table = _param(request, "table")
    stamp = _param(request, "Stamp")

    text = OK
    raw_id = None
    try:
        with Session() as session:
            try:
                text = response_for(request, session, body, serial, table)
            except Exception:
                # Answering wrongly loses a batch. Answering OK never does.
                log.exception("failed to build a response for %s", request.url)
                text = OK

            raw = RawRequest(
                remote_addr=request.client.host if request.client else None,
                method=request.method,
                path=request.url.path,
                query_string=request.url.query or "",
                headers=_headers(request),
                content_type=request.headers.get("content-type"),
                body=body,
                body_bytes=len(body),
                serial_number=serial,
                table_param=table,
                stamp_param=stamp,
                response_body=text,
            )
            session.add(raw)
            session.commit()
            raw_id = raw.id

            if serial is not None and session.get(Device, serial) is None:
                # Logged, never rejected (SPEC §12).
                log.warning(
                    "request from unknown serial %r (raw_request %s) — answered 200 OK",
                    serial,
                    raw_id,
                )
    except Exception:
        log.exception("capture failed for %s — answering %r anyway", request.url, text)
        return _ok(text)

    if raw_id is not None and (table or "").strip().lower() == "attlog":
        try:
            with Session() as session:
                stored = session.get(RawRequest, raw_id)
                written = parse_raw_request(session, stored)
                session.commit()
                log.info("raw_request %s parsed into %s punch rows", raw_id, written)
        except Exception:
            # Store, respond OK, log it (SPEC §12). The raw row is already
            # committed, so a replay fixes this without the device involved.
            log.exception("parse failed for raw_request %s", raw_id)

    if raw_id is not None and request.url.path.rstrip("/").endswith(
            "/iclock/devicecmd"):
        # Reading the result happens after the answer, for the same reason
        # parsing does: the response is already `OK`, and a result this code
        # cannot make sense of must not become an error the device sees
        # (SPEC §12).
        try:
            with Session() as session:
                stored = session.get(RawRequest, raw_id)
                written = record_results(session, stored)
                session.commit()
                log.info("raw_request %s carried %s command result(s)",
                         raw_id, len(written))
        except Exception:
            log.exception("could not record command results for raw_request %s",
                          raw_id)

    return _ok(text)


# GET  /iclock/cdata?SN=&options=all&pushver=&language=  -> GET OPTION FROM: SN
# POST /iclock/cdata?SN=&table=ATTLOG&Stamp=             -> OK: {n}
# POST /iclock/cdata?SN=&table=OPERLOG&Stamp=            -> OK
# POST /iclock/cdata?SN=&table=ATTPHOTO                  -> OK
router.add_api_route("/iclock/cdata", handle, methods=ALL_METHODS)

# GET /iclock/getrequest?SN=  -> OK, or C:{id}:{CMD} when one is queued (step 8)
router.add_api_route("/iclock/getrequest", handle, methods=ALL_METHODS)

# POST /iclock/devicecmd?SN=  -> OK, and the result is recorded downstream
router.add_api_route("/iclock/devicecmd", handle, methods=ALL_METHODS)

# POST /iclock/fdata  -> OK
router.add_api_route("/iclock/fdata", handle, methods=ALL_METHODS)

# Catch-all. Required *in addition to* turning trailing-slash redirects off —
# neither alone is enough (SPEC §12). Registered last so the named routes win.
router.add_api_route("/iclock/{rest:path}", handle, methods=ALL_METHODS)
