"""The daily attendance commands: build the rows, read the rows.

`build` is a rebuild every time. There is no incremental path, because a
schedule correction has to be able to move a figure that is already written
(SPEC §3, §4).

There is no total here on purpose. Every period total is a query over these
rows (SPEC §3), and no period boundary is confirmed (BUILD.md parked).
"""

from __future__ import annotations

import datetime as dt
import sys

from sqlalchemy import select

from app.attendance import build_days, days_for, rows_in
from app.corrections import employee_by_number, punches_for
from app.db import Session
from app.models import Employee


def parse_date(text: str) -> dt.date:
    return dt.datetime.strptime(text, "%Y-%m-%d").date()


def _figure(value, provisional: bool) -> str:
    """A figure that rests on a provisional schedule row says so wherever it is
    printed. Every seeded schedule is provisional until HR confirms it."""
    if value is None:
        return "-"
    return f"{value}{' (provisional)' if provisional else ''}"


def cmd_build(args) -> int:
    start, end = parse_date(args.start), parse_date(args.end)
    with Session() as session:
        employee_ids = None
        if args.employee:
            employee_ids = [employee_by_number(session, args.employee).id]
        try:
            built = build_days(session, start, end, employee_ids)
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        session.commit()

    counted: dict[str, int] = {}
    for item in built:
        counted[item.status_code] = counted.get(item.status_code, 0) + 1
    print(f"built {len(built)} rows for {start} → {end}")
    for code, count in sorted(counted.items()):
        print(f"  {code:18} {count}")
    print("  a status is what the punches amount to. Absence is not one of them "
          "— it needs leave, which is step 5")
    return 0


def cmd_show(args) -> int:
    start = parse_date(args.start)
    end = parse_date(args.end) if args.end else start
    with Session() as session:
        if args.employee:
            employee = employee_by_number(session, args.employee)
            rows = days_for(session, employee.id, start, end)
            numbers = {employee.id: employee.employee_number}
        else:
            rows = rows_in(session, start, end)
            numbers = {
                e.id: e.employee_number
                for e in session.scalars(select(Employee))
            }

        if not rows:
            print(f"no daily attendance rows for {start} → {end}. "
                  "`hr attendance build --from ... --to ...` first")
            return 0

        for row in rows:
            provisional = row.schedule_provisional
            print(f"{row.attendance_day}  {numbers.get(row.employee_id, '?'):>6}  "
                  f"{row.status_code}")
            scheduled = "-"
            if row.scheduled_start:
                scheduled = (
                    f"{row.scheduled_start.time()}–{row.scheduled_end.time()}"
                    f" +{row.grace_minutes}m grace"
                )
            print(f"    group {row.group_code or '-':<12} schedule {scheduled}"
                  f"{' (provisional row)' if provisional else ''}")
            if row.is_rest_day or row.holiday_name:
                calendar = []
                if row.is_rest_day:
                    calendar.append("rest day")
                if row.holiday_name:
                    calendar.append(
                        f"{row.holiday_name}"
                        f"{', factory closed' if row.holiday_closes else ', worked'}"
                    )
                print(f"    calendar      {'; '.join(calendar)}")
            print(f"    first in      {row.first_in or '-'}"
                  f"   {row.first_in_source or ''}"
                  f"{'  [entered by a person]' if row.first_in_manual else ''}")
            print(f"    last out      {row.last_out or '-'}"
                  f"   {row.last_out_source or ''}"
                  f"{'  [entered by a person]' if row.last_out_manual else ''}")
            print(f"    punches       {row.punch_count} "
                  f"({row.device_punch_count} device, "
                  f"{row.manual_punch_count} manual)")
            print(f"    late minutes  {_figure(row.late_minutes, provisional)}"
                  "   a figure, not a deduction (SPEC §5)")
            if row.note:
                print(f"    note          {row.note}")
            if args.detail:
                # The punch detail is the whole record, copies included — the
                # raw and parsed layers keep every push on purpose. Which of
                # them the day's figures counted is marked here rather than
                # left to be worked out (SPEC §12, §9 A37).
                seen: set = set()
                for record in punches_for(session, row.employee_id,
                                          row.attendance_day):
                    counted = "counted"
                    if not record.manual and record.at in seen:
                        counted = "copy, not counted"
                    elif not record.manual:
                        seen.add(record.at)
                    who = f"  {record.who}" if record.who else ""
                    print(f"      {record.at}  {record.source:<15} "
                          f"{counted:<18}{who}")
    return 0


def add_parsers(sub) -> None:
    attendance = sub.add_parser(
        "attendance", help="daily attendance: one row per employee per day"
    )
    commands = attendance.add_subparsers(dest="attendance_command", required=True)

    build = commands.add_parser(
        "build",
        help="rebuild the rows for a date range from punches, corrections and "
             "the schedule in force on each day",
    )
    build.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD")
    build.add_argument("--to", dest="end", required=True, help="YYYY-MM-DD")
    build.add_argument("--employee", help="one employee number, else everyone "
                                         "employed on each day")
    build.set_defaults(func=cmd_build)

    show = commands.add_parser("show", help="read the rows")
    show.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD")
    show.add_argument("--to", dest="end", help="YYYY-MM-DD, default the same day")
    show.add_argument("--employee")
    show.add_argument("--detail", action="store_true",
                      help="list the punches each figure came from")
    show.set_defaults(func=cmd_show)
