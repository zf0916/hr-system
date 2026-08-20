"""The sheet commands: render it to the screen, export it to Excel, and read
one employee's period in detail.

`render` and `export` call the same `sheet.render`. The export writes the file
and nothing reads it back — the file goes one way (SPEC §7, §13).

`detail` is the per-day punch detail §7 requires: one employee, one period,
every day of it. It is what Accounts reads instead of the punch card, and it is
not a second sheet — it renders no cells, no shading and no pages.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from app.attendance import days_for
from app.corrections import employee_by_number, punches_for
from app.db import Session
from app.schedule import effective_holiday
from app.sheet import period_for, render, to_excel, to_text


def parse_date(text: str) -> dt.date:
    return dt.datetime.strptime(text, "%Y-%m-%d").date()


def _period(session, args) -> tuple[dt.date, dt.date]:
    if args.month:
        return period_for(session, args.month)
    if not (args.start and args.end):
        raise ValueError("give --month YYYY-MM, or both --from and --to")
    return parse_date(args.start), parse_date(args.end)


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
    print("  the same render as the screen — one layout, two outputs (SPEC §7)")
    print("  export only: nothing reads this file back in (SPEC §13)")
    for note in sheet.notes:
        print(f"  note: {note}")
    return 0


def cmd_detail(args) -> int:
    """One employee, one period, every day of it — punch times, leave codes and
    manual punches in one view. This is what replaces reading the punch card."""
    start, end = parse_date(args.start), parse_date(args.end)
    with Session() as session:
        try:
            employee = employee_by_number(session, args.employee)
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        rows = {row.attendance_day: row for row in
                days_for(session, employee.id, start, end)}

        print(f"{employee.employee_number}   {start} → {end}")
        print("per-day punch detail — what Accounts reads instead of the punch "
              "card (SPEC §7)")
        print(f"\n{'date':<12}{'day':<5}{'first in':<10}{'last out':<10}"
              f"{'late':>6}  {'leave':<7}punches")
        day = start
        while day <= end:
            row = rows.get(day)
            holiday = effective_holiday(session, day)
            marks = []
            if row is not None and row.is_rest_day:
                marks.append("rest day")
            if holiday:
                marks.append(holiday.name + ("" if holiday.closes else " (worked)"))
            first = last = late = ""
            leave = "-"
            if row is not None:
                first = row.first_in.strftime("%H:%M") if row.first_in else ""
                if row.first_in_manual:
                    first += "*"
                last = row.last_out.strftime("%H:%M") if row.last_out else ""
                if row.last_out_manual:
                    last += "*"
                late = "" if row.late_minutes is None else str(row.late_minutes)
                if row.schedule_provisional and late:
                    late += "p"
            print(f"{str(day):<12}{day.strftime('%a'):<5}{first:<10}{last:<10}"
                  f"{late:>6}  {leave:<7}"
                  + (f"{row.punch_count}" if row else "-")
                  + (f"   {'; '.join(marks)}" if marks else ""))
            if args.punches and row is not None and row.punch_count:
                seen: set = set()
                for record in punches_for(session, employee.id, day):
                    counted = "counted"
                    if not record.manual and record.at in seen:
                        counted = "copy, not counted"
                    elif not record.manual:
                        seen.add(record.at)
                    who = f"  {record.who}" if record.who else ""
                    print(f"            {record.at}  {record.source:<15}"
                          f"{counted:<18}{who}")
            day += dt.timedelta(days=1)

        print("\n* entered by a person, not the device (SPEC §3)")
        print("p late minutes measured against a provisional schedule row")
        print("leave is step 5: the column exists and is empty, and nothing "
              "here invents a code")
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
