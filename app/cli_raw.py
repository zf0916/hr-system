"""Read the raw layer without opening the database.

Layer 1 only: what arrived, when, from which serial, which table, and the
bytes. **Nothing here parses, decodes or interprets anything** — the body is
printed as bytes, because deciding what encoding it is in belongs to the parser
(SPEC §12), and `hr replay` is what runs that.

The point of this command is watching a real capture land: `hr raw` after a
push, `hr raw --id N` for the whole request.
"""

from __future__ import annotations

import sys

from sqlalchemy import select

from app.db import Session
from app.models import RawRequest

# Enough of a body to read a punch batch off the screen. A BIODATA push is
# base64 and runs to tens of kilobytes; the rest is on disk either way.
BODY_LIMIT = 2000


def _bytes_lines(body: bytes, limit: int) -> list[str]:
    """The body, as bytes, one printed line per line in it.

    `repr` escapes the tabs a punch line is separated by and any byte that is
    not printable ASCII, so nothing here has to guess at an encoding.
    """
    shown = body[:limit]
    lines = [repr(line) for line in shown.split(b"\n")]
    if len(body) > limit:
        lines.append(f"... {len(body) - limit} more bytes not shown")
    return lines


def _one_line(row: RawRequest) -> str:
    answered = (row.response_body or "").splitlines()
    first = answered[0] if answered else ""
    if len(answered) > 1:
        first += f"  (+{len(answered) - 1} more lines)"
    return (
        f"{row.id:>6}  {row.received_at.isoformat(sep=' ', timespec='seconds')}  "
        f"{(row.serial_number or '-'):<16} {(row.table_param or '-'):<9} "
        f"{row.method:<4} {row.path:<22} {row.body_bytes:>7}B  -> {first!r}"
    )


def cmd_raw(args) -> int:
    with Session() as session:
        if args.id is not None:
            row = session.get(RawRequest, args.id)
            if row is None:
                print(f"no raw request with id {args.id}", file=sys.stderr)
                return 1
            print(f"raw_request {row.id}")
            print(f"  received      {row.received_at.isoformat(sep=' ')}"
                  "   (the server's clock, on arrival)")
            print(f"  from          {row.remote_addr or '-'}")
            print(f"  request       {row.method} {row.path}")
            print(f"  query         {row.query_string or '-'}")
            print(f"  serial        {row.serial_number or '-'}")
            print(f"  table         {row.table_param or '-'}")
            print(f"  Stamp         {row.stamp_param or '-'}")
            print(f"  content-type  {row.content_type or '-'}")
            print(f"  answered      {row.response_body!r}")
            print("  headers:")
            for name, value in row.headers:
                print(f"    {name}: {value}")
            print(f"  body          {row.body_bytes} bytes, printed as bytes:")
            for line in _bytes_lines(row.body, args.max_bytes):
                print(f"    {line}")
            return 0

        query = select(RawRequest).order_by(RawRequest.id.desc())
        if args.serial:
            query = query.where(RawRequest.serial_number == args.serial)
        if args.table:
            query = query.where(RawRequest.table_param == args.table)
        if args.since_id:
            query = query.where(RawRequest.id > args.since_id)
        rows = list(session.scalars(query.limit(args.limit)))

        if not rows:
            print("nothing captured yet")
            return 0
        print(f"{'id':>6}  {'received':<25}  {'serial':<16} {'table':<9} "
              f"{'':<4} {'path':<22} {'bytes':>8}  answered")
        for row in reversed(rows):
            print(_one_line(row))
            if args.body:
                for line in _bytes_lines(row.body, args.max_bytes):
                    print(f"        {line}")
        print(f"\n{len(rows)} of the most recent requests. "
              "`hr raw --id N` for one whole request.")
    return 0


def add_parsers(sub) -> None:
    raw = sub.add_parser(
        "raw",
        help="read the raw layer: what arrived, when, from which serial, which "
             "table, and the bytes. Parses nothing",
    )
    raw.add_argument("--id", type=int, help="one request, whole")
    raw.add_argument("--limit", type=int, default=20)
    raw.add_argument("--serial", help="only this device serial, matched exactly")
    raw.add_argument("--table", help="only this table parameter, matched exactly")
    raw.add_argument("--since-id", type=int, default=0,
                     help="only requests newer than this id — poll with it to "
                          "watch a capture land")
    raw.add_argument("--body", action="store_true",
                     help="print each body as well as the summary line")
    raw.add_argument("--max-bytes", type=int, default=BODY_LIMIT,
                     help=f"how much of a body to print (default {BODY_LIMIT})")
    raw.set_defaults(func=cmd_raw)
