"""The sheet commands: render it to the screen, export it to Excel, and read
one employee's period in detail.

`render` and `export` call the same `sheet.render`. The export writes the file
and nothing reads it back — the file goes one way (SPEC §7, §13).

`detail` is the per-day punch detail §7 requires: one employee, one period,
every day of it. It is what Accounts reads instead of the punch card, and it is
not a second sheet — it renders no cells, no shading and no pages. It is built
in `app.detail`, which the browser's detail screen also draws.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from app.corrections import employee_by_number
from app.db import Session
from app.detail import render_detail
from app.detail import to_text as detail_text
from app.sheet import render, resolve_period, to_excel, to_text


def parse_date(text: str) -> dt.date:
    return dt.datetime.strptime(text, "%Y-%m-%d").date()


def _period(session, args) -> tuple[dt.date, dt.date]:
    return resolve_period(session, args.month, args.start, args.end)


def cmd_render(args) -> int:
    with Session() as session:
        try:
            start, end = _period(session, args)
            sheet = render(session, start, end, section_code=args.section)
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        print(to_text(sheet))
    return 0


def cmd_export(args) -> int:
    out = Path(args.out)
    with Session() as session:
        try:
            start, end = _period(session, args)
            sheet = render(session, start, end, section_code=args.section)
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        to_excel(sheet, out)
    print(f"wrote {out}")
    print(f"  {sheet.headcount} employees, {len(sheet.columns)} days, "
          f"{sheet.page_count} page(s)")
    print("  the same render the screen draws — one layout, every "
          "output (SPEC §7)")
    print("  export only: nothing reads this file back in (SPEC §13)")
    for note in sheet.notes:
        print(f"  note: {note}")
    return 0


def cmd_detail(args) -> int:
    """One employee, one period, every day of it — punch times, leave codes and
    manual punches in one view. This is what replaces reading the punch card.

    The view is built in `app.detail` and drawn here, which is the same
    arrangement as the sheet: **the terminal and the browser draw one object,
    so a figure cannot differ between them** (SPEC §7).
    """
    start, end = parse_date(args.start), parse_date(args.end)
    with Session() as session:
        try:
            employee = employee_by_number(session, args.employee)
            built = render_detail(session, employee, start, end,
                                  with_punches=args.punches)
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        print(detail_text(built, with_punches=args.punches))
    return 0


def add_parsers(sub) -> None:
    sheet = sub.add_parser(
        "sheet", help="the attendance sheet: screen, Excel export, per-day detail"
    )
    commands = sheet.add_subparsers(dest="sheet_command", required=True)

    def period_args(parser) -> None:
        parser.add_argument("--month", help="YYYY-MM, expanded by the period rule")
        parser.add_argument("--from", dest="start", help="YYYY-MM-DD")
        parser.add_argument("--to", dest="end", help="YYYY-MM-DD")
        parser.add_argument("--section", help="one section only")

    show = commands.add_parser("render", help="the screen — the system")
    period_args(show)
    show.set_defaults(func=cmd_render)

    export = commands.add_parser(
        "export", help="the Excel file — the record HR files. Export only")
    period_args(export)
    export.add_argument("--out", required=True, help="path to write the .xlsx to")
    export.set_defaults(func=cmd_export)

    detail = commands.add_parser(
        "detail",
        help="one employee, one period, every day of it — what Accounts reads "
             "instead of the punch card",
    )
    detail.add_argument("--employee", required=True)
    detail.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD")
    detail.add_argument("--to", dest="end", required=True, help="YYYY-MM-DD")
    detail.add_argument("--punches", action="store_true",
                        help="list every punch behind each day")
    detail.set_defaults(func=cmd_detail)
