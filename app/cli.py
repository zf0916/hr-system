"""One command creates the database and seeds it; one command replays.

There are no migrations until the parallel run starts. Until then the schema
changes by dropping and recreating (BUILD.md, "Data and migrations"). From the
first day of the parallel run this command stops being safe: raw device capture
cannot be recreated, and `seed` would destroy it.
"""

import argparse
import datetime as dt
import sys
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.config import DATABASE_URL, PARSER_VERSION
from app.db import Session, engine
from app.employee_import import (
    MappingError,
    build_key,
    number_rules,
    run_import,
)
from app.models import (
    Base,
    DailyAttendance,
    Device,
    DeviceOption,
    DeviceUserMap,
    Employee,
    EmployeeAssignment,
    EmployeeNumberKey,
    EmploymentPeriod,
    GroupSchedule,
    Holiday,
    HolidayAdjustment,
    ManualPunch,
    ParsedPunch,
    ParserSetting,
    RawRequest,
)
from app.cli_alert import add_parsers as add_alert_parsers
from app.cli_attendance import add_parsers as add_attendance_parsers
from app.corrections import employee_by_number
from app.cli_corrections import add_parsers as add_corrections_parsers
from app.cli_raw import add_parsers as add_raw_parsers
from app.cli_schedule import add_parsers as add_schedule_parsers
from app.cli_sheet import add_parsers as add_sheet_parsers
from app.parser import replay as replay_parser
from app.seed import seed as seed_rows


def cmd_seed(args) -> int:
    with Session() as session:
        captured = 0
        try:
            captured = session.scalar(select(func.count()).select_from(RawRequest)) or 0
        except Exception:
            session.rollback()
    if captured and not args.force:
        print(
            f"refusing to drop: {captured} raw requests are already captured.\n"
            "Real device capture cannot be recreated. Re-run with --force only if "
            "this is still test data (BUILD.md, Data and migrations).",
            file=sys.stderr,
        )
        return 1

    with engine.begin() as conn:
        # DROP CASCADE, because raw_request carries its own append-only triggers.
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        # Needed by the exclusion constraints that keep effective-dated rows
        # from overlapping.
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
    Base.metadata.create_all(engine)
    with Session() as session:
        seed_rows(session)
    print(f"schema created and seeded at {_dsn()}")
    return 0


def cmd_replay(args) -> int:
    """A parser change means bumping the version and replaying the raw layer —
    never re-collecting from the device."""
    with Session() as session:
        requests, punches, unparsable = replay_parser(session, since_id=args.since_id)
        session.commit()
    print(
        f"parser {PARSER_VERSION}: replayed {requests} raw requests "
        f"into {punches} punch rows"
    )
    if unparsable:
        print(
            f"{len(unparsable)} raw requests could not be parsed at all and were "
            f"left for a later parser version: {unparsable[:20]}"
        )
        return 1
    return 0


def cmd_status(args) -> int:
    with Session() as session:
        counts = {
            "raw_request": session.scalar(select(func.count()).select_from(RawRequest)),
            "parsed_punch": session.scalar(select(func.count()).select_from(ParsedPunch)),
            "parsed_punch (failed)": session.scalar(
                select(func.count()).select_from(ParsedPunch).where(
                    ParsedPunch.parse_ok.is_(False)
                )
            ),
            "device": session.scalar(select(func.count()).select_from(Device)),
            "device_option": session.scalar(
                select(func.count()).select_from(DeviceOption)
            ),
            "parser_setting": session.scalar(
                select(func.count()).select_from(ParserSetting)
            ),
            "employee": session.scalar(select(func.count()).select_from(Employee)),
            "employee_number_key": session.scalar(
                select(func.count()).select_from(EmployeeNumberKey)
            ),
            "employee_assignment": session.scalar(
                select(func.count()).select_from(EmployeeAssignment)
            ),
            "employment_period": session.scalar(
                select(func.count()).select_from(EmploymentPeriod)
            ),
            "device_user_map": session.scalar(
                select(func.count()).select_from(DeviceUserMap)
            ),
            "group_schedule": session.scalar(
                select(func.count()).select_from(GroupSchedule)
            ),
            "holiday": session.scalar(select(func.count()).select_from(Holiday)),
            "holiday_adjustment": session.scalar(
                select(func.count()).select_from(HolidayAdjustment)
            ),
            "manual_punch": session.scalar(
                select(func.count()).select_from(ManualPunch)
            ),
            "daily_attendance": session.scalar(
                select(func.count()).select_from(DailyAttendance)
            ),
        }
    print(_dsn())
    for name, value in counts.items():
        print(f"  {name:24} {value}")
    return 0


def cmd_employees_import(args) -> int:
    source = Path(args.file)
    mapping_path = Path(args.mapping)
    if not source.exists():
        print(f"no such file: {source}", file=sys.stderr)
        return 1
    if not mapping_path.exists():
        print(f"no such mapping file: {mapping_path}", file=sys.stderr)
        return 1

    with Session() as session:
        try:
            result = run_import(
                session,
                source,
                mapping_path,
                replace=args.replace,
                allow_new=set(args.allow_new or ()),
                accept_odd_numbers=args.accept_odd_numbers,
            )
        except MappingError as exc:
            print(f"the mapping is wrong, so nothing was read:\n  {exc}",
                  file=sys.stderr)
            return 1

        if result.problems:
            print(
                f"{len(result.problems)} problems with {source.name} — "
                "nothing was written",
                file=sys.stderr,
            )
            for problem in result.problems[:60]:
                where = f"row {problem.row}: " if problem.row else ""
                print(f"  {where}{problem.field}: {problem.message}", file=sys.stderr)
            if len(result.problems) > 60:
                print(f"  ... and {len(result.problems) - 60} more", file=sys.stderr)
            return 1

        session.commit()

    if result.headers:
        print("mapping (header text is echoed, never matched on):")
        for name, header in result.headers.items():
            print(f"  {name:16} <- {header!r}")
    for table, count in result.written.items():
        print(f"  {table:22} {count}")
    if result.blank_rows:
        print(f"  {len(result.blank_rows)} blank rows skipped")
    numeric = [s for s in result.staged if s.number_from_numeric_cell]
    if numeric:
        print(
            f"  {len(numeric)} employee numbers came from numeric cells — if any "
            "had leading zeros, the spreadsheet lost them before this"
        )
    odd = [s for s in result.staged if s.odd_number]
    if odd:
        print("  accepted odd numbers, stored exactly as given:")
        for staged in odd:
            print(f"    row {staged.row}: {staged.employee_number!r} "
                  f"matched as {staged.number_key!r}")
    if result.new_vocabulary:
        print("  added to the vocabulary:")
        for kind, value in result.new_vocabulary:
            print(f"    {kind}: {value}")
    return 0


def cmd_devices_add(args) -> int:
    """Put a device on the allowlist.

    The allowlist is what the receiver logs unknown serials against (SPEC §12)
    and what the ingestion alert watches (step 9). A device that captures
    perfectly but is not on this list is a device nobody is watching.
    """
    with Session() as session:
        if session.get(Device, args.serial) is not None:
            print(f"{args.serial} is already on the allowlist", file=sys.stderr)
            return 1
        session.add(Device(serial_number=args.serial, label=args.label,
                           note=args.note))
        session.commit()
    print(f"{args.serial} added: {args.label}")
    print("  the receiver already answered it 200 OK either way — this is what "
          "the ingestion alert watches (SPEC §12, step 9)")
    return 0


def cmd_devices_list(args) -> int:
    with Session() as session:
        devices = list(session.scalars(select(Device).order_by(Device.serial_number)))
        if not devices:
            print("the allowlist is empty")
            return 0
        for device in devices:
            last = session.scalar(
                select(func.max(RawRequest.received_at))
                .where(RawRequest.serial_number == device.serial_number)
            )
            heard = last.isoformat(sep=" ", timespec="seconds") if last else "never"
            print(f"{device.serial_number:<18} {device.label:<24} last heard {heard}")
    return 0


def cmd_employees_map_pin(args) -> int:
    """Point a device PIN at an employee, from a date.

    The importer writes these rows from the employee list, on A19 — the PIN is
    the employee number. This is for the case A19 does not cover: an enrollment
    the device made on its own terms, such as a PIN with no leading zero, which
    is the only kind the device will accept (SPEC §2, §10). It writes one dated
    row in the mapping table and nothing else — no punch is touched, and
    capture still resolves nothing (SPEC §13).
    """
    with Session() as session:
        try:
            employee = employee_by_number(session, args.employee)
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        row = DeviceUserMap(
            employee_id=employee.id,
            serial_number=args.serial,
            pin=args.pin,
            effective_from=dt.datetime.strptime(args.start, "%Y-%m-%d").date(),
            effective_to=(
                dt.datetime.strptime(args.end, "%Y-%m-%d").date()
                if args.end else None
            ),
            source=args.source,
            note=args.note,
        )
        session.add(row)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            print("refused: that PIN already points at an employee over part of "
                  f"this range — one PIN cannot be two people on one day.\n  "
                  f"{str(exc.orig).strip().splitlines()[0]}", file=sys.stderr)
            return 1
    print(f"pin {args.pin!r} -> employee {employee.employee_number} "
          f"from {args.start}{' to ' + args.end if args.end else ' onwards'}")
    print("  the mapping is read on the punch's own date, never at capture "
          "(SPEC §9 A33, §13)")
    return 0


def cmd_employees_rekey(args) -> int:
    """Rebuild the matching keys from the rule rows. This is what SPEC §2 means
    by correcting a wrong assumption about the number format by remapping rows
    — no stored employee number is touched."""
    with Session() as session:
        rules = number_rules(session)
        changed = 0
        for employee in session.scalars(select(Employee)):
            wanted = build_key(employee.employee_number, rules)
            keys = session.scalars(
                select(EmployeeNumberKey).where(
                    EmployeeNumberKey.employee_id == employee.id
                )
            ).all()
            if [k.key for k in keys] == [wanted]:
                continue
            for key in keys:
                session.delete(key)
            session.flush()
            session.add(EmployeeNumberKey(
                employee_id=employee.id, key=wanted, built_by="rekey"
            ))
            changed += 1
        session.commit()
    print(f"rekeyed {changed} employees using width "
          f"{rules['key_width']} padded with {rules['key_pad']!r}")
    return 0


def _dsn() -> str:
    return DATABASE_URL.rsplit("@", 1)[-1]


def main() -> int:
    parser = argparse.ArgumentParser(prog="hr", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_seed = sub.add_parser("seed", help="drop, recreate and seed the database")
    p_seed.add_argument(
        "--force",
        action="store_true",
        help="drop even though requests have already been captured",
    )
    p_seed.set_defaults(func=cmd_seed)

    p_replay = sub.add_parser("replay", help="rebuild parsed punches from the raw layer")
    p_replay.add_argument("--since-id", type=int, default=0)
    p_replay.set_defaults(func=cmd_replay)

    sub.add_parser("status", help="row counts").set_defaults(func=cmd_status)

    p_emp = sub.add_parser("employees", help="the employee list")
    emp = p_emp.add_subparsers(dest="employees_command", required=True)

    p_import = emp.add_parser(
        "import",
        help="load an employee list from a spreadsheet, using an explicit mapping",
    )
    p_import.add_argument("file", help=".xlsx file from HR")
    p_import.add_argument(
        "--mapping",
        required=True,
        help="TOML file saying which sheet, which rows and which columns",
    )
    p_import.add_argument(
        "--replace",
        action="store_true",
        help="delete the loaded employee list first and load this one instead",
    )
    p_import.add_argument(
        "--allow-new",
        action="append",
        choices=("section", "role", "group"),
        metavar="KIND",
        help="allow this list to introduce new sections, roles or groups — name "
             "the kind, one --allow-new each. Anything not named is an error, "
             "which is what catches a column pointing at the wrong place",
    )
    p_import.add_argument(
        "--accept-odd-numbers",
        action="store_true",
        help="accept employee numbers that do not match the expected shape; they "
             "are stored exactly as given either way",
    )
    p_import.set_defaults(func=cmd_employees_import)

    p_map = emp.add_parser(
        "map-pin",
        help="point a device PIN at an employee from a date, for an enrollment "
             "the employee list does not describe",
    )
    p_map.add_argument("--pin", required=True, help="exactly as the device sends it")
    p_map.add_argument("--employee", required=True, help="employee number")
    p_map.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD")
    p_map.add_argument("--to", dest="end", help="YYYY-MM-DD, else open-ended")
    p_map.add_argument("--serial", help="only this device, else any")
    p_map.add_argument("--source", required=True,
                       help="where this mapping came from, in words")
    p_map.add_argument("--note")
    p_map.set_defaults(func=cmd_employees_map_pin)

    emp.add_parser(
        "rekey", help="rebuild the matching keys from employee_number_rule"
    ).set_defaults(func=cmd_employees_rekey)

    p_dev = sub.add_parser("devices", help="the device allowlist")
    dev = p_dev.add_subparsers(dest="devices_command", required=True)
    p_dev_add = dev.add_parser(
        "add", help="add a serial to the allowlist, so the alert watches it")
    p_dev_add.add_argument("--serial", required=True)
    p_dev_add.add_argument("--label", required=True)
    p_dev_add.add_argument("--note")
    p_dev_add.set_defaults(func=cmd_devices_add)
    dev.add_parser("list", help="what is on the allowlist, and when each was "
                                "last heard from").set_defaults(func=cmd_devices_list)

    add_schedule_parsers(sub)
    add_corrections_parsers(sub)
    add_raw_parsers(sub)
    add_attendance_parsers(sub)
    add_sheet_parsers(sub)
    add_alert_parsers(sub)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
