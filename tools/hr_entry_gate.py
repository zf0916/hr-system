#!/usr/bin/env python3
"""The gate for step 5: deliberate mistakes that must fail.

Every case builds its own employee, schedule and forms inside a transaction
that is rolled back.

The three the step was asked to prove, each shown working and then broken:

  1. **a leave record whose day count is recomputed from its range fails** —
     the form states the count, and a half day or a non-working day inside a
     range makes the count and the span different numbers;
  2. **a gate pass whose hours were typed rather than derived fails** — the
     form has no hours field, so neither does the row;
  3. **a sheet cell holding a code no leave record produced fails** — every
     code on the sheet is a record somebody entered.

    uv run python tools/hr_entry_gate.py

Exits non-zero if any deliberate mistake was accepted.
"""

from __future__ import annotations

import datetime as dt
import inspect
from decimal import Decimal

import psycopg
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError, IntegrityError

from app.attendance import build_days
from app.db import Session
from app.hr_entry import (
    leave_by_day,
    leave_for,
    record_gate_pass,
    record_leave,
    suggested_code,
)
from app.models import (
    DeviceUserMap,
    Employee,
    EmployeeAssignment,
    EmployeeGroup,
    EmployeeNumberKey,
    EmploymentPeriod,
    GatePass,
    LeaveRecord,
    LeaveType,
)
from app.schedule import set_schedule
from app.sheet import EMPTY, LEAVE, TICK, render, to_text

GROUP = "GATE-HR-DAY"
NUMBER, PIN = "9801", "9801"

# August 2026: the 3rd is a Monday, the 9th a Sunday.
MONTH_START, MONTH_END = dt.date(2026, 8, 1), dt.date(2026, 8, 31)
MONDAY = dt.date(2026, 8, 3)

DAY_SHIFT = {
    "start_time": dt.time(8, 0), "end_time": dt.time(17, 30),
    "end_next_day": False, "rest_weekdays": [7], "grace_minutes": 0,
    "provisional": True,
}


class Gate:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.checks = 0

    def check(self, ok: bool, what: str, detail: str = "") -> bool:
        self.checks += 1
        print(f"  {'ok  ' if ok else 'FAIL'}    {what}"
              + ("" if ok else f" — {detail}"))
        if not ok:
            self.failures.append(what)
        return ok

    def refused(self, what: str, fn, want: str = "",
                expect=(IntegrityError, DatabaseError, psycopg.Error,
                        ValueError, TypeError)) -> None:
        with Session() as session:
            try:
                employee = setup(session)
                fn(session, employee)
                session.flush()
            except expect as exc:
                line = str(exc).strip().splitlines()[0]
                if want and want not in str(exc):
                    self.check(False, what,
                               f"refused, but not by {want}: {line[:110]}")
                else:
                    self.check(True, what)
                    print(f"          refused: {line[:130]}")
            else:
                self.check(False, what, "it was accepted")
            finally:
                session.rollback()


def setup(session) -> Employee:
    session.add(EmployeeGroup(code=GROUP, label=GROUP, note="gate"))
    session.flush()
    set_schedule(session, GROUP, dt.date(2020, 1, 1), **DAY_SHIFT)
    employee = Employee(employee_number=NUMBER)
    session.add(employee)
    session.flush()
    session.add(EmployeeNumberKey(employee_id=employee.id, key=NUMBER,
                                 built_by="gate"))
    session.add(EmployeeAssignment(
        employee_id=employee.id, effective_from=dt.date(2020, 1, 1),
        name="Leave Test", section_code="QC", role_code="QA/QC",
        group_code=GROUP))
    session.add(EmploymentPeriod(employee_id=employee.id,
                                active_from=dt.date(2020, 1, 1)))
    session.add(DeviceUserMap(employee_id=employee.id, pin=PIN,
                             effective_from=dt.date(2020, 1, 1), source="gate"))
    session.flush()
    return employee


def main() -> int:
    gate = Gate()

    print("\n-- the vocabularies are rows, and four types have no code")
    with Session() as session:
        types = {row.code: row.suggested_sheet_code
                 for row in session.scalars(select(LeaveType))}
        gate.check(len(types) == 7, "seven ticks on the form", f"got {types}")
        gate.check(types.get("ANNUAL") == "AL" and types.get("SICK") == "MC"
                   and types.get("UNPAID") == "UL",
                   "three carry a suggested code (A48)", f"got {types}")
        without = sorted(code for code, suggested in types.items()
                         if suggested is None)
        gate.check(without == ["COMPASSIONATE", "HOSPITALIZATION", "MATERNITY",
                               "SOCSO"],
                   f"and four carry none, because the legend has none: {without}")
        gate.check(suggested_code(session, "MATERNITY") is None,
                   "entry suggests nothing for those, rather than the nearest "
                   "letter")

    print("\n-- 1. the number of days is what the form says")
    with Session() as session:
        employee = setup(session)
        # Three calendar days, two days of leave: the range contains a Sunday.
        record = record_leave(
            session, employee, leave_type_code="ANNUAL", sheet_code="AL",
            period_from=dt.date(2026, 8, 7), period_to=dt.date(2026, 8, 9),
            days="2", date_of_application=dt.date(2026, 7, 20),
            entered_by="HR: Gate")
        span = (record.period_to - record.period_from).days + 1
        gate.check(record.days == Decimal("2") and span == 3,
                   f"2 days over a 3-day range, stored as stated",
                   f"days {record.days}, span {span}")

        half = record_leave(
            session, employee, leave_type_code="ANNUAL", sheet_code="AL",
            period_from=MONDAY, period_to=MONDAY, days="0.5",
            date_of_application=None, entered_by="HR: Gate")
        gate.check(half.days == Decimal("0.5"),
                   "a half day is stored as 0.5", f"got {half.days}")

        # The deliberate mistake, in the code's shape: there is no parameter
        # that could compute it, and none that lets it be left out.
        signature = inspect.signature(record_leave)
        gate.check("days" in signature.parameters,
                   "record_leave takes the count as an argument")
        gate.check(signature.parameters["days"].default
                   is inspect.Parameter.empty,
                   "and it is required — nothing defaults it to a span")
        source = inspect.getsource(record_leave)
        gate.check("period_to - period_from" not in source
                   and ".days + 1" not in source,
                   "and nothing in it subtracts the two dates",
                   "the range is being counted somewhere in record_leave")
        session.rollback()

    gate.refused(
        "a leave record with no day count at all is refused",
        lambda session, employee: record_leave(
            session, employee, leave_type_code="ANNUAL", sheet_code="AL",
            period_from=MONDAY, period_to=MONDAY, days=None,
            date_of_application=None, entered_by="HR: Gate"),
        want="never computed from the range")

    gate.refused(
        "a day count recomputed from the range is refused by the database "
        "when it is not a whole or half day",
        lambda session, employee: session.execute(text(
            "INSERT INTO leave_record (employee_id, leave_type_code, "
            "sheet_code, period_from, period_to, days, entered_by) VALUES "
            "(:e, 'ANNUAL', 'AL', '2026-08-07', '2026-08-09', "
            "(DATE '2026-08-09' - DATE '2026-08-07') + 0.25, 'gate')"),
            {"e": employee.id}),
        want="leave_record_days_are_halves")

    gate.refused(
        "zero days is refused",
        lambda session, employee: record_leave(
            session, employee, sheet_code="AL", period_from=MONDAY,
            period_to=MONDAY, days="0", date_of_application=None,
            entered_by="HR: Gate"),
        want="not a number of days")

    gate.refused(
        "a record that says neither what was applied for nor what goes on the "
        "sheet is refused",
        lambda session, employee: record_leave(
            session, employee, period_from=MONDAY, period_to=MONDAY, days="1",
            date_of_application=None, entered_by="HR: Gate"),
        want="says what it is")

    print("\n-- neither leave field is filled in from the other (SPEC §6)")
    with Session() as session:
        employee = setup(session)
        typed_only = record_leave(
            session, employee, leave_type_code="MATERNITY", sheet_code=None,
            period_from=dt.date(2026, 8, 10), period_to=dt.date(2026, 8, 14),
            days="5", date_of_application=None, entered_by="HR: Gate")
        gate.check(typed_only.sheet_code is None,
                   "a type with no legend code stores no code",
                   f"got {typed_only.sheet_code!r}")
        code_only = record_leave(
            session, employee, sheet_code="EL", period_from=dt.date(2026, 8, 17),
            period_to=dt.date(2026, 8, 17), days="1",
            date_of_application=None, entered_by="HR: Gate")
        gate.check(code_only.leave_type_code is None,
                   "a code with no box on the form stores no type — EL is "
                   "written on the sheet and nobody applies for it",
                   f"got {code_only.leave_type_code!r}")
        gate.check(typed_only.sql_account_code is None,
                   "and the SQL Account code stays empty (SPEC §8)")
        session.rollback()

    print("\n-- 2. the hours on a gate pass are never typed")
    with Session() as session:
        employee = setup(session)
        gate_pass = record_gate_pass(
            session, employee, pass_date=MONDAY, category_code="PERSONAL",
            out_time=dt.time(14, 0), in_time=dt.time(16, 30),
            reason="bank", destination="Melaka town", entered_by="HR: Gate")
        gate.check(gate_pass.hours == Decimal("2.50"),
                   "14:00 to 16:30 is 2.50 hours, derived",
                   f"got {gate_pass.hours}")

        signature = inspect.signature(record_gate_pass)
        gate.check("hours" not in signature.parameters,
                   "record_gate_pass has no hours parameter",
                   f"parameters {list(signature.parameters)}")
        gate.check("department" not in signature.parameters,
                   "and no department parameter — the form has no such field")

        column = GatePass.__table__.c.hours
        gate.check(column.computed is not None and column.computed.persisted,
                   "the column is generated by the database, so there is "
                   "nothing to type into", f"computed {column.computed}")
        session.rollback()

    gate.refused(
        "typing the hours is refused: the column cannot be written",
        lambda session, employee: session.execute(text(
            "INSERT INTO gate_pass (employee_id, pass_date, category_code, "
            "out_time, in_time, hours, entered_by) VALUES "
            "(:e, '2026-08-03', 'PERSONAL', '14:00', '16:30', 99, 'gate')"),
            {"e": employee.id}),
        want="hours")

    gate.refused(
        "an in time before the out time is refused",
        lambda session, employee: record_gate_pass(
            session, employee, pass_date=MONDAY, category_code="PERSONAL",
            out_time=dt.time(16, 30), in_time=dt.time(14, 0),
            entered_by="HR: Gate"),
        want="not after out time")

    gate.refused(
        "a category that is not one of the four ticks is refused",
        lambda session, employee: record_gate_pass(
            session, employee, pass_date=MONDAY, category_code="MEDICAL_SLIP",
            out_time=dt.time(14, 0), in_time=dt.time(16, 0),
            entered_by="HR: Gate"),
        want="four ticks")

    print("\n-- 3. every code on the sheet is a record somebody entered")
    with Session() as session:
        employee = setup(session)
        record_leave(
            session, employee, leave_type_code="ANNUAL", sheet_code="AL",
            period_from=dt.date(2026, 8, 5), period_to=dt.date(2026, 8, 6),
            days="2", date_of_application=dt.date(2026, 7, 28),
            entered_by="HR: Gate")
        record_leave(
            session, employee, leave_type_code="MATERNITY", sheet_code=None,
            period_from=dt.date(2026, 8, 12), period_to=dt.date(2026, 8, 12),
            days="1", date_of_application=None, entered_by="HR: Gate")
        build_days(session, MONTH_START, MONTH_END, [employee.id])
        sheet = render(session, MONTH_START, MONTH_END)

        coded = {c.date: sheet.cell(employee.id, c.date) for c in sheet.columns}
        gate.check(coded[dt.date(2026, 8, 5)].text == "AL"
                   and coded[dt.date(2026, 8, 5)].kind == LEAVE,
                   "the leave day shows its code",
                   f"got {coded[dt.date(2026, 8, 5)].text!r}")
        gate.check(coded[dt.date(2026, 8, 6)].text == "AL",
                   "and so does the second day of the same record")
        gate.check(coded[dt.date(2026, 8, 12)].text == ""
                   and "no sheet code" in coded[dt.date(2026, 8, 12)].detail,
                   "a leave day with no sheet code shows nothing rather than "
                   "borrowing a letter",
                   f"got {coded[dt.date(2026, 8, 12)].text!r}")

        # Every code on the sheet traces to a record.
        by_day = leave_by_day(session, MONTH_START, MONTH_END)
        orphans = [
            (r.employee_number, c.date, sheet.cell(r.employee_id, c.date).text)
            for r in sheet.rows for c in sheet.columns
            if sheet.cell(r.employee_id, c.date).leave_code
            and (r.employee_id, c.date) not in by_day
        ]
        gate.check(not orphans,
                   f"no cell carries a code without a record behind it",
                   f"orphans {orphans[:3]}")

        # The deliberate mistake: a code put in a cell by hand. The renderer
        # cannot produce one, so this stages what it would look like and checks
        # the same rule catches it.
        stray = render(session, MONTH_START, MONTH_END)
        victim = stray.cells[(employee.id, dt.date(2026, 8, 19))]
        victim.text, victim.kind, victim.leave_code = "MC", LEAVE, "MC"
        staged_orphans = [
            (r.employee_id, c.date)
            for r in stray.rows for c in stray.columns
            if stray.cell(r.employee_id, c.date).leave_code
            and (r.employee_id, c.date) not in by_day
        ]
        gate.check(len(staged_orphans) == 1,
                   "a code in a cell with no record behind it is caught",
                   f"caught {staged_orphans}")
        gate.check(leave_for(session, employee.id, dt.date(2026, 8, 19)) is None,
                   "and the day it was put on has no leave record at all")

        text_sheet = to_text(sheet)
        gate.check("AL" in text_sheet and "Annual leave" in text_sheet,
                   "the legend prints the codes from their rows")
        gate.check("not entered yet" not in text_sheet,
                   "and the sheet no longer says leave is not entered yet")
        session.rollback()

    print("\n-- what this step deliberately does not do")
    with Session() as session:
        columns = set(LeaveRecord.__table__.columns.keys())
        for absent in ("balance", "entitlement", "approved_by", "approval_state",
                       "verified_by", "carried_forward"):
            gate.check(absent not in columns,
                       f"no {absent} column on a leave record")
        gate.check("sql_account_code" in columns,
                   "the SQL Account code is carried from the start, and left "
                   "empty (SPEC §6, §8)")

    print(f"\n{gate.checks} checks")
    if gate.failures:
        print(f"{len(gate.failures)} FAILED:")
        for failure in gate.failures:
            print(f"  - {failure}")
        return 1
    print("clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
