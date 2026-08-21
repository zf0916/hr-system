"""The HR entry commands: leave and gate pass, typed off the paper forms.

`hr leave add` records one line of a leave application. **The number of days is
required**, because the form states it and nothing computes it from the range.
The screen suggests a sheet code beside the type (SPEC §9 A48) and offers none
for the four types the legend has no letter for — whatever is typed is what the
row records.

`hr gatepass add` records one gate pass. **There is no --hours option**: the
form has no hours field, and the database derives them from the two times.
"""

from __future__ import annotations

import datetime as dt
import sys

from sqlalchemy import select

from app.corrections import employee_by_number
from app.db import Session
from app.hr_entry import (
    categories,
    gate_passes,
    leave_codes,
    leave_records,
    leave_types,
    record_gate_pass,
    record_leave,
    suggested_code,
)
from app.models import Employee


def parse_date(text: str) -> dt.date:
    return dt.datetime.strptime(text, "%Y-%m-%d").date()


def parse_time(text: str) -> dt.time:
    return dt.datetime.strptime(text, "%H:%M").time()


def employee_numbers(session) -> dict:
    return {e.id: e.employee_number for e in session.scalars(select(Employee))}


def cmd_leave_add(args) -> int:
    with Session() as session:
        try:
            employee = employee_by_number(session, args.employee)
            code = args.code
            suggested = suggested_code(session, args.type)
            if code is None and not args.no_code:
                # A48: the screen offers what the type usually maps to, and
                # nothing at all for the four types with no legend letter.
                code = suggested
            record = record_leave(
                session, employee,
                leave_type_code=args.type,
                sheet_code=code,
                period_from=parse_date(args.start),
                period_to=parse_date(args.end),
                days=args.days,
                date_of_application=(parse_date(args.applied)
                                     if args.applied else None),
                reason=args.reason,
                entered_by=args.by,
                note=args.note,
            )
            session.commit()
        except ValueError as exc:
            session.rollback()
            print(f"refused: {exc}", file=sys.stderr)
            return 1

        print(f"leave {record.id} for {employee.employee_number}: "
              f"{record.period_from} → {record.period_to}, {record.days} day(s)")
        print(f"  applied for   {record.leave_type_code or '-'}")
        print(f"  sheet code    {record.sheet_code or '-'}"
              + ("   (suggested; --code changes it, --no-code leaves it empty)"
                 if code and code == suggested else ""))
        if args.type and suggested is None:
            print("  the legend has no code for this type, so none was "
                  "suggested (SPEC §6)")
        print(f"  applied on    {record.date_of_application or '-'}")
        print("  days are stored as the form states them, never counted from "
              "the range (SPEC §6)")
        print("  the SQL Account code stays empty until Accounts answers "
              "(SPEC §8)")
    return 0


def cmd_gatepass_add(args) -> int:
    with Session() as session:
        try:
            employee = employee_by_number(session, args.employee)
            record = record_gate_pass(
                session, employee,
                pass_date=parse_date(args.date),
                category_code=args.category,
                out_time=parse_time(args.out),
                in_time=parse_time(args.back),
                reason=args.reason,
                destination=args.destination,
                entered_by=args.by,
                note=args.note,
            )
            session.commit()
        except ValueError as exc:
            session.rollback()
            print(f"refused: {exc}", file=sys.stderr)
            return 1

        print(f"gate pass {record.id} for {employee.employee_number} on "
              f"{record.pass_date}")
        print(f"  {record.out_time} → {record.in_time} = {record.hours} hours")
        print(f"  {record.category_code}"
              + (f", {record.destination}" if record.destination else ""))
        print("  the hours were derived from the two times, not typed — there "
              "is no field for them (SPEC §5)")
    return 0


def cmd_leave_list(args) -> int:
    start, end = parse_date(args.start), parse_date(args.end)
    with Session() as session:
        employee_id = None
        if args.employee:
            employee_id = employee_by_number(session, args.employee).id
        numbers = employee_numbers(session)
        rows = leave_records(session, employee_id, start, end)
        if not rows:
            print(f"no leave recorded over {start} → {end}")
            return 0
        print(f"{'id':>5}  {'employee':<9}{'from':<12}{'to':<12}{'days':>5}  "
              f"{'type':<16}{'code':<6}{'applied':<12}entered by")
        for row in rows:
            print(f"{row.id:>5}  {numbers.get(row.employee_id, '?'):<9}"
                  f"{str(row.period_from):<12}{str(row.period_to):<12}"
                  f"{row.days:>5}  {row.leave_type_code or '-':<16}"
                  f"{row.sheet_code or '-':<6}"
                  f"{str(row.date_of_application or '-'):<12}{row.entered_by}")
        print("\nthe applied-for type and the sheet code are two fields, and "
              "neither is filled in from the other (SPEC §6)")
    return 0


def cmd_gatepass_list(args) -> int:
    start, end = parse_date(args.start), parse_date(args.end)
    with Session() as session:
        employee_id = None
        if args.employee:
            employee_id = employee_by_number(session, args.employee).id
        numbers = employee_numbers(session)
        rows = gate_passes(session, employee_id, start, end)
        if not rows:
            print(f"no gate pass recorded over {start} → {end}")
            return 0
        print(f"{'id':>5}  {'employee':<9}{'date':<12}{'out':<7}{'in':<7}"
              f"{'hours':>6}  {'category':<18}destination")
        total = 0.0
        for row in rows:
            total += float(row.hours or 0)
            print(f"{row.id:>5}  {numbers.get(row.employee_id, '?'):<9}"
                  f"{str(row.pass_date):<12}{str(row.out_time)[:5]:<7}"
                  f"{str(row.in_time)[:5]:<7}{row.hours:>6}  "
                  f"{row.category_code:<18}{row.destination or ''}")
        print(f"\n{len(rows)} pass(es), {total:.2f} hours — every one of them "
              "derived from its two times (SPEC §5)")
    return 0


def cmd_vocabulary(args) -> int:
    with Session() as session:
        print("leave types on the form, and the code entry suggests (A48):")
        for row in leave_types(session):
            suggested = row.suggested_sheet_code or "— none: the legend has no code"
            print(f"  {row.code:<16}{row.label:<24}{suggested}")
        print("\nsheet legend codes:")
        for row in leave_codes(session):
            print(f"  {row.code:<6}{row.label}")
        print("\ngate pass categories:")
        for row in categories(session):
            print(f"  {row.code:<18}{row.label}")
    return 0


def add_parsers(sub) -> None:
    leave = sub.add_parser("leave", help="leave, typed off the application form")
    leave_commands = leave.add_subparsers(dest="leave_command", required=True)

    add = leave_commands.add_parser("add", help="record one line of leave")
    add.add_argument("--employee", required=True)
    add.add_argument("--type", help="the tick on the form: ANNUAL, SICK, ...")
    add.add_argument("--code", help="the sheet code. Defaults to what the type "
                                    "suggests, where the legend has one")
    add.add_argument("--no-code", action="store_true",
                     help="record no sheet code at all")
    add.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD")
    add.add_argument("--to", dest="end", required=True, help="YYYY-MM-DD")
    add.add_argument("--days", required=True,
                     help="as the form states it — 1, 2, 0.5. Never computed "
                          "from the range")
    add.add_argument("--applied", help="date of application, YYYY-MM-DD")
    add.add_argument("--reason", help="required by the form for unpaid leave")
    add.add_argument("--by", required=True, help="who in HR entered it")
    add.add_argument("--note")
    add.set_defaults(func=cmd_leave_add)

    listing = leave_commands.add_parser("list", help="leave over a period")
    listing.add_argument("--from", dest="start", required=True)
    listing.add_argument("--to", dest="end", required=True)
    listing.add_argument("--employee")
    listing.set_defaults(func=cmd_leave_list)

    leave_commands.add_parser(
        "types", help="the form's ticks, the legend's codes, the four categories"
    ).set_defaults(func=cmd_vocabulary)

    gate = sub.add_parser("gatepass", help="gate passes, typed off the form")
    gate_commands = gate.add_subparsers(dest="gatepass_command", required=True)

    gate_add = gate_commands.add_parser("add", help="record one gate pass")
    gate_add.add_argument("--employee", required=True)
    gate_add.add_argument("--date", required=True, help="YYYY-MM-DD")
    gate_add.add_argument("--category", required=True,
                          help="OFFICIAL, PERSONAL, MEDICAL_TREATMENT, OTHERS")
    gate_add.add_argument("--out", required=True, help="HH:MM, off the paper")
    gate_add.add_argument("--in", dest="back", required=True, help="HH:MM")
    gate_add.add_argument("--reason")
    gate_add.add_argument("--destination")
    gate_add.add_argument("--by", required=True, help="who in HR entered it")
    gate_add.add_argument("--note")
    gate_add.set_defaults(func=cmd_gatepass_add)

    gate_list = gate_commands.add_parser("list", help="gate passes over a period")
    gate_list.add_argument("--from", dest="start", required=True)
    gate_list.add_argument("--to", dest="end", required=True)
    gate_list.add_argument("--employee")
    gate_list.set_defaults(func=cmd_gatepass_list)
