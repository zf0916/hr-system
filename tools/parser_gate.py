#!/usr/bin/env python3
"""Parser gate — the punch line's shape, and the four names it earns.

SPEC.md §12: ten tab-separated fields and a trailing tab, so a split yields
eleven pieces with the last one empty. Observed in raw_request 96 and 109.

Two halves:

  * the shape, checked against `parse_line` directly — every wrong shape has to
    fail, with the line kept and nothing padded, truncated or coerced;
  * the real capture, checked against the database — requests 96 and 109 must
    parse with parse_ok true and the four named values as the device sent them.

The database half is skipped when the capture is not there, so this runs on a
fresh database too. Exits non-zero on the first thing that is wrong.

    uv run python tools/parser_gate.py
"""

from __future__ import annotations

import os
import sys

from app.parser import FIELD_COUNT, NAMED_FIELDS, parse_line

REAL_LINE = "1\t2026-08-20 11:27:27\t255\t15\t0\t0\t0\t0\t0\t0\t"
REAL_FIELDS = ["1", "2026-08-20 11:27:27", "255", "15", "0", "0", "0", "0", "0", "0"]

# The line SPEC §12 used to claim, from documentation. It never existed.
SEVEN_FIELD = "0090\t2026-08-18 07:58:11\t0\t1\t0\t0\t0"

failures: list[str] = []
checks = 0


def check(ok: bool, what: str, detail: str = "") -> bool:
    global checks
    checks += 1
    if ok:
        print(f"  ok    {what}")
    else:
        print(f"  FAIL  {what}" + (f" — {detail}" if detail else ""))
        failures.append(what)
    return ok


def must_fail(line: str, what: str) -> None:
    """A wrong shape fails, keeps the line, and is not made to fit."""
    row = parse_line(line)
    check(not row["parse_ok"], f"{what}: refused", f"parse_error {row['parse_error']!r}")
    check(row["parse_error"] is not None, f"{what}: says why")
    check(row["raw_line"] == line.replace("\x00", ""), f"{what}: line kept whole")
    pieces = line.replace("\x00", "").split("\t")
    check(row["field_count"] == len(pieces), f"{what}: piece count recorded as found",
          f"got {row['field_count']}, line has {len(pieces)}")
    check(row["fields"] == pieces,
          f"{what}: every piece kept, nothing padded or truncated",
          f"got {row['fields']}")


def main() -> int:
    print("-- the observed shape parses")
    row = parse_line(REAL_LINE)
    check(row["parse_ok"], "ten fields and a trailing tab: accepted",
          f"parse_error {row['parse_error']!r}")
    check(row["fields"] == REAL_FIELDS,
          "all ten fields stored positionally and verbatim", f"got {row['fields']}")
    check(len(row["fields"]) == FIELD_COUNT,
          "the trailing empty piece is not stored as an eleventh field",
          f"got {len(row['fields'])} fields")
    check(row["field_count"] == FIELD_COUNT + 1,
          "the split's piece count is recorded as eleven",
          f"got {row['field_count']}")
    check(row["pin"] == "1", "named: pin", f"got {row['pin']!r}")
    check(row["punch_time_text"] == "2026-08-20 11:27:27", "named: device time as sent",
          f"got {row['punch_time_text']!r}")
    check(str(row["punch_time"]) == "2026-08-20 11:27:27",
          "the timestamp is the same value, no timezone applied",
          f"got {row['punch_time']}")
    check(row["status_code"] == "255", "named: status", f"got {row['status_code']!r}")
    check(row["verify_code"] == "15", "named: verify", f"got {row['verify_code']!r}")
    check(set(NAMED_FIELDS.values()) == {"pin", "punch_time_text", "status_code",
                                        "verify_code"},
          "only the four observed fields have a name",
          f"named {sorted(NAMED_FIELDS.values())}")
    check(all(k not in row for k in ("workcode", "reserved_1", "reserved_2")),
          "fields five to ten have no name at all")

    print("\n-- a fingerprint punch, the other observed verify code")
    row = parse_line(REAL_LINE.replace("\t15\t", "\t1\t", 1))
    check(row["parse_ok"] and row["verify_code"] == "1",
          "verify 1 parses the same way", f"got {row['verify_code']!r}")

    print("\n-- wrong shapes, every one a failure with the line kept")
    must_fail(SEVEN_FIELD, "the old seven-field line")
    must_fail(REAL_LINE + "0\t", "twelve fields")
    must_fail(REAL_LINE.rstrip("\t"), "ten fields, trailing tab removed")
    must_fail(REAL_LINE + "x", "eleventh piece not empty")
    must_fail("0090\t2026-08-18 07:58:11", "two fields")
    must_fail("", "an empty line")
    must_fail("no separators at all", "no tabs at all")

    print("\n-- a right-shaped line can still be wrong in its values")
    row = parse_line("\t2026-08-20 11:27:27\t255\t15\t0\t0\t0\t0\t0\t0\t")
    check(not row["parse_ok"] and "empty pin" in (row["parse_error"] or ""),
          "empty pin: refused", f"parse_error {row['parse_error']!r}")
    row = parse_line("1\t20-08-2026 11:27\t255\t15\t0\t0\t0\t0\t0\t0\t")
    check(not row["parse_ok"] and "unparseable device time" in (row["parse_error"] or ""),
          "unparseable device time: refused", f"parse_error {row['parse_error']!r}")
    check(row["punch_time"] is None and row["punch_time_text"] == "20-08-2026 11:27",
          "the unparseable time is still kept as the string it was")
    row = parse_line(REAL_LINE.replace("255", "2\x0055", 1))
    check(not row["parse_ok"] and "NUL" in (row["parse_error"] or ""),
          "NUL bytes: dropped for storage and marked",
          f"parse_error {row['parse_error']!r}")

    print("\n-- the real capture, from the database")
    if os.environ.get("SKIP_DB"):
        print("  --    skipped (SKIP_DB)")
    else:
        check_capture()

    print(f"\n{checks} checks")
    if failures:
        print(f"{len(failures)} FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("clean")
    return 0


def check_capture() -> None:
    """Requests 96 and 109 are the device's own punches. They are the reason
    this parser version exists, so they are checked against the stored bytes
    rather than against a copy of them."""
    from sqlalchemy import select

    from app.db import Session
    from app.models import ParsedPunch, RawRequest

    with Session() as session:
        raws = session.scalars(
            select(RawRequest)
            .where(RawRequest.table_param == "ATTLOG",
                   RawRequest.serial_number == "PYA8262300072")
            .order_by(RawRequest.id)
        ).all()
        if not raws:
            print("  --    no device ATTLOG capture in this database, skipped")
            return
        for raw in raws:
            rows = session.scalars(
                select(ParsedPunch)
                .where(ParsedPunch.raw_request_id == raw.id)
                .order_by(ParsedPunch.line_no)
            ).all()
            label = f"raw_request {raw.id}"
            if not check(len(rows) == 1, f"{label}: one punch row", f"got {len(rows)}"):
                continue
            row = rows[0]
            check(row.parse_ok, f"{label}: parse_ok", f"parse_error {row.parse_error!r}")
            check(row.pin == "1", f"{label}: pin 1", f"got {row.pin!r}")
            sent = raw.body.decode("ascii").strip().split("\t")[1]
            check(row.punch_time_text == sent,
                  f"{label}: device time as sent ({sent})",
                  f"got {row.punch_time_text!r}")
            check(str(row.punch_time) == sent,
                  f"{label}: timestamp equals the string, unconverted",
                  f"got {row.punch_time}")
            check(row.status_code == "255", f"{label}: status 255",
                  f"got {row.status_code!r}")
            check(row.verify_code == "15", f"{label}: verify 15",
                  f"got {row.verify_code!r}")
            check(len(row.fields) == FIELD_COUNT,
                  f"{label}: ten fields stored", f"got {row.fields}")


if __name__ == "__main__":
    raise SystemExit(main())
