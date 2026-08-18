#!/usr/bin/env python3
"""The gate for step 4: deliberate mistakes that must fail.

Every case runs in a transaction that is rolled back, and builds the employee,
schedule, mapping and device punch it needs, so nothing depends on what happens
to be loaded.

    uv run python tools/corrections_gate.py

Exits non-zero if any deliberate mistake was accepted.
"""

from __future__ import annotations

import datetime as dt
import sys

import psycopg
from sqlalchemy import select, text, update
from sqlalchemy.exc import DatabaseError, IntegrityError

from app.corrections import (
    correction_counts,
    employee_by_number,
    punches_for,
    record_guard_entry,
    record_hr_retroactive,
)
from app.db import Session
from app.models import (
    DeviceUserMap,
    Employee,
    EmployeeAssignment,
    EmployeeGroup,
    EmployeeNumberKey,
    EmploymentPeriod,
    ManualPunch,
    ParsedPunch,
    RawRequest,
)
from app.parser import parse_raw_request, replay
from app.schedule import set_schedule

TODAY = dt.date.today()
GROUP = "GATE-CORR"
NUMBER = "9401"
PIN = "9401"

DAY_SHIFT = {
    "start_time": dt.time(8, 0),
    "end_time": dt.time(17, 30),
    "end_next_day": False,
    "rest_weekdays": [7],
    "grace_minutes": 0,
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

    def refused(self, what: str, fn, expect=(IntegrityError, DatabaseError,
                                            psycopg.Error, ValueError, TypeError)) -> None:
        with Session() as session:
            try:
                setup(session)
                fn(session)
                session.flush()
            except expect as exc:
                line = str(exc).strip().splitlines()[0]
                self.check(True, what)
                print(f"            {line[:112]}")
            except Exception as exc:  # pragma: no cover - unexpected failure
                self.check(False, what, f"failed for the wrong reason: {exc!r}")
            else:
                self.check(False, what, "it was accepted")
            finally:
                session.rollback()


def setup(session) -> Employee:
    """One employee, in a group with a schedule, with a device PIN mapped."""
    session.add(EmployeeGroup(code=GROUP, label=GROUP, note="gate"))
    session.flush()
    set_schedule(session, GROUP, dt.date(2020, 1, 1), **DAY_SHIFT)

    employee = Employee(employee_number=NUMBER)
    session.add(employee)
    session.flush()
    session.add(EmployeeNumberKey(
        employee_id=employee.id, key=NUMBER, built_by="gate"))
    session.add(EmployeeAssignment(
        employee_id=employee.id, effective_from=dt.date(2020, 1, 1),
        name="Gate Test", section_code="QC", role_code="QA/QC",
        group_code=GROUP))
    session.add(EmploymentPeriod(
        employee_id=employee.id, active_from=dt.date(2020, 1, 1)))
    session.add(DeviceUserMap(
        employee_id=employee.id, pin=PIN, effective_from=dt.date(2020, 1, 1),
        source="gate"))
    session.flush()
    return employee


def add_device_punch(session, employee: Employee, at: dt.datetime) -> RawRequest:
    """A real device punch: a raw request, parsed by the parser."""
    body = (f"{PIN}\t{at.strftime('%Y-%m-%d %H:%M:%S')}\t0\t1\t0\t0\t0\r\n").encode()
    raw = RawRequest(
        method="POST", path="/iclock/cdata",
        query_string="SN=SIM0000000001&table=ATTLOG&Stamp=9999",
        headers=[["content-type", "text/plain"]], content_type="text/plain",
        body=body, body_bytes=len(body), serial_number="SIM0000000001",
        table_param="ATTLOG", stamp_param="9999", response_body="OK: 1",
    )
    session.add(raw)
    session.flush()
    parse_raw_request(session, raw)
    session.flush()
    return raw


def main() -> int:
    gate = Gate()

    print("\n-- a guard entry given a time")
    gate.refused(
        "the guard entry function will not take a time",
        lambda session: record_guard_entry(
            session, employee_by_number(session, NUMBER),
            reason_code="biometric_failed", made_by="Guard: Suresh",
            asserted_time=dt.datetime(2026, 8, 17, 6, 0, 0),
        ),
        expect=(TypeError,),
    )
    gate.refused(
        "a guard row carrying a time, written straight to the table",
        lambda session: session.add(ManualPunch(
            employee_id=employee_by_number(session, NUMBER).id,
            path="guard", asserted_time=dt.datetime(2026, 8, 17, 6, 0, 0),
            attendance_day=TODAY, reason_code="biometric_failed",
            made_by="Guard: Suresh")),
    )

    print("\n-- a correction that edits punch data instead of sitting beside it")
    def edit_raw(session):
        raw = add_device_punch(session, employee_by_number(session, NUMBER),
                               dt.datetime.combine(TODAY, dt.time(8, 2)))
        session.execute(
            update(RawRequest).where(RawRequest.id == raw.id).values(body=b"edited"))
    gate.refused("editing the raw request the punch came from", edit_raw)

    def delete_raw(session):
        raw = add_device_punch(session, employee_by_number(session, NUMBER),
                               dt.datetime.combine(TODAY, dt.time(8, 2)))
        session.execute(text("DELETE FROM raw_request WHERE id = :id"),
                        {"id": raw.id})
    gate.refused("deleting it instead", delete_raw)

    with Session() as session:
        try:
            employee = setup(session)
            add_device_punch(session, employee, dt.datetime.combine(TODAY, dt.time(8, 2)))
            punch = session.scalars(
                select(ParsedPunch).where(ParsedPunch.pin == PIN)).first()
            original = punch.punch_time
            punch.punch_time = dt.datetime.combine(TODAY, dt.time(7, 55))
            session.flush()
            replay(session)
            session.flush()
            after = session.scalars(
                select(ParsedPunch).where(ParsedPunch.pin == PIN)).first()
            gate.check(
                after.punch_time == original,
                "editing a parsed punch does not survive — the raw layer wins",
                f"the edit stuck: {after.punch_time}",
            )
        finally:
            session.rollback()

    print("\n-- an unmarked correction")
    gate.refused(
        "a correction claiming to be a device punch",
        lambda session: session.add(ManualPunch(
            employee_id=employee_by_number(session, NUMBER).id,
            path="device", asserted_time=dt.datetime.combine(TODAY, dt.time(8, 2)),
            attendance_day=TODAY, reason="looks like the device said it",
            made_by="someone")),
    )
    gate.refused(
        "a correction with nobody's name on it",
        lambda session: session.add(ManualPunch(
            employee_id=employee_by_number(session, NUMBER).id,
            path="hr_retroactive",
            asserted_time=dt.datetime.combine(TODAY, dt.time(8, 2)),
            attendance_day=TODAY, reason="forgotten punch", made_by="   ")),
    )

    print("\n-- a correction for an employee number that does not exist")
    gate.refused(
        "a guard entry for a number nobody recognises",
        lambda session: record_guard_entry(
            session, employee_by_number(session, "9999"),
            reason_code="biometric_failed", made_by="Guard: Suresh"),
    )
    gate.refused(
        "a manual punch written against an employee id that is not there",
        lambda session: session.add(ManualPunch(
            employee_id=987654321, path="hr_retroactive",
            asserted_time=dt.datetime.combine(TODAY, dt.time(8, 2)),
            attendance_day=TODAY, reason="forgotten punch", made_by="HR: Mei Ling")),
    )

    print("\n-- an HR retroactive entry with no reason")
    gate.refused(
        "the retroactive function refuses an empty reason",
        lambda session: record_hr_retroactive(
            session, employee_by_number(session, NUMBER),
            asserted_time=dt.datetime.combine(TODAY, dt.time(8, 2)),
            reason="   ", made_by="HR: Mei Ling"),
        expect=(ValueError,),
    )
    gate.refused(
        "a retroactive row with no reason, written straight to the table",
        lambda session: session.add(ManualPunch(
            employee_id=employee_by_number(session, NUMBER).id,
            path="hr_retroactive",
            asserted_time=dt.datetime.combine(TODAY, dt.time(8, 2)),
            attendance_day=TODAY, reason=None, made_by="HR: Mei Ling")),
    )

    print("\n-- the guard's entry is stamped by the server, whatever the caller wants")
    with Session() as session:
        try:
            employee = setup(session)
            before = dt.datetime.now(dt.timezone.utc)
            punch = record_guard_entry(
                session, employee, reason_code="biometric_failed",
                made_by="Guard: Suresh")
            session.refresh(punch)
            after = dt.datetime.now(dt.timezone.utc)
            gate.check(
                before <= punch.recorded_at <= after,
                "the stamp is the moment of entry, not a value passed in",
                f"stamped {punch.recorded_at}",
            )
            gate.check(punch.asserted_time is None,
                       "and there is no claimed time on the row at all")
        finally:
            session.rollback()

    print("\n-- counting, which is what finds a bad enrollment")
    with Session() as session:
        try:
            employee = setup(session)
            for _ in range(3):
                record_guard_entry(session, employee,
                                   reason_code="biometric_failed",
                                   made_by="Guard: Suresh")
            record_hr_retroactive(
                session, employee,
                asserted_time=dt.datetime.combine(TODAY, dt.time(17, 35)),
                reason="device down after the power cut", made_by="HR: Mei Ling")
            rows = correction_counts(session, TODAY - dt.timedelta(days=30),
                                     TODAY + dt.timedelta(days=1))
            counted = {(number, path): entries for number, path, entries, _, _ in rows}
            gate.check(
                counted.get((NUMBER, "guard")) == 3
                and counted.get((NUMBER, "hr_retroactive")) == 1,
                "manual punches are counted per employee per period, by path",
                f"got {counted}",
            )
        finally:
            session.rollback()

    print("\n-- not a gate: one day, one employee, all three kinds read together")
    with Session() as session:
        try:
            employee = setup(session)
            add_device_punch(session, employee,
                             dt.datetime.combine(TODAY, dt.time(8, 2, 14)))
            record_guard_entry(session, employee, reason_code="biometric_failed",
                               made_by="Guard: Suresh",
                               note="fingerprint would not read, wet hands")
            record_hr_retroactive(
                session, employee,
                asserted_time=dt.datetime.combine(TODAY, dt.time(17, 35)),
                reason="device down after the power cut; supervisor confirmed",
                made_by="HR: Mei Ling")
            records = punches_for(session, employee.id, TODAY)

            print(f"            employee {NUMBER}, attendance day {TODAY}")
            for record in records:
                at = record.at.strftime("%H:%M:%S") if record.at else "-"
                print(f"              {at}  {record.source:15} "
                      f"manual={'YES' if record.manual else 'no '}  "
                      f"{(record.who or '—'):16} {record.why or ''}")
                print(f"                        {record.evidence}")
            gate.check(len(records) == 3, "all three are on the day",
                       f"got {len(records)}")
            gate.check(
                all(r.source for r in records)
                and [r.manual for r in records].count(True) == 2,
                "every line says where it came from, and two say a person made them",
            )
            gate.check(
                all(r.who and r.why for r in records if r.manual),
                "each manual line carries who made it and why",
            )
            gate.check(
                not any(r.who for r in records if not r.manual),
                "the device line has nobody's name on it — it was not entered",
            )
        finally:
            session.rollback()

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
