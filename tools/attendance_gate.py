#!/usr/bin/env python3
"""The gate for step 6: deliberate mistakes that must fail.

Every case builds its own employee, group, schedule, mapping and punches inside
a transaction that is rolled back, so nothing depends on what happens to be
loaded and nothing is left behind.

The five the step was asked to prove, each shown working and then broken:

  1. a night-shift punch at 04:35 belongs to the previous attendance day;
  2. late minutes come from the schedule in force on that day, not today's;
  3. a manual punch is never silently one of the day's figures;
  4. one punch is a first in and no last out — including in the database;
  5. two rebuilds from the same punches produce the same row.

    uv run python tools/attendance_gate.py

Exits non-zero if any deliberate mistake was accepted.
"""

from __future__ import annotations

import datetime as dt
import sys

import psycopg
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError, IntegrityError

from app.attendance import build_day, build_days, late_minutes_for, status_for
from app.corrections import record_guard_entry, record_hr_retroactive
from app.db import Session
from app.models import (
    AttendanceStatus,
    DailyAttendance,
    DeviceUserMap,
    Employee,
    EmployeeAssignment,
    EmployeeGroup,
    EmployeeNumberKey,
    EmploymentPeriod,
    GroupSchedule,
    RawRequest,
)
from app.parser import parse_raw_request
from app.schedule import attendance_day_for, schedule_for, set_schedule

DAY_GROUP = "GATE-DA-DAY"
NIGHT_GROUP = "GATE-DA-NIGHT"
DAY_NUMBER, DAY_PIN = "9601", "9601"
NIGHT_NUMBER, NIGHT_PIN = "9602", "9602"

# Columns that must come out identical from two rebuilds. `id` and `built_at`
# are the two that may differ, and only those two.
COMPARED = [
    "employee_id", "attendance_day", "group_code", "schedule_id",
    "schedule_provisional", "scheduled_start", "scheduled_end", "grace_minutes",
    "is_rest_day", "holiday_name", "holiday_closes", "first_in",
    "first_in_source", "first_in_manual", "last_out", "last_out_source",
    "last_out_manual", "punch_count", "device_punch_count",
    "manual_punch_count", "duplicate_pushes", "late_minutes", "status_code",
    "note",
]

DAY_SHIFT = {
    "start_time": dt.time(8, 0), "end_time": dt.time(17, 30),
    "end_next_day": False, "rest_weekdays": [7], "grace_minutes": 0,
    "provisional": True,
}
NIGHT_SHIFT = {
    "start_time": dt.time(19, 30), "end_time": dt.time(4, 30),
    "end_next_day": True, "rest_weekdays": [7], "grace_minutes": 0,
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
                expect=(IntegrityError, DatabaseError, psycopg.Error, ValueError)) -> None:
        """The database or the code has to refuse this, not just avoid doing it.

        The employee is built first: without one, the insert below would fail on
        a not-null violation and the check would pass for the wrong reason.
        `want` names the constraint that has to be the one doing the refusing.
        """
        with Session() as session:
            try:
                employee, _ = setup(session)
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


def make_employee(session, number: str, pin: str, group: str,
                  name: str) -> Employee:
    employee = Employee(employee_number=number)
    session.add(employee)
    session.flush()
    session.add(EmployeeNumberKey(employee_id=employee.id, key=number,
                                 built_by="gate"))
    session.add(EmployeeAssignment(
        employee_id=employee.id, effective_from=dt.date(2020, 1, 1), name=name,
        section_code="QC", role_code="QA/QC", group_code=group))
    session.add(EmploymentPeriod(employee_id=employee.id,
                                active_from=dt.date(2020, 1, 1)))
    session.add(DeviceUserMap(employee_id=employee.id, pin=pin,
                             effective_from=dt.date(2020, 1, 1), source="gate"))
    session.flush()
    return employee


def setup(session) -> tuple[Employee, Employee]:
    """A day-shift employee and a night-shift one, each with a schedule."""
    session.add(EmployeeGroup(code=DAY_GROUP, label=DAY_GROUP, note="gate"))
    session.add(EmployeeGroup(code=NIGHT_GROUP, label=NIGHT_GROUP, note="gate"))
    session.flush()
    set_schedule(session, DAY_GROUP, dt.date(2020, 1, 1), **DAY_SHIFT)
    set_schedule(session, NIGHT_GROUP, dt.date(2020, 1, 1), **NIGHT_SHIFT)
    day = make_employee(session, DAY_NUMBER, DAY_PIN, DAY_GROUP, "Day Shift")
    night = make_employee(session, NIGHT_NUMBER, NIGHT_PIN, NIGHT_GROUP,
                          "Night Shift")
    return day, night


def punch(session, pin: str, at: dt.datetime) -> None:
    """A real device punch: the observed ten-field line, through the parser."""
    fields = [pin, at.strftime("%Y-%m-%d %H:%M:%S"), "255", "15"] + ["0"] * 6
    body = ("\t".join(fields) + "\t\r\n").encode()
    raw = RawRequest(
        method="POST", path="/iclock/cdata",
        query_string="SN=GATE&table=ATTLOG&Stamp=9999",
        headers=[["content-type", "text/plain"]], content_type="text/plain",
        body=body, body_bytes=len(body), serial_number="GATE",
        table_param="ATTLOG", stamp_param="9999", response_body="OK: 1")
    session.add(raw)
    session.flush()
    parse_raw_request(session, raw)
    session.flush()


def main() -> int:
    gate = Gate()

    print("\n-- the status vocabulary has no absence in it")
    with Session() as session:
        codes = set(session.scalars(select(AttendanceStatus.code)))
        gate.check(codes == {"punches_recorded", "one_punch", "no_punch"},
                   "three statuses, all of them facts", f"got {sorted(codes)}")
        gate.check("absent" not in codes and not any("absen" in c for c in codes),
                   "no status means absent — that needs leave (SPEC §3, §13)")
        gate.check(status_for(0) == "no_punch" and status_for(1) == "one_punch"
                   and status_for(5) == "punches_recorded",
                   "the status is decided by the punch count and nothing else")

    print("\n-- 1. a night-shift punch at 04:35 belongs to the previous day")
    with Session() as session:
        _, night = setup(session)
        # Monday 19:40 in, Tuesday 04:35 out. One attendance day: Monday.
        monday, tuesday = dt.date(2026, 3, 2), dt.date(2026, 3, 3)
        punch(session, NIGHT_PIN, dt.datetime.combine(monday, dt.time(19, 40)))
        punch(session, NIGHT_PIN, dt.datetime.combine(tuesday, dt.time(4, 35)))

        landed = attendance_day_for(
            session, NIGHT_GROUP, dt.datetime.combine(tuesday, dt.time(4, 35)))
        gate.check(landed.date == monday,
                   "step 3 puts the 04:35 punch on Monday",
                   f"got {landed.date}")

        built = build_days(session, monday, tuesday, [night.id])
        rows = {b.attendance_day: b.values for b in built}
        gate.check(rows[monday]["punch_count"] == 2,
                   "Monday's row has both punches",
                   f"got {rows[monday]['punch_count']}")
        gate.check(str(rows[monday]["first_in"]) == "2026-03-02 19:40:00"
                   and str(rows[monday]["last_out"]) == "2026-03-03 04:35:00",
                   "first in 19:40 Monday, last out 04:35 Tuesday, one row",
                   f"got {rows[monday]['first_in']} → {rows[monday]['last_out']}")
        gate.check(rows[tuesday]["punch_count"] == 0
                   and rows[tuesday]["status_code"] == "no_punch",
                   "Tuesday has no punch of its own",
                   f"got {rows[tuesday]['punch_count']} punches")
        gate.check(rows[monday]["late_minutes"] == 10,
                   "late against Monday's 19:30 start: 10 minutes",
                   f"got {rows[monday]['late_minutes']}")

        # The deliberate mistake: the punch landing on its own calendar date.
        wrong = dt.datetime.combine(tuesday, dt.time(4, 35)).date()
        gate.check(wrong != rows[monday]["attendance_day"],
                   "the calendar date and the attendance day are not the same "
                   "thing here", f"both {wrong}")
        by_clock = [b for b in built if b.attendance_day == wrong][0]
        gate.check(by_clock.values["punch_count"] == 0,
                   "putting the 04:35 punch on Tuesday would leave Monday's "
                   "shift half-recorded and Tuesday claiming a punch it did "
                   "not have")
        session.rollback()

    print("\n-- 2. late minutes come from the day's schedule, not today's")
    with Session() as session:
        day, _ = setup(session)
        # The schedule changes: 08:00 until 30 June, 09:00 from 1 July.
        set_schedule(session, DAY_GROUP, dt.date(2026, 7, 1),
                     **{**DAY_SHIFT, "start_time": dt.time(9, 0)})
        june = dt.date(2026, 6, 15)
        punch(session, DAY_PIN, dt.datetime.combine(june, dt.time(8, 20)))

        built = build_day(session, day.id, june)
        gate.check(built.values["late_minutes"] == 20,
                   "20 minutes late against June's 08:00 start",
                   f"got {built.values['late_minutes']}")
        gate.check(str(built.values["scheduled_start"]) == "2026-06-15 08:00:00",
                   "the row carries the start it was measured against",
                   f"got {built.values['scheduled_start']}")

        # The deliberate mistake: measure against the schedule in force now.
        today_schedule = schedule_for(session, DAY_GROUP, dt.date(2026, 7, 15))
        by_today = late_minutes_for(
            dt.datetime.combine(june, today_schedule.start_time),
            today_schedule.grace_minutes,
            dt.datetime.combine(june, dt.time(8, 20)))
        gate.check(by_today == 0 and built.values["late_minutes"] == 20,
                   "today's 09:00 schedule would have said 0 — a different "
                   "answer for the same punch", f"got {by_today}")
        gate.check(built.values["schedule_id"] != today_schedule.id,
                   "the row points at June's schedule row, not July's")

        print("       and the grace period is a row, not a constant:")
        session.execute(
            text("UPDATE group_schedule SET grace_minutes = 15 "
                 "WHERE group_code = :g AND effective_from = '2020-01-01'"),
            {"g": DAY_GROUP})
        session.flush()
        regrace = build_day(session, day.id, june)
        gate.check(regrace.values["late_minutes"] == 5,
                   "15 minutes of grace turns 20 late into 5 — an UPDATE, not "
                   "a code change", f"got {regrace.values['late_minutes']}")
        session.rollback()

    print("\n-- 3. a manual punch is never silently one of the day's figures")
    with Session() as session:
        day, _ = setup(session)
        when = dt.date(2026, 4, 6)
        # A guard entry can only be stamped now, so this day is today's.
        today = dt.date.today()
        record_guard_entry(session, day, reason_code="biometric_failed",
                           made_by="Guard: Gate")
        punch(session, DAY_PIN, dt.datetime.combine(today, dt.time(23, 59)))
        built = build_day(session, day.id, today)
        values = built.values
        gate.check(values["manual_punch_count"] >= 1,
                   "the manual punch is counted as manual",
                   f"got {values['manual_punch_count']}")
        gate.check(values["punch_count"]
                   == values["device_punch_count"] + values["manual_punch_count"],
                   "manual punches count toward the day's figures")
        gate.check(values["first_in_manual"] is True
                   and values["first_in_source"] == "guard entry",
                   "the first in says a person entered it",
                   f"got {values['first_in_source']!r}, "
                   f"manual={values['first_in_manual']}")
        gate.check("entered by a person" in (values["note"] or ""),
                   f"the row's note says so too: {values['note']!r}")

        # HR retroactive, on a day of its own, and equally visible.
        record_hr_retroactive(
            session, day, asserted_time=dt.datetime.combine(when, dt.time(8, 30)),
            reason="device down", made_by="HR: Gate")
        retro = build_day(session, day.id, when).values
        gate.check(retro["first_in_manual"] is True
                   and retro["first_in_source"] == "HR retroactive"
                   and retro["late_minutes"] == 30,
                   "an HR retroactive entry is a figure and is marked as manual",
                   f"got {retro['first_in_source']!r} late={retro['late_minutes']}")
        session.rollback()

    # The deliberate mistake, in the database: a figure with no source.
    gate.refused(
        "a first in with no source is refused",
        lambda session, employee: session.execute(text(
            "INSERT INTO daily_attendance (employee_id, attendance_day, "
            "first_in, punch_count, device_punch_count, manual_punch_count, "
            "duplicate_pushes, status_code, schedule_provisional, is_rest_day, first_in_manual, "
            "last_out_manual) VALUES (:e, "
            "'2026-04-07', '2026-04-07 08:00:00', 1, 1, 0, 0, 'one_punch', "
            "false, false, false, false)"), {"e": employee.id}),
        want="daily_attendance_first_in_says_its_source")

    print("\n-- 4. one punch is a first in and no last out")
    with Session() as session:
        day, _ = setup(session)
        alone = dt.date(2026, 4, 8)
        punch(session, DAY_PIN, dt.datetime.combine(alone, dt.time(8, 5)))
        values = build_day(session, day.id, alone).values
        gate.check(values["punch_count"] == 1
                   and str(values["first_in"]) == "2026-04-08 08:05:00",
                   "first in is the punch", f"got {values['first_in']}")
        gate.check(values["last_out"] is None and values["last_out_source"] is None,
                   "last out is empty, not a copy of the first in",
                   f"got {values['last_out']}")
        gate.check(values["status_code"] == "one_punch",
                   "the status says exactly that", f"got {values['status_code']}")
        gate.check(values["late_minutes"] == 5,
                   "the lateness figure still works off the one punch",
                   f"got {values['late_minutes']}")
        session.rollback()

    gate.refused(
        "a last out on a single punch is refused by the database",
        lambda session, employee: session.execute(text(
            "INSERT INTO daily_attendance (employee_id, attendance_day, "
            "first_in, first_in_source, last_out, last_out_source, punch_count, "
            "device_punch_count, manual_punch_count, duplicate_pushes, status_code, "
            "schedule_provisional, is_rest_day, first_in_manual, last_out_manual) "
            "VALUES (:e, '2026-04-09', "
            "'2026-04-09 08:05:00', 'device', '2026-04-09 08:05:00', 'device', "
            "1, 1, 0, 0, 'one_punch', false, false, false, false)"),
            {"e": employee.id}),
        want="daily_attendance_last_out_needs_two_punches")

    gate.refused(
        "two rows for one employee on one day are refused",
        lambda session, employee: session.execute(text(
            "INSERT INTO daily_attendance (employee_id, attendance_day, "
            "punch_count, device_punch_count, manual_punch_count, "
            "duplicate_pushes, status_code, "
            "schedule_provisional, is_rest_day, first_in_manual, last_out_manual) "
            "VALUES (:e, '2026-04-11', 0, 0, 0, 0, 'no_punch', false, false, "
            "false, false), (:e, '2026-04-11', 0, 0, 0, 0, 'no_punch', false, "
            "false, false, false)"), {"e": employee.id}),
        want="uq_daily_attendance_employee_day")

    gate.refused(
        "a status that is not in the vocabulary is refused",
        lambda session, employee: session.execute(text(
            "INSERT INTO daily_attendance (employee_id, attendance_day, "
            "punch_count, device_punch_count, manual_punch_count, "
            "duplicate_pushes, status_code, "
            "schedule_provisional, is_rest_day, first_in_manual, last_out_manual) "
            "VALUES (:e, '2026-04-12', 0, 0, 0, 0, 'absent', false, false, "
            "false, false)"), {"e": employee.id}),
        want="daily_attendance_status_code_fkey")

    print("\n-- a re-pushed punch is one punch (SPEC §12, §9 A37)")
    with Session() as session:
        day, _ = setup(session)
        when = dt.date(2026, 4, 14)
        at = dt.datetime.combine(when, dt.time(8, 2))
        for _ in range(42):
            punch(session, DAY_PIN, at)
        values = build_day(session, day.id, when).values
        gate.check(values["punch_count"] == 1,
                   "42 pushes of one punch count as one",
                   f"got {values['punch_count']}")
        gate.check(values["duplicate_pushes"] == 41,
                   "and the 41 copies are counted as copies",
                   f"got {values['duplicate_pushes']}")
        gate.check(values["last_out"] is None
                   and values["status_code"] == "one_punch",
                   "so the day is one punch, not a first in equal to a last out",
                   f"got last_out {values['last_out']}")
        gate.check("re-pushing" in (values["note"] or ""),
                   f"the row says why: {values['note']!r}")

        # Two genuine punches a second apart are two punches, not a duplicate.
        punch(session, DAY_PIN, at + dt.timedelta(seconds=1))
        two = build_day(session, day.id, when).values
        gate.check(two["punch_count"] == 2 and two["last_out"] is not None,
                   "a punch one second later is a different punch",
                   f"got {two['punch_count']}")

        # A manual punch is never deduplicated: each is a separate act.
        record_hr_retroactive(session, day, asserted_time=at,
                              reason="device down", made_by="HR: One")
        record_hr_retroactive(session, day, asserted_time=at,
                              reason="device down, second entry",
                              made_by="HR: Two")
        manual = build_day(session, day.id, when).values
        gate.check(manual["manual_punch_count"] == 2,
                   "two HR entries at the same time stay two entries",
                   f"got {manual['manual_punch_count']}")
        session.rollback()

    print("\n-- 5. two rebuilds from the same punches produce the same row")
    with Session() as session:
        day, _ = setup(session)
        when = dt.date(2026, 4, 10)
        punch(session, DAY_PIN, dt.datetime.combine(when, dt.time(8, 12)))
        punch(session, DAY_PIN, dt.datetime.combine(when, dt.time(17, 40)))

        build_days(session, when, when, [day.id])
        first = snapshot(session, day.id, when)
        build_days(session, when, when, [day.id])
        second = snapshot(session, day.id, when)

        differences = {k: (first[k], second[k]) for k in COMPARED
                       if first[k] != second[k]}
        gate.check(not differences, "every compared column is identical",
                   f"differ: {differences}")
        gate.check(len(session.scalars(select(DailyAttendance).where(
            DailyAttendance.employee_id == day.id,
            DailyAttendance.attendance_day == when)).all()) == 1,
            "the rebuild replaced the row rather than adding a second one")

        # The deliberate mistake: something that makes a rebuild disagree with
        # itself. A schedule changed between builds is a *correct* difference,
        # so the check is that the difference is the schedule's, not the row's.
        set_schedule(session, DAY_GROUP, dt.date(2026, 4, 10),
                     **{**DAY_SHIFT, "start_time": dt.time(8, 30)})
        build_days(session, when, when, [day.id])
        third = snapshot(session, day.id, when)
        gate.check(third["late_minutes"] == 0 and first["late_minutes"] == 12,
                   "a corrected schedule does move the figure — that is what a "
                   "rebuild is for", f"got {third['late_minutes']}")
        gate.check(third["punch_count"] == first["punch_count"],
                   "and it does not move the punches")
        session.rollback()

    print("\n-- what this step deliberately does not do")
    with Session() as session:
        columns = set(DailyAttendance.__table__.columns.keys())
        for absent in ("period_total", "deduction", "leave_code", "absent",
                       "work_minutes", "overtime_minutes"):
            gate.check(absent not in columns,
                       f"no {absent} column on the row")
    print("\n-- a figure from a provisional schedule says so on the row")
    with Session() as session:
        day, _ = setup(session)
        when = dt.date(2026, 4, 13)
        punch(session, DAY_PIN, dt.datetime.combine(when, dt.time(8, 25)))
        values = build_day(session, day.id, when).values
        gate.check(values["schedule_provisional"] is True,
                   "the row says the 25-minute figure rests on a provisional "
                   "schedule row", f"got {values['schedule_provisional']}")
        session.execute(
            text("UPDATE group_schedule SET provisional = false "
                 "WHERE group_code = :g"), {"g": DAY_GROUP})
        session.flush()
        confirmed = build_day(session, day.id, when).values
        gate.check(confirmed["schedule_provisional"] is False
                   and confirmed["late_minutes"] == values["late_minutes"],
                   "confirming the schedule changes the flag and not the figure",
                   f"got {confirmed['schedule_provisional']}")
        session.rollback()

    print(f"\n{gate.checks} checks")
    if gate.failures:
        print(f"{len(gate.failures)} FAILED:")
        for failure in gate.failures:
            print(f"  - {failure}")
        return 1
    print("clean")
    return 0


def snapshot(session, employee_id: int, day: dt.date) -> dict:
    session.expire_all()
    row = session.scalars(
        select(DailyAttendance).where(
            DailyAttendance.employee_id == employee_id,
            DailyAttendance.attendance_day == day,
        )
    ).one()
    return {name: getattr(row, name) for name in COMPARED}


if __name__ == "__main__":
    raise SystemExit(main())
