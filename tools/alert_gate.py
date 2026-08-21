#!/usr/bin/env python3
"""The gate for step 9: deliberate mistakes that must fail.

Every case builds its own device, schedule, calendar and punches inside a
transaction that is rolled back, and asks the check at a chosen moment rather
than waiting for one.

The four the step was asked to prove:

  1. silence past the threshold must raise something;
  2. a quiet night and a Sunday must not raise a false alarm;
  3. normal polling with punches arriving must raise nothing;
  4. the threshold is a row — change it and the behaviour changes with it.

    uv run python tools/alert_gate.py

Exits non-zero if any deliberate mistake was accepted.
"""

from __future__ import annotations

import datetime as dt
import pathlib

from sqlalchemy import select, text

from app.alert import CONTACT, PUNCH, RAISED, CLEARED, check, record, latest_state
from app.db import Session
from app.models import (
    AlertSetting,
    Device,
    DeviceState,
    EmployeeGroup,
    Holiday,
    ParsedPunch,
    RawRequest,
)
from app.parser import parse_raw_request
from app.schedule import set_schedule

SERIAL = "GATE-ALERT-0001"
GROUP = "GATE-AL-DAY"

# The site runs +8, so a UTC moment is chosen for each local time it means.
# 2026-03-04 is a Wednesday; 2026-03-08 is a Sunday.
def utc(day: dt.date, hour: int, minute: int = 0) -> dt.datetime:
    """A UTC instant for a local wall-clock time at +8."""
    local = dt.datetime.combine(day, dt.time(hour, minute))
    return (local - dt.timedelta(hours=8)).replace(tzinfo=dt.timezone.utc)


WEDNESDAY = dt.date(2026, 3, 4)
SUNDAY = dt.date(2026, 3, 8)
HOLIDAY = dt.date(2026, 3, 12)

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


def isolate(session) -> None:
    """Leave only this gate's schedule in force.

    Whether punches are due is asked of every group in the database, which is
    right — a night shift running at 02:00 means punches *are* due at 02:00.
    That also means a gate that asserts "02:00 is quiet" is really asserting
    something about whatever happens to be loaded. This removes the ambient
    schedules inside the transaction, so each case tests the alert rather than
    the fixture. Everything here is rolled back.
    """
    session.execute(text("DELETE FROM daily_attendance"))
    session.execute(text("DELETE FROM manual_punch"))
    session.execute(text("DELETE FROM group_schedule WHERE group_code <> :g"),
                    {"g": GROUP})
    session.flush()


def setup(session) -> None:
    session.add(Device(serial_number=SERIAL, label="gate device"))
    session.add(EmployeeGroup(code=GROUP, label=GROUP, note="gate"))
    session.flush()
    set_schedule(session, GROUP, dt.date(2020, 1, 1), **DAY_SHIFT)
    session.add(Holiday(holiday_date=HOLIDAY, name="Gate Holiday",
                        scope_code="federal", closes=True, provisional=True))
    session.flush()
    isolate(session)


def arrive(session, at: dt.datetime, punch_at: dt.datetime | None = None) -> None:
    """A request landing at `at` (server clock). With `punch_at`, it is an
    ATTLOG carrying one punch; without, it is the poll the device sends every
    few seconds."""
    if punch_at is None:
        raw = RawRequest(
            received_at=at, method="GET", path="/iclock/getrequest",
            query_string=f"SN={SERIAL}", headers=[], body=b"", body_bytes=0,
            serial_number=SERIAL, response_body="OK")
        session.add(raw)
        session.flush()
        return
    fields = ["1", punch_at.strftime("%Y-%m-%d %H:%M:%S"), "255", "15"] + ["0"] * 6
    body = ("\t".join(fields) + "\t\r\n").encode()
    raw = RawRequest(
        received_at=at, method="POST", path="/iclock/cdata",
        query_string=f"SN={SERIAL}&table=ATTLOG&Stamp=9999", headers=[],
        content_type="text/plain", body=body, body_bytes=len(body),
        serial_number=SERIAL, table_param="ATTLOG", stamp_param="9999",
        response_body="OK: 1")
    session.add(raw)
    session.flush()
    parse_raw_request(session, raw)
    session.flush()


def one(statuses):
    return next(s for s in statuses if s.serial_number == SERIAL)


def kinds(status) -> set[str]:
    return {alarm.kind for alarm in status.alarms}


def main() -> int:
    gate = Gate()

    print("\n-- the thresholds are rows")
    with Session() as session:
        keys = set(session.scalars(select(AlertSetting.key)))
        for key in ("alert.contact_silence_minutes", "alert.punch_silence_minutes",
                    "alert.punch_expected_after_minutes",
                    "alert.check_punches_when_closed",
                    "alert.watch_only_after_first_contact"):
            gate.check(key in keys, f"{key} is a row", f"rows: {sorted(keys)}")

    print("\n-- 3. polling normally with punches arriving raises nothing")
    with Session() as session:
        setup(session)
        now = utc(WEDNESDAY, 11, 0)
        arrive(session, now - dt.timedelta(seconds=20))
        arrive(session, now - dt.timedelta(minutes=45),
               punch_at=dt.datetime.combine(WEDNESDAY, dt.time(7, 58)))
        status = one(check(session, now=now))
        gate.check(not status.alarming, "nothing is raised",
                   f"raised {kinds(status)}")
        gate.check(status.punches_expected,
                   "and punches were expected at 11:00 on a Wednesday",
                   status.expectation)
        session.rollback()

    print("\n-- 1. silence past the threshold raises")
    with Session() as session:
        setup(session)
        now = utc(WEDNESDAY, 11, 0)
        arrive(session, now - dt.timedelta(hours=3))
        arrive(session, now - dt.timedelta(hours=3, minutes=1),
               punch_at=dt.datetime.combine(WEDNESDAY, dt.time(7, 58)))
        status = one(check(session, now=now))
        gate.check(CONTACT in kinds(status),
                   "three hours of total silence raises the contact alarm",
                   f"raised {kinds(status)}")
        gate.check(status.minutes_since_contact == 180,
                   "and it says how long", f"got {status.minutes_since_contact}")
        gate.check(any("Cloud Server" in a.detail for a in status.alarms),
                   "and what it usually means (SPEC §10)")

        written = record(session, [status])
        gate.check(any(row.state == RAISED for row in written),
                   "the transition is recorded once")
        again = record(session, [one(check(session, now=now))])
        gate.check(not again,
                   "and a second check does not write a second row",
                   f"wrote {[(r.kind, r.state) for r in again]}")

        # It clears when contact resumes, and the clear is recorded.
        arrive(session, now)
        cleared = record(session, [one(check(session, now=now))])
        gate.check(any(row.kind == CONTACT and row.state == CLEARED
                       for row in cleared),
                   "and it clears when the device comes back",
                   f"wrote {[(r.kind, r.state) for r in cleared]}")
        session.rollback()

    print("\n-- 1b. the device talking but no punch, mid-shift, raises")
    with Session() as session:
        setup(session)
        now = utc(WEDNESDAY, 14, 0)
        for minutes in range(0, 400, 10):
            arrive(session, now - dt.timedelta(minutes=minutes))
        arrive(session, now - dt.timedelta(hours=5),
               punch_at=dt.datetime.combine(WEDNESDAY - dt.timedelta(days=1),
                                            dt.time(8, 1)))
        status = one(check(session, now=now))
        gate.check(CONTACT not in kinds(status),
                   "contact is fine — the polls are arriving")
        gate.check(PUNCH in kinds(status),
                   "but the punch alarm is raised", f"raised {kinds(status)}")
        gate.check(any("shift" in a.detail for a in status.alarms),
                   "and it says which shift was running", str(status.expectation))
        session.rollback()

    print("\n-- 2. a quiet night, a Sunday and a closed holiday raise nothing")
    # Each moment gets its own transaction: the raw layer is append-only and
    # refuses a DELETE, which is the rule doing its job even in a test.
    for label, moment in (
        ("02:00 on a weekday, with only a day shift configured",
         utc(WEDNESDAY, 2, 0)),
        ("07:30, before the shift has run long enough", utc(WEDNESDAY, 7, 30)),
        ("20:00, after the shift ended", utc(WEDNESDAY, 20, 0)),
        ("11:00 on a Sunday", utc(SUNDAY, 11, 0)),
        ("11:00 on a holiday the factory closes for", utc(HOLIDAY, 11, 0)),
    ):
        with Session() as session:
            setup(session)
            arrive(session, moment - dt.timedelta(seconds=15))
            arrive(session, moment - dt.timedelta(days=4),
                   punch_at=dt.datetime.combine(dt.date(2026, 2, 28), dt.time(8, 0)))
            status = one(check(session, now=moment))
            gate.check(PUNCH not in kinds(status),
                       f"no punch alarm at {label}",
                       f"raised {kinds(status)}: {status.expectation}")
            gate.check(not status.punches_expected,
                       f"and the reason is on the status: {status.expectation}")
            session.rollback()

    # The deliberate mistake: one threshold over "time since last punch" would
    # have alarmed at every one of those moments.
    with Session() as session:
        setup(session)
        moment = utc(SUNDAY, 11, 0)
        arrive(session, moment - dt.timedelta(seconds=15))
        arrive(session, moment - dt.timedelta(days=2),
               punch_at=dt.datetime.combine(dt.date(2026, 3, 6), dt.time(8, 0)))
        status = one(check(session, now=moment))
        gate.check(status.minutes_since_punch > 2000 and PUNCH not in kinds(status),
                   "two days without a punch on a Sunday is silence, not a fault"
                   f" — {status.minutes_since_punch} minutes and no alarm",
                   f"raised {kinds(status)}")
        gate.check(CONTACT not in kinds(status),
                   "and contact is still checked on a Sunday, because the "
                   "device polls whether or not the factory runs")
        session.rollback()

    print("\n-- 2a. but a night shift running at 02:00 does expect punches")
    with Session() as session:
        setup(session)
        set_schedule(session, GROUP, dt.date(2026, 1, 1), **{
            **DAY_SHIFT, "start_time": dt.time(19, 30),
            "end_time": dt.time(4, 30), "end_next_day": True})
        moment = utc(WEDNESDAY, 2, 0)
        arrive(session, moment - dt.timedelta(seconds=15))
        arrive(session, moment - dt.timedelta(days=2),
               punch_at=dt.datetime.combine(dt.date(2026, 3, 1), dt.time(20, 0)))
        status = one(check(session, now=moment))
        gate.check(status.punches_expected and PUNCH in kinds(status),
                   "a night shift silent at 02:00 is a fault, not a quiet night "
                   "— the calendar and the schedule decide, not the clock",
                   f"expected={status.punches_expected} raised={kinds(status)}")
        gate.check("19:30" in status.expectation,
                   f"and it names the shift: {status.expectation}")
        session.rollback()

    print("\n-- 2b. contact silence is caught on a Sunday, unlike punch silence")
    with Session() as session:
        setup(session)
        moment = utc(SUNDAY, 11, 0)
        arrive(session, moment - dt.timedelta(hours=6))
        status = one(check(session, now=moment))
        gate.check(CONTACT in kinds(status),
                   "the receiver being down all Sunday morning is caught",
                   f"raised {kinds(status)}")
        gate.check(PUNCH not in kinds(status),
                   "and it is not confused with nobody punching")
        session.rollback()

    print("\n-- 4. the thresholds are rows: change one and the answer changes")
    with Session() as session:
        setup(session)
        now = utc(WEDNESDAY, 11, 0)
        arrive(session, now - dt.timedelta(minutes=45))
        status = one(check(session, now=now))
        gate.check(CONTACT in kinds(status),
                   "45 minutes of silence alarms at the seeded 15-minute row")

        session.execute(
            text("UPDATE alert_setting SET value = '600' "
                 "WHERE key = 'alert.contact_silence_minutes'"))
        session.flush()
        relaxed = one(check(session, now=now))
        gate.check(CONTACT not in kinds(relaxed),
                   "the same silence does not alarm at 600 — an UPDATE, not a "
                   "code change", f"raised {kinds(relaxed)}")

        session.execute(
            text("UPDATE alert_setting SET value = '5' "
                 "WHERE key = 'alert.contact_silence_minutes'"))
        session.execute(
            text("UPDATE alert_setting SET value = '30' "
                 "WHERE key = 'alert.punch_expected_after_minutes'"))
        session.flush()
        tightened = one(check(session, now=now))
        gate.check(CONTACT in kinds(tightened),
                   "and alarms again at 5 minutes")

        # The other half of the same rule: whether closed days are checked.
        session.execute(
            text("UPDATE alert_setting SET value = 'yes' "
                 "WHERE key = 'alert.check_punches_when_closed'"))
        session.flush()
        sunday = one(check(session, now=utc(SUNDAY, 11, 0)))
        gate.check(sunday.punches_expected,
                   "with check_punches_when_closed = yes, a Sunday expects "
                   "punches — the row decides, not the code",
                   sunday.expectation)
        session.rollback()

    print("\n-- a device that is knowingly down is not an outage")
    with Session() as session:
        setup(session)
        now = utc(WEDNESDAY, 11, 0)
        arrive(session, now - dt.timedelta(hours=6))
        raised = one(check(session, now=now))
        gate.check(CONTACT in kinds(raised),
                   "six hours of silence alarms while the device is live",
                   f"raised {kinds(raised)}")
        record(session, [raised])

        session.execute(
            text("UPDATE device SET state_code = 'down', "
                 "state_reason = 'off the wall for repair' WHERE serial_number = :s"),
            {"s": SERIAL})
        session.flush()
        session.expire_all()
        stood_down = one(check(session, now=now))
        gate.check(not stood_down.alarming and not stood_down.watched,
                   "the same silence alarms nothing once it is stood down",
                   f"raised {kinds(stood_down)}")
        gate.check("repair" in stood_down.why_unwatched,
                   f"and the reason travels with it: {stood_down.why_unwatched!r}")

        cleared = record(session, [stood_down])
        gate.check(any(row.kind == CONTACT and row.state == CLEARED
                       for row in cleared),
                   "standing alerts are cleared rather than left raised forever",
                   f"wrote {[(r.kind, r.state) for r in cleared]}")
        gate.check(latest_state(session, SERIAL, CONTACT) == CLEARED,
                   "so nothing is left showing raised on a device nobody expects "
                   "to hear from")

        # Still recognised, still stored, still on the list.
        device = session.get(Device, SERIAL)
        gate.check(device is not None and device.state_code == "down",
                   "the serial is still on the allowlist, not deleted")
        arrive(session, now)
        gate.check(session.get(Device, SERIAL) is not None,
                   "and its requests are still captured and stored")

        # Retired behaves the same way, and live brings it back.
        session.execute(
            text("UPDATE device SET state_code = 'retired' WHERE serial_number = :s"),
            {"s": SERIAL})
        session.flush()
        session.expire_all()
        gate.check(not one(check(session, now=now)).watched,
                   "a retired serial is not watched either")
        session.execute(
            text("UPDATE device SET state_code = 'live' WHERE serial_number = :s"),
            {"s": SERIAL})
        session.flush()
        session.expire_all()
        # The last request above was at `now`, so the watch is asked about a
        # later moment: six hours of silence after it came back.
        later = now + dt.timedelta(hours=6)
        gate.check(CONTACT in kinds(one(check(session, now=later))),
                   "and setting it live again starts the watch",
                   f"raised {kinds(one(check(session, now=later)))}")
        session.rollback()

    print("\n-- which states are watched is a row, not a branch")
    with Session() as session:
        setup(session)
        codes = {row.code: row.alerted
                 for row in session.scalars(select(DeviceState))}
        gate.check(codes == {"live": True, "down": False, "retired": False},
                   "three states, one of them watched", f"got {codes}")

        now = utc(WEDNESDAY, 11, 0)
        arrive(session, now - dt.timedelta(hours=6))
        session.execute(
            text("UPDATE device SET state_code = 'down' WHERE serial_number = :s"),
            {"s": SERIAL})
        session.flush()
        session.expire_all()
        gate.check(not one(check(session, now=now)).alarming,
                   "down is quiet")
        session.execute(
            text("UPDATE device_state SET alerted = true WHERE code = 'down'"))
        session.flush()
        session.expire_all()
        gate.check(CONTACT in kinds(one(check(session, now=now))),
                   "flipping the row's alerted flag makes it alarm — the row "
                   "decides, not the code")
        session.rollback()

    print("\n-- a serial that has never been heard from is not an outage (A45)")
    with Session() as session:
        setup(session)
        status = one(check(session, now=utc(WEDNESDAY, 11, 0)))
        gate.check(not status.watched and not status.alarming,
                   "a device on the allowlist that has never called is not "
                   "alarming", f"raised {kinds(status)}")
        gate.check(not record(session, [status]),
                   "and nothing is recorded about it")

        session.execute(
            text("UPDATE alert_setting SET value = 'no' "
                 "WHERE key = 'alert.watch_only_after_first_contact'"))
        session.flush()
        watched = one(check(session, now=utc(WEDNESDAY, 11, 0)))
        gate.check(CONTACT in kinds(watched),
                   "unless the row says to watch it from the moment it is "
                   "added", f"raised {kinds(watched)}")
        session.rollback()

    print("\n-- what this step deliberately does not do")
    # Checked on the imports rather than on the prose: the docstring talks
    # about polls and channels, and a substring hunt would match that.
    import ast
    import app.alert as alert_module

    tree = ast.parse(pathlib.Path(alert_module.__file__).read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    for module, why in (
        ("requests", "no notification channel"),
        ("httpx", "no HTTP client"),
        ("urllib", "nothing reaches out"),
        ("socket", "nothing opens a connection"),
        ("smtplib", "no mail client"),
        ("subprocess", "nothing shells out"),
    ):
        gate.check(module not in imported, f"{why}: app/alert.py does not import "
                                           f"{module}", f"imports {sorted(imported)}")
    gate.check(imported <= {"__future__", "datetime", "dataclasses", "zoneinfo",
                            "sqlalchemy", "app"},
               "it imports the database and the clock, and nothing else",
               f"imports {sorted(imported)}")

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
