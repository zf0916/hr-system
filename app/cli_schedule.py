"""The schedule and calendar commands.

Everything here answers, or changes, one question: for this group, on this date,
what schedule and what calendar applied?
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from sqlalchemy import extract, select

from app.calendar_import import adjust as adjust_holiday
from app.calendar_import import describe_adjustments
from app.calendar_import import run_import as import_holidays
from app.db import Session
from app.models import EmployeeGroup, GroupSchedule, Holiday, HolidayAdjustment
from app.schedule import (
    attendance_day_for,
    break_window,
    day_context,
    effective_holiday,
    set_schedule,
)
from app.seed import PROVISIONAL_REST_WEEKDAYS, PROVISIONAL_SHIFTS
from app.xlsx_mapping import MappingError

WEEKDAYS = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}


def parse_date(text: str) -> dt.date:
    return dt.datetime.strptime(text, "%Y-%m-%d").date()


def parse_time(text: str) -> dt.time:
    return dt.datetime.strptime(text, "%H:%M").time()


def parse_rest_days(text: str) -> list[int]:
    days = [int(part) for part in text.split(",") if part.strip()]
    if any(day < 1 or day > 7 for day in days):
        raise ValueError("rest days are ISO weekdays: 1 Monday … 7 Sunday")
    return days


def describe(schedule: GroupSchedule) -> str:
    ends = "+1d" if schedule.end_next_day else ""
    parts = [
        f"{schedule.start_time.strftime('%H:%M')}–"
        f"{schedule.end_time.strftime('%H:%M')}{ends}"
    ]
    if schedule.break_start and schedule.break_end:
        parts.append(
            f"break {schedule.break_start.strftime('%H:%M')}–"
            f"{schedule.break_end.strftime('%H:%M')}"
        )
    else:
        parts.append("break not known")
    parts.append(f"grace {schedule.grace_minutes}m")
    parts.append(
        "rest " + ",".join(WEEKDAYS[d] for d in sorted(schedule.rest_weekdays or []))
    )
    if schedule.provisional:
        parts.append("PROVISIONAL")
    return " · ".join(parts)


# ---- schedule --------------------------------------------------------------


def cmd_schedule_seed_provisional(args) -> int:
    """Provisional schedules for the groups the sample list introduced, so that
    step 3 can be exercised before HR confirms anything (SPEC §9 A31)."""
    start = parse_date(args.effective_from)
    with Session() as session:
        groups = list(session.scalars(select(EmployeeGroup.code).order_by(
            EmployeeGroup.code)))
        if not groups:
            print("no employee groups exist yet — load an employee list first",
                  file=sys.stderr)
            return 1
        unknown = [g for g in groups if g not in PROVISIONAL_SHIFTS]
        if unknown:
            print(
                f"no provisional shift is defined for {unknown}. Guessing one "
                "would invent a schedule HR has not given — set it explicitly "
                "with `hr schedule set`.",
                file=sys.stderr,
            )
            return 1
        made = 0
        for group in groups:
            if session.scalars(select(GroupSchedule).where(
                    GroupSchedule.group_code == group)).first():
                print(f"  {group}: already has a schedule, left alone")
                continue
            shift = dict(PROVISIONAL_SHIFTS[group])
            note = shift.pop("note")
            set_schedule(
                session, group, start,
                rest_weekdays=list(PROVISIONAL_REST_WEEKDAYS),
                grace_minutes=0, provisional=True,
                source="provisional seed (SPEC §9 A31)", note=note, **shift,
            )
            made += 1
            print(f"  {group}: from {start} — provisional")
        session.commit()
    print(f"{made} provisional schedules created. All marked provisional.")
    return 0


def cmd_schedule_set(args) -> int:
    fields = {
        "start_time": parse_time(args.start),
        "end_time": parse_time(args.end),
        "end_next_day": args.end_next_day,
        "grace_minutes": args.grace,
        "rest_weekdays": parse_rest_days(args.rest_days),
        "window_before_minutes": args.window_before,
        "window_after_minutes": args.window_after,
        "provisional": not args.confirmed,
        "source": args.source,
        "note": args.note,
        "break_start_next_day": args.break_start_next_day,
        "break_end_next_day": args.break_end_next_day,
    }
    if args.brk:
        try:
            start_text, end_text = args.brk.split("-")
        except ValueError:
            print("--break looks like 12:30-13:15", file=sys.stderr)
            return 1
        fields["break_start"] = parse_time(start_text)
        fields["break_end"] = parse_time(end_text)

    with Session() as session:
        if session.get(EmployeeGroup, args.group) is None:
            print(f"{args.group!r} is not a known group", file=sys.stderr)
            return 1
        try:
            schedule = set_schedule(
                session, args.group, parse_date(args.effective_from), **fields
            )
            session.commit()
        except Exception as exc:
            session.rollback()
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        print(f"{args.group}: from {schedule.effective_from} — {describe(schedule)}")
        previous = session.scalars(
            select(GroupSchedule)
            .where(
                GroupSchedule.group_code == args.group,
                GroupSchedule.effective_to == schedule.effective_from
                - dt.timedelta(days=1),
            )
        ).first()
        if previous:
            print(
                f"  the row before it now ends {previous.effective_to} and still "
                "renders every date it covered"
            )
    return 0


def cmd_schedule_show(args) -> int:
    day = parse_date(args.date)
    with Session() as session:
        context = day_context(session, args.group, day)
        if context.schedule is None:
            print(f"{args.group} on {day} ({WEEKDAYS[day.isoweekday()]}): "
                  "no schedule in force")
            if context.holiday:
                print(f"  calendar: {context.holiday.name}")
            return 1
        schedule = context.schedule
        print(f"{args.group} on {day} ({WEEKDAYS[day.isoweekday()]})")
        print(f"  schedule      {describe(schedule)}")
        print(f"  in force      {schedule.effective_from} to "
              f"{schedule.effective_to or 'open'}  (row {schedule.id})")
        print(f"  shift         {context.shift_start} to {context.shift_end}")
        window = break_window(schedule, day)
        if window:
            print(f"  break         {window[0]} to {window[1]}")
        print(f"  punch window  {context.window_start} to {context.window_end}")
        print(f"  rest day      {context.is_rest_day}")
        if context.holiday:
            holiday = context.holiday
            flags = []
            if holiday.provisional:
                flags.append("PROVISIONAL")
            if holiday.adjusted:
                flags.append(f"adjusted by {holiday.made_by}: {holiday.reason}")
            print(f"  holiday       {holiday.name} ({holiday.scope_code}), "
                  f"closes={holiday.closes}"
                  + (f"  [{'; '.join(flags)}]" if flags else ""))
        else:
            print("  holiday       none")
        print(f"  working day   {context.working_day}")
    return 0


def cmd_schedule_attendance_day(args) -> int:
    punched_at = dt.datetime.strptime(args.at, "%Y-%m-%d %H:%M:%S")
    with Session() as session:
        result = attendance_day_for(session, args.group, punched_at)
        print(f"{args.group}: a punch at {punched_at} "
              f"({WEEKDAYS[punched_at.isoweekday()]})")
        print(f"  attendance day  {result.date} "
              f"({WEEKDAYS[result.date.isoweekday()]})")
        print(f"  schedule row    {result.schedule_id}")
        print(f"  {result.note}")
    return 0


# ---- calendar --------------------------------------------------------------


def cmd_calendar_import(args) -> int:
    source = Path(args.file)
    mapping_path = Path(args.mapping)
    for path in (source, mapping_path):
        if not path.exists():
            print(f"no such file: {path}", file=sys.stderr)
            return 1

    with Session() as session:
        try:
            result = import_holidays(
                session, source, mapping_path,
                year=args.year, replace=args.replace,
                allow_new=set(args.allow_new or ()),
                provisional=not args.confirmed,
            )
        except MappingError as exc:
            print(f"the mapping is wrong, so nothing was read:\n  {exc}",
                  file=sys.stderr)
            return 1
        if result.problems:
            print(f"{len(result.problems)} problems with {source.name} — "
                  "nothing was written", file=sys.stderr)
            for problem in result.problems[:60]:
                print(f"  {problem}", file=sys.stderr)
            return 1
        session.commit()

    if result.headers:
        print("mapping (header text is echoed, never matched on):")
        for name, header in result.headers.items():
            print(f"  {name:8} <- {header!r}")
    if result.replaced:
        print(f"  replaced {result.replaced} holidays previously loaded for {args.year}")
    closes = sum(1 for s in result.staged if s.closes)
    print(f"  {result.written} holidays loaded for {args.year} "
          f"({closes} close the factory, {result.written - closes} worked)")
    print(f"  marked {'CONFIRMED' if args.confirmed else 'PROVISIONAL'}")
    if result.new_scopes:
        print(f"  new scopes added: {result.new_scopes}")
    if result.adjustments:
        print("  per-date adjustments were kept, not discarded:")
        for date, verdict, detail in result.adjustments:
            print(f"    {date} {verdict}: {detail}")
    return 0


def cmd_calendar_adjust(args) -> int:
    closes = None
    if args.closes is not None:
        closes = args.closes == "yes"
    with Session() as session:
        try:
            adjustment = adjust_holiday(
                session,
                date=parse_date(args.date),
                reason=args.reason,
                made_by=args.by,
                remove=args.remove,
                closes=closes,
                name=args.name,
                scope=args.scope,
            )
            session.commit()
        except ValueError as exc:
            session.rollback()
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        holiday = effective_holiday(session, adjustment.holiday_date)
    print(f"{adjustment.holiday_date}: {adjustment.action} by {adjustment.made_by} "
          f"— {adjustment.reason}")
    if holiday:
        print(f"  the calendar now says: {holiday.name} ({holiday.scope_code}), "
              f"closes={holiday.closes}  [{holiday.source}]")
    else:
        print("  the calendar now says: not a holiday")
    print("  a re-upload of the year keeps this row and reports it")
    return 0


def cmd_calendar_show(args) -> int:
    with Session() as session:
        if args.date:
            day = parse_date(args.date)
            holiday = effective_holiday(session, day)
            if holiday is None:
                print(f"{day}: not a holiday")
                return 0
            print(f"{day}: {holiday.name} ({holiday.scope_code}) "
                  f"closes={holiday.closes} "
                  f"{'PROVISIONAL' if holiday.provisional else 'confirmed'} "
                  f"[{holiday.source}]")
            if holiday.adjusted:
                print(f"  adjusted by {holiday.made_by}: {holiday.reason}")
            return 0

        year = args.year
        dates = set(session.scalars(
            select(Holiday.holiday_date).where(
                extract("year", Holiday.holiday_date) == year)
        ))
        dates |= set(session.scalars(
            select(HolidayAdjustment.holiday_date).where(
                extract("year", HolidayAdjustment.holiday_date) == year)
        ))
        if not dates:
            print(f"no calendar loaded for {year}")
            return 0
        print(f"calendar for {year}")
        for day in sorted(dates):
            holiday = effective_holiday(session, day)
            if holiday is None:
                print(f"  {day} {WEEKDAYS[day.isoweekday()]}  (removed by adjustment)")
                continue
            flags = []
            if holiday.provisional:
                flags.append("PROVISIONAL")
            if holiday.adjusted:
                flags.append("adjusted")
            print(f"  {day} {WEEKDAYS[day.isoweekday()]}  "
                  f"closes={'yes' if holiday.closes else 'NO '}  "
                  f"{holiday.name} ({holiday.scope_code})"
                  + (f"  [{', '.join(flags)}]" if flags else ""))
        standing = describe_adjustments(session, year)
        if standing:
            print("  adjustments on record:")
            for date, verdict, detail in standing:
                print(f"    {date} {verdict}: {detail}")
    return 0


def add_parsers(sub) -> None:
    schedule = sub.add_parser("schedule", help="schedules per group, effective-dated")
    commands = schedule.add_subparsers(dest="schedule_command", required=True)

    seed_provisional = commands.add_parser(
        "seed-provisional",
        help="provisional schedules for the known groups, marked provisional",
    )
    seed_provisional.add_argument("--from", dest="effective_from", default="2015-01-01")
    seed_provisional.set_defaults(func=cmd_schedule_seed_provisional)

    set_cmd = commands.add_parser(
        "set", help="a schedule for a group from a date; a change is a new row"
    )
    set_cmd.add_argument("--group", required=True)
    set_cmd.add_argument("--from", dest="effective_from", required=True,
                         help="YYYY-MM-DD")
    set_cmd.add_argument("--start", required=True, help="HH:MM")
    set_cmd.add_argument("--end", required=True, help="HH:MM")
    set_cmd.add_argument("--end-next-day", action="store_true",
                         help="the shift ends on the following day")
    set_cmd.add_argument("--break", dest="brk", help="HH:MM-HH:MM")
    set_cmd.add_argument("--break-start-next-day", action="store_true")
    set_cmd.add_argument("--break-end-next-day", action="store_true")
    set_cmd.add_argument("--grace", type=int, default=0, help="minutes (A4)")
    set_cmd.add_argument("--rest-days", default="7",
                         help="ISO weekdays, 1 Monday … 7 Sunday, e.g. 7 or 6,7")
    set_cmd.add_argument("--window-before", type=int, default=240,
                         help="minutes before the shift a punch still counts (A30)")
    set_cmd.add_argument("--window-after", type=int, default=240)
    set_cmd.add_argument("--confirmed", action="store_true",
                         help="HR has confirmed this; without it the row is provisional")
    set_cmd.add_argument("--source")
    set_cmd.add_argument("--note")
    set_cmd.set_defaults(func=cmd_schedule_set)

    show = commands.add_parser(
        "show", help="what schedule and calendar applied for a group on a date"
    )
    show.add_argument("--group", required=True)
    show.add_argument("--date", required=True, help="YYYY-MM-DD")
    show.set_defaults(func=cmd_schedule_show)

    attendance = commands.add_parser(
        "attendance-day", help="which attendance day a punch belongs to"
    )
    attendance.add_argument("--group", required=True)
    attendance.add_argument("--at", required=True, help="'YYYY-MM-DD HH:MM:SS'")
    attendance.set_defaults(func=cmd_schedule_attendance_day)

    calendar = sub.add_parser("calendar", help="public holidays and closures")
    calendar_commands = calendar.add_subparsers(dest="calendar_command", required=True)

    import_cmd = calendar_commands.add_parser(
        "import", help="load a year of holidays from a spreadsheet"
    )
    import_cmd.add_argument("file")
    import_cmd.add_argument("--mapping", required=True)
    import_cmd.add_argument("--year", type=int, required=True)
    import_cmd.add_argument("--replace", action="store_true",
                            help="replace the year's uploaded holidays; per-date "
                                 "adjustments are kept and reported")
    import_cmd.add_argument("--allow-new", action="append", choices=("scope",),
                            metavar="KIND")
    import_cmd.add_argument("--confirmed", action="store_true",
                            help="this is HR's real list, not a provisional one")
    import_cmd.set_defaults(func=cmd_calendar_import)

    adjust_cmd = calendar_commands.add_parser(
        "adjust", help="change one date; survives a re-upload of the year"
    )
    adjust_cmd.add_argument("--date", required=True, help="YYYY-MM-DD")
    adjust_cmd.add_argument("--closes", choices=("yes", "no"))
    adjust_cmd.add_argument("--name")
    adjust_cmd.add_argument("--scope")
    adjust_cmd.add_argument("--remove", action="store_true",
                            help="this date is not a holiday after all")
    adjust_cmd.add_argument("--reason", required=True)
    adjust_cmd.add_argument("--by", required=True, help="who decided")
    adjust_cmd.set_defaults(func=cmd_calendar_adjust)

    show_cmd = calendar_commands.add_parser("show", help="the effective calendar")
    show_cmd.add_argument("--year", type=int)
    show_cmd.add_argument("--date")
    show_cmd.set_defaults(func=cmd_calendar_show)
