"""The correction commands, and the punch-detail read.

`hr corrections guard` has no option for a time, and adding one would mean
changing both this file and the check constraint behind it.

`hr corrections cancel` voids a correction **by writing a row**. There is no
command that edits one and none that deletes one, and the database refuses
both: the original punch keeps its time, its reason, its author and its stamp,
and the daily row stops counting it (SPEC §3, §13).
"""

from __future__ import annotations

import datetime as dt
import sys

from sqlalchemy import select

from app.corrections import (
    cancel_manual_punch,
    correction_counts,
    employee_by_number,
    manual_punches_in,
    punches_for,
    rebuild_attendance_days,
    record_guard_entry,
    record_hr_retroactive,
)
from app.db import Session
from app.models import CorrectionReason


def parse_date(text: str) -> dt.date:
    return dt.datetime.strptime(text, "%Y-%m-%d").date()


def cmd_guard(args) -> int:
    with Session() as session:
        try:
            employee = employee_by_number(session, args.employee)
            punch = record_guard_entry(
                session, employee, reason_code=args.reason,
                made_by=args.by, note=args.note,
            )
            session.commit()
        except ValueError as exc:
            session.rollback()
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        reason = session.get(CorrectionReason, punch.reason_code)
    print(f"guard entry for {employee.employee_number}")
    print(f"  stamped       {punch.recorded_at}  (the server's clock, not typed)")
    print(f"  attendance day {punch.attendance_day}")
    print(f"  reason        {reason.label}")
    print(f"  entered by    {punch.made_by}")
    print("  it is marked as manual wherever it is read, and counted per employee")
    return 0


def cmd_retroactive(args) -> int:
    try:
        asserted = dt.datetime.strptime(args.at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        print("--at looks like '2026-08-17 08:05:00'", file=sys.stderr)
        return 1
    with Session() as session:
        try:
            employee = employee_by_number(session, args.employee)
            punch = record_hr_retroactive(
                session, employee, asserted_time=asserted,
                reason=args.reason, made_by=args.by, note=args.note,
            )
            session.commit()
        except ValueError as exc:
            session.rollback()
            print(f"refused: {exc}", file=sys.stderr)
            return 1
    print(f"retroactive entry for {employee.employee_number}")
    print(f"  states        {punch.asserted_time}")
    print(f"  recorded      {punch.recorded_at}  (server-stamped, kept alongside)")
    print(f"  attendance day {punch.attendance_day}")
    print(f"  reason        {punch.reason}")
    print(f"  entered by    {punch.made_by}")
    return 0


def cmd_list(args) -> int:
    """The corrections over a period, so one can be found and cancelled."""
    start, end = parse_date(args.start), parse_date(args.end)
    with Session() as session:
        employee_id = None
        if args.employee:
            try:
                employee_id = employee_by_number(session, args.employee).id
            except ValueError as exc:
                print(f"refused: {exc}", file=sys.stderr)
                return 1
        records = manual_punches_in(session, employee_id, start, end)

    print(f"corrections on record, {start} to {end}")
    if not records:
        print("  none")
        return 0
    print(f"  {'id':>6}  {'day':<12}{'time':<20}{'path':<15}"
          f"{'entered by':<20}why")
    for record in records:
        at = record.at.strftime("%Y-%m-%d %H:%M:%S") if record.at else "-"
        print(f"  {record.punch_id:>6}  {str(record.attendance_day):<12}"
              f"{at:<20}{record.source:<15}{(record.who or '-'):<20}"
              f"{record.why or ''}")
        if record.cancelled:
            print(f"  {'':>6}  {'':<12}CANCELLED by {record.cancelled_by} — "
                  f"{record.cancelled_why}")
    cancelled = sum(1 for r in records if r.cancelled)
    print(f"  {len(records)} correction(s), {cancelled} cancelled")
    print("  only these can be cancelled. A device punch is a fact from the "
          "hardware (SPEC §3)")
    return 0


def cmd_cancel(args) -> int:
    with Session() as session:
        try:
            row = cancel_manual_punch(
                session, args.punch, reason=args.reason,
                cancelled_by=args.by, note=args.note,
            )
            session.commit()
        except ValueError as exc:
            session.rollback()
            print(f"refused: {exc}", file=sys.stderr)
            return 1
    print(f"correction {args.punch} cancelled")
    print(f"  by            {row.cancelled_by}")
    print(f"  because       {row.reason}")
    print(f"  recorded      {row.cancelled_at}  (server-stamped)")
    print("  the punch row is unchanged — a cancellation is a row, never an "
          "edit and never a delete (SPEC §3, §13)")
    print("  rebuild the days it touches so the figures stop counting it: "
          "hr attendance build")
    return 0


def cmd_count(args) -> int:
    start, end = parse_date(args.start), parse_date(args.end)
    with Session() as session:
        employee_id = None
        if args.employee:
            try:
                employee_id = employee_by_number(session, args.employee).id
            except ValueError as exc:
                print(f"refused: {exc}", file=sys.stderr)
                return 1
        rows = correction_counts(session, start, end, employee_id)
    print(f"manual punches per employee, {start} to {end}")
    if not rows:
        print("  none")
        return 0
    print(f"  {'employee':10} {'path':16} {'entries':>7} {'cancelled':>9}  "
          f"first        last")
    for number, path, entries, cancelled, first_day, last_day in rows:
        print(f"  {number:10} {path:16} {entries:>7} {cancelled:>9}  "
              f"{first_day}  {last_day}")
    print("  a rising count for one employee is a bad enrollment, or a process "
          "being worked around")
    print("  a cancelled correction is still counted here: the act happened, "
          "and how often somebody had to make one is the signal (SPEC §3)")
    return 0


def cmd_punches(args) -> int:
    day = parse_date(args.day)
    with Session() as session:
        try:
            employee = employee_by_number(session, args.employee)
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        records = punches_for(session, employee.id, day)

    print(f"punch detail — employee {employee.employee_number}, "
          f"attendance day {day}")
    if not records:
        print("  no punches on record. That is a fact, not an absence.")
        return 0
    print(f"  {'time':20} {'source':15} {'manual':7} {'who':22} why / evidence")
    for record in records:
        at = record.at.strftime("%Y-%m-%d %H:%M:%S") if record.at else "-"
        print(f"  {at:20} {record.source:15} "
              f"{'YES' if record.manual else 'no':7} {(record.who or '-'):22} "
              f"{record.why or record.evidence}")
        if record.cancelled:
            print(f"  {'':20} {'':15} {'':7} {'':22} CANCELLED by "
                  f"{record.cancelled_by} — {record.cancelled_why}")
        if record.manual:
            print(f"  {'':20} {'':15} {'':7} {'':22} {record.evidence}")
    manual = sum(1 for r in records if r.manual)
    cancelled = sum(1 for r in records if r.cancelled)
    print(f"  {len(records)} punches, {manual} entered by a person"
          + (f", {cancelled} cancelled and not counted" if cancelled else ""))
    print("  first in, last out, lateness and absence are not decided here")
    return 0


def cmd_rebuild_days(args) -> int:
    with Session() as session:
        changed = rebuild_attendance_days(session)
        session.commit()
    print(f"{changed} corrections moved to a different attendance day")
    return 0


def add_parsers(sub) -> None:
    corrections = sub.add_parser(
        "corrections", help="manual punches: guard entry and HR retroactive entry"
    )
    commands = corrections.add_subparsers(dest="corrections_command", required=True)

    guard = commands.add_parser(
        "guard",
        help="the employee is standing in front of the guard now; the server "
             "stamps the time and there is no option to type one",
    )
    guard.add_argument("--employee", required=True, help="employee number")
    guard.add_argument("--reason", required=True,
                       help="a reason a guard entry may give")
    guard.add_argument("--by", required=True, help="which guard")
    guard.add_argument("--note")
    guard.set_defaults(func=cmd_guard)

    retro = commands.add_parser(
        "retroactive", help="HR corrects a punch after the fact; the time is entered"
    )
    retro.add_argument("--employee", required=True)
    retro.add_argument("--at", required=True, help="'YYYY-MM-DD HH:MM:SS'")
    retro.add_argument("--reason", required=True, help="why, in words")
    retro.add_argument("--by", required=True, help="who in HR")
    retro.add_argument("--note")
    retro.set_defaults(func=cmd_retroactive)

    listing = commands.add_parser(
        "list", help="the corrections on record over a period, with their ids"
    )
    listing.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD")
    listing.add_argument("--to", dest="end", required=True, help="YYYY-MM-DD")
    listing.add_argument("--employee")
    listing.set_defaults(func=cmd_list)

    cancel = commands.add_parser(
        "cancel",
        help="void a correction by writing a row. The punch is not edited and "
             "not deleted, and the database refuses both",
    )
    cancel.add_argument("--punch", required=True, type=int,
                        help="the manual_punch id, from `hr corrections list`")
    cancel.add_argument("--reason", required=True, help="why, in words")
    cancel.add_argument("--by", required=True, help="who in HR")
    cancel.add_argument("--note")
    cancel.set_defaults(func=cmd_cancel)

    count = commands.add_parser(
        "count", help="manual punches per employee per period"
    )
    count.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD")
    count.add_argument("--to", dest="end", required=True, help="YYYY-MM-DD")
    count.add_argument("--employee")
    count.set_defaults(func=cmd_count)

    commands.add_parser(
        "rebuild-days",
        help="recompute each correction's attendance day from the schedules",
    ).set_defaults(func=cmd_rebuild_days)

    punches = sub.add_parser(
        "punches", help="a day's punch detail for one employee, device and manual"
    )
    punches.add_argument("--employee", required=True)
    punches.add_argument("--day", required=True, help="YYYY-MM-DD")
    punches.set_defaults(func=cmd_punches)
