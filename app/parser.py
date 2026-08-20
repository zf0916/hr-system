"""Layer 2: punch lines out of a stored raw request.

Everything here is disposable. A parser change means bumping PARSER_VERSION and
replaying the raw layer — never re-collecting from the device (CLAUDE.md).

The line format is what the device actually sends, observed in raw_request 96
and 109 and written down in SPEC.md §12: **ten tab-separated fields and a
trailing tab**, so a split yields eleven pieces with the last one empty.

    1 <tab> 2026-08-20 11:27:27 <tab> 255 <tab> 15 <tab> 0 <tab> 0 <tab> 0 <tab> 0 <tab> 0 <tab> 0 <tab>

All ten are stored positionally and verbatim in `fields`. **Only four are given
a name** — pin, device time, status, verify — because those are the four §12 can
say the meaning of. The other six were `0` in every line ever captured, and a
name for them would invite logic that nothing has earned. Guessing names from
documentation is what put a seven-field line in §12 in the first place.

**Anything that is not this shape is a parse failure with the line kept.** No
padding, no truncation, no coercion. The raw layer still holds the bytes, so a
shape this parser refuses costs a version bump and a replay — never a
re-collection from the device.
"""

import datetime as dt
import logging

from sqlalchemy import delete, select

from app.config import PARSER_VERSION
from app.models import ParsedPunch, ParserSetting, RawRequest

log = logging.getLogger("hr.parser")

ATTLOG = "attlog"

# Only used if the parser_setting row is missing, i.e. an unseeded database.
DEFAULT_DECODE_ORDER = "utf-8,gbk,latin-1"
DEVICE_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# Observed in raw_request 96 and 109 (SPEC §12).
FIELD_COUNT = 10

# The only positions with an observed meaning. Position -> column name.
NAMED_FIELDS = {
    0: "pin",
    1: "punch_time_text",
    2: "status_code",
    3: "verify_code",
}


def decode_order(session) -> list[str]:
    row = session.get(ParserSetting, "attlog.decode_order")
    raw = row.value if row else DEFAULT_DECODE_ORDER
    return [c.strip() for c in raw.split(",") if c.strip()]


def decode_body(body: bytes, codecs: list[str]) -> tuple[str, str]:
    """Decode here, never at capture — name fields are GBK on many firmware
    builds (SPEC §12). The last codec in the list is expected to be one that
    cannot fail, so that a line always survives as *something* to look at."""
    for codec in codecs:
        try:
            return body.decode(codec), codec
        except (UnicodeDecodeError, LookupError):
            continue
    return body.decode("latin-1", errors="replace"), "latin-1/replace"


def is_attlog(raw: RawRequest) -> bool:
    return (raw.table_param or "").strip().lower() == ATTLOG


def parse_line(line: str) -> dict:
    """One punch line to one row's worth of values. Never raises.

    Anything the device can send has to survive being written down here, or a
    replay stops on it. A body that is not really an ATTLOG body — a photo
    pushed at the wrong route, a truncated batch — decodes to text carrying NUL
    bytes, which a PostgreSQL text column cannot hold. They are dropped for
    storage and the line is marked; the raw layer still has the original bytes.

    A line of the wrong shape is a row with `parse_ok` false and every piece it
    did split into kept in `fields`. The named values are still read off their
    positions, because a person reading a failed row wants to see what was
    there — but on a failed row they mean nothing, and nothing downstream reads
    a row whose `parse_ok` is false.
    """
    stored = line.replace("\x00", "")
    parts = stored.split("\t")

    # The trailing tab the device sends makes the last piece empty. It is a
    # separator, not an eleventh field, so it is not stored as one — but its
    # absence is a different shape, and a different shape is a failure.
    trailing_empty = len(parts) == FIELD_COUNT + 1 and parts[-1] == ""
    fields = parts[:FIELD_COUNT] if trailing_empty else parts

    row: dict = {
        "raw_line": stored,
        "field_count": len(parts),
        "fields": fields,
        "parse_ok": True,
        "parse_error": None,
        "punch_time": None,
    }
    for position, name in NAMED_FIELDS.items():
        value = fields[position] if position < len(fields) else None
        row[name] = (value or "").strip() or None

    problems = []
    if stored != line:
        problems.append("NUL bytes dropped for storage; the raw layer has the original")
    if not trailing_empty:
        problems.append(
            f"expected {FIELD_COUNT} tab-separated fields and a trailing tab "
            f"({FIELD_COUNT + 1} pieces, the last one empty); got {len(parts)} "
            f"pieces, the last one {parts[-1]!r}"
        )
    if not row["pin"]:
        problems.append("empty pin")

    text = row["punch_time_text"]
    if text:
        try:
            # Stored as sent. No timezone applied, no conversion (SPEC §14).
            row["punch_time"] = dt.datetime.strptime(text, DEVICE_TIME_FORMAT)
        except ValueError:
            problems.append(f"unparseable device time {text!r}")
    else:
        problems.append("empty device time")

    if problems:
        row["parse_ok"] = False
        row["parse_error"] = "; ".join(problems)
    return row


def parse_raw_request(session, raw: RawRequest) -> int:
    """Parse one stored ATTLOG body into parsed_punch rows. Returns the number
    of rows written. Callers treat a raised exception as a logged event and
    nothing more — a parse failure never affects the response (SPEC §12)."""
    if not is_attlog(raw):
        return 0

    text, codec = decode_body(raw.body, decode_order(session))
    written = 0
    for line_no, line in enumerate(text.replace("\r\n", "\n").split("\n"), start=1):
        if not line.strip():
            continue
        values = parse_line(line)
        session.add(
            ParsedPunch(
                raw_request_id=raw.id,
                parser_version=PARSER_VERSION,
                line_no=line_no,
                decoded_with=codec,
                **values,
            )
        )
        written += 1
        if not values["parse_ok"]:
            log.warning(
                "raw_request %s line %s did not parse: %s",
                raw.id,
                line_no,
                values["parse_error"],
            )
    return written


def replay(session, since_id: int = 0) -> tuple[int, int, list[int]]:
    """Rebuild layer 2 from layer 1. The raw layer is untouched and never
    re-collected. Returns (requests read, punch rows written, requests that
    could not be parsed at all).

    One request that cannot be parsed never stops the rebuild — the raw layer
    still holds it, and it can be parsed by a later parser version.
    """
    session.execute(delete(ParsedPunch).where(ParsedPunch.raw_request_id > since_id))
    session.flush()
    requests = 0
    punches = 0
    unparsable: list[int] = []
    ids = session.scalars(
        select(RawRequest.id).where(RawRequest.id > since_id).order_by(RawRequest.id)
    ).all()
    for raw_id in ids:
        raw = session.get(RawRequest, raw_id)
        if not is_attlog(raw):
            continue
        requests += 1
        savepoint = session.begin_nested()
        try:
            punches += parse_raw_request(session, raw)
            session.flush()
            savepoint.commit()
        except Exception:
            savepoint.rollback()
            unparsable.append(raw_id)
            log.exception("raw_request %s could not be parsed at all", raw_id)
    return requests, punches, unparsable
