"""Corrections: the two ways a punch that did not happen at the device gets on
the record, and the read that keeps them visible.

SPEC §3 sets the shape. A correction is a row beside the punch data, never an
edit to it. Every row carries who made it, when, and why. The guard's path has
no time field at all — that is not an oversight to be tidied up later, it is the
difference between a correction and buddy punching with a log.

Counting is not decoration either. A rising count for one employee means a bad
enrollment or a process being worked around, so the count is queryable per
employee per period from the first row written.

What this module deliberately does not do: decide anything. No first in, no last
out, no late minutes, no status, no absence. Those are steps 6 and 7. `punches_for`
lists what is on the record for a day and says where each line came from.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.models import (
    CorrectionReason,
    DeviceUserMap,
    Employee,
    EmployeeAssignment,
    EmployeeNumberKey,
    ManualPunch,
    ManualPunchCancellation,
    ParsedPunch,
    RawRequest,
    SiteSetting,
)
from app.schedule import attendance_day_for

GUARD = "guard"
HR_RETROACTIVE = "hr_retroactive"

DEFAULT_TIMEZONE = "Asia/Kuala_Lumpur"


@dataclass
class PunchRecord:
    """One line of a day's punch detail, from wherever it came.

    `source` and `manual` are on every row. There is no way to read this list
    and not know which lines a person entered.

    **`cancelled` is on every row too, and a cancelled row is still returned.**
    A punch that vanishes from the detail when it is taken out of the figures
    is indistinguishable from one nobody ever made, which is the hole §3 keeps
    closing. What changes is that the figures stop counting it — that happens
    where the figures are built, not by dropping the line here.
    """

    at: dt.datetime | None
    source: str
    manual: bool
    attendance_day: dt.date
    who: str | None = None
    why: str | None = None
    recorded_at: dt.datetime | None = None
    evidence: str = ""
    punch_id: int | None = None
    cancelled: bool = False
    cancelled_by: str | None = None
    cancelled_why: str | None = None
    cancelled_at: dt.datetime | None = None


def site_timezone(session) -> str:
    """A19-style assumption, kept as a row (SPEC §9 A32)."""
    row = session.get(SiteSetting, "timezone")
    return row.value if row else DEFAULT_TIMEZONE


def local_now(session) -> tuple[dt.datetime, dt.datetime]:
    """(server instant with its timezone, the same instant as a local wall
    clock). Server-observed times carry a timezone; the local form is what a
    device punch would have looked like (SPEC §14)."""
    now = dt.datetime.now(dt.timezone.utc)
    local = now.astimezone(ZoneInfo(site_timezone(session))).replace(tzinfo=None)
    return now, local


def employee_by_number(session, number: str) -> Employee:
    """Find an employee by the number as printed, or by its matching key.

    A correction is made against an employee who exists. There is no path that
    creates one, and no path that records a correction against a number nobody
    recognises.
    """
    employee = session.scalars(
        select(Employee).where(Employee.employee_number == number)
    ).first()
    if employee is not None:
        return employee
    employee = session.scalars(
        select(Employee)
        .join(EmployeeNumberKey, EmployeeNumberKey.employee_id == Employee.id)
        .where(EmployeeNumberKey.key == number)
    ).first()
    if employee is not None:
        return employee
    raise ValueError(
        f"no employee with number {number!r}. A correction is recorded against "
        "an employee on the list, never against a number that is not on it."
    )


def group_for(session, employee_id: int, on_date: dt.date) -> str | None:
    """The group in force on that date — which decides the schedule, and so the
    attendance day."""
    return session.scalars(
        select(EmployeeAssignment.group_code)
        .where(
            EmployeeAssignment.employee_id == employee_id,
            EmployeeAssignment.effective_from <= on_date,
            (EmployeeAssignment.effective_to.is_(None))
            | (EmployeeAssignment.effective_to >= on_date),
        )
        .order_by(EmployeeAssignment.effective_from.desc())
        .limit(1)
    ).first()


def attendance_day_of(session, employee_id: int, at: dt.datetime) -> tuple[dt.date, int | None]:
    """Which attendance day a moment belongs to, for this employee.

    Uses the schedule in force, so a night-shift correction at 04:35 lands on
    the previous day exactly as a device punch would. Falls back to the calendar
    date when the employee has no group or no schedule yet — a fact to carry,
    not a reason to refuse the correction.
    """
    group = group_for(session, employee_id, at.date())
    if group is None:
        return at.date(), None
    landed = attendance_day_for(session, group, at)
    return landed.date, landed.schedule_id


def record_guard_entry(session, employee: Employee, *, reason_code: str,
                       made_by: str, note: str | None = None) -> ManualPunch:
    """The guard records that this employee is standing in front of him.

    There is no time parameter, and there is no way to add one without changing
    this signature and the check constraint behind it. The moment is the
    server's.
    """
    reason = session.get(CorrectionReason, reason_code)
    if reason is None or reason.path != GUARD:
        allowed = list(session.scalars(
            select(CorrectionReason.code).where(CorrectionReason.path == GUARD)
        ))
        raise ValueError(
            f"{reason_code!r} is not a reason a guard entry may give. "
            f"Allowed: {allowed}"
        )
    if not made_by or not made_by.strip():
        raise ValueError("a guard entry records which guard made it")

    _, local = local_now(session)
    day, schedule_id = attendance_day_of(session, employee.id, local)
    punch = ManualPunch(
        employee_id=employee.id,
        path=GUARD,
        asserted_time=None,
        attendance_day=day,
        schedule_id=schedule_id,
        reason_code=reason_code,
        reason=None,
        made_by=made_by.strip(),
        note=note,
    )
    session.add(punch)
    session.flush()

    # The row is on the record before its day is worked out, and the day is
    # worked out from the server's own stamp rather than from this process's
    # clock. Nothing a caller passes in can reach either.
    session.refresh(punch)
    stamped_local = punch.recorded_at.astimezone(
        ZoneInfo(site_timezone(session))
    ).replace(tzinfo=None)
    punch.attendance_day, punch.schedule_id = attendance_day_of(
        session, employee.id, stamped_local
    )
    session.flush()
    return punch


def record_hr_retroactive(session, employee: Employee, *, asserted_time: dt.datetime,
                          reason: str, made_by: str,
                          note: str | None = None) -> ManualPunch:
    """HR corrects a punch after the fact — a device that was down, a punch
    somebody forgot. The time is entered, and the reason is required."""
    if asserted_time is None:
        raise ValueError("a retroactive entry states the time it is correcting")
    if not reason or not reason.strip():
        raise ValueError(
            "a retroactive entry records why. Every adjustment carries who made "
            "it and why (SPEC §3)"
        )
    if not made_by or not made_by.strip():
        raise ValueError("a retroactive entry records who made it")

    day, schedule_id = attendance_day_of(session, employee.id, asserted_time)
    punch = ManualPunch(
        employee_id=employee.id,
        path=HR_RETROACTIVE,
        asserted_time=asserted_time,
        attendance_day=day,
        schedule_id=schedule_id,
        reason_code=None,
        reason=reason.strip(),
        made_by=made_by.strip(),
        note=note,
    )
    session.add(punch)
    session.flush()
    return punch


def device_punches_for(session, employee_id: int, day: dt.date) -> list[PunchRecord]:
    """Device punches that belong to this employee on this attendance day.

    The PIN is resolved here, downstream, using the mapping in force on the
    punch's own date — never at capture (SPEC §2, §13).
    """
    mappings = session.scalars(
        select(DeviceUserMap).where(DeviceUserMap.employee_id == employee_id)
    ).all()
    if not mappings:
        return []

    pins = sorted({m.pin for m in mappings})
    window_start = dt.datetime.combine(day - dt.timedelta(days=1), dt.time.min)
    window_end = dt.datetime.combine(day + dt.timedelta(days=2), dt.time.min)

    rows = session.execute(
        select(ParsedPunch, RawRequest.received_at)
        .join(RawRequest, RawRequest.id == ParsedPunch.raw_request_id)
        .where(
            ParsedPunch.pin.in_(pins),
            ParsedPunch.parse_ok.is_(True),
            ParsedPunch.punch_time >= window_start,
            ParsedPunch.punch_time < window_end,
        )
        .order_by(ParsedPunch.punch_time)
    ).all()

    records = []
    for punch, received_at in rows:
        punch_date = punch.punch_time.date()
        in_force = [
            m for m in mappings
            if m.pin == punch.pin
            and m.effective_from <= punch_date
            and (m.effective_to is None or m.effective_to >= punch_date)
        ]
        if not in_force:
            continue
        landed, _ = attendance_day_of(session, employee_id, punch.punch_time)
        if landed != day:
            continue
        records.append(PunchRecord(
            at=punch.punch_time,
            source="device",
            manual=False,
            attendance_day=landed,
            who=None,
            why=None,
            recorded_at=received_at,
            evidence=(
                f"pin {punch.pin}, verify {punch.verify_code}, "
                f"raw_request {punch.raw_request_id}, parsed_punch {punch.id}"
            ),
        ))
    return records


def manual_punches_for(session, employee_id: int, day: dt.date) -> list[PunchRecord]:
    """Every correction on this day, cancelled ones included and marked."""
    rows = session.execute(
        select(ManualPunch, ManualPunchCancellation)
        .outerjoin(ManualPunchCancellation,
                   ManualPunchCancellation.manual_punch_id == ManualPunch.id)
        .where(
            ManualPunch.employee_id == employee_id,
            ManualPunch.attendance_day == day,
        )
        .order_by(ManualPunch.recorded_at)
    ).all()
    timezone = ZoneInfo(site_timezone(session))

    records = []
    for row, cancellation in rows:
        if row.path == GUARD:
            at = row.recorded_at.astimezone(timezone).replace(tzinfo=None)
            source = "guard entry"
            reason = session.get(CorrectionReason, row.reason_code)
            why = reason.label if reason else row.reason_code
        else:
            at = row.asserted_time
            source = "HR retroactive"
            why = row.reason
        evidence = f"manual_punch {row.id}"
        if row.path == GUARD:
            evidence += ", server-stamped, no time was typed"
        if cancellation is not None:
            evidence += (f"; cancelled by manual_punch_cancellation "
                         f"{cancellation.id} — the punch row is unchanged")
        records.append(PunchRecord(
            at=at,
            source=source,
            manual=True,
            attendance_day=row.attendance_day,
            who=row.made_by,
            why=why,
            recorded_at=row.recorded_at,
            evidence=evidence,
            punch_id=row.id,
            cancelled=cancellation is not None,
            cancelled_by=cancellation.cancelled_by if cancellation else None,
            cancelled_why=cancellation.reason if cancellation else None,
            cancelled_at=cancellation.cancelled_at if cancellation else None,
        ))
    return records


def cancel_manual_punch(session, manual_punch_id: int, *, reason: str,
                        cancelled_by: str,
                        note: str | None = None) -> ManualPunchCancellation:
    """Void a correction with a row. **Never an edit and never a delete.**

    The `manual_punch` being cancelled is not touched: same time, same reason,
    same person, same stamp, and the database refuses an `UPDATE` that changes
    any of them (SPEC §3, §13). What changes is that the figures stop counting
    it — `attendance.build_day` leaves a cancelled punch out of first in, last
    out and the counts, and records how many it left out.

    **Only a manual punch can be cancelled.** A device punch is a fact from the
    hardware; there is no row here for one and nothing in this module touches
    `parsed_punch`. An id that is not a manual punch is refused by name rather
    than silently doing nothing.

    **It does not commit** — the caller does (CLAUDE.md).
    """
    if not reason or not reason.strip():
        raise ValueError(
            "a cancellation records why. It is a correction like any other and "
            "carries who made it and why (SPEC §3)"
        )
    if not cancelled_by or not cancelled_by.strip():
        raise ValueError("a cancellation records who made it")

    punch = session.get(ManualPunch, manual_punch_id)
    if punch is None:
        raise ValueError(
            f"{manual_punch_id!r} is not a manual punch. Only a correction "
            "somebody entered can be cancelled — a device punch is a fact from "
            "the hardware and nothing here touches it (SPEC §3)"
        )
    existing = session.scalars(
        select(ManualPunchCancellation)
        .where(ManualPunchCancellation.manual_punch_id == punch.id)
    ).first()
    if existing is not None:
        raise ValueError(
            f"manual_punch {punch.id} was already cancelled by "
            f"{existing.cancelled_by} at {existing.cancelled_at}. Cancelling "
            "twice is not two facts"
        )

    row = ManualPunchCancellation(
        manual_punch_id=punch.id,
        reason=reason.strip(),
        cancelled_by=cancelled_by.strip(),
        note=note,
    )
    session.add(row)
    session.flush()
    session.refresh(row)
    return row


def manual_punches_in(session, employee_id: int | None, start: dt.date,
                      end: dt.date) -> list[PunchRecord]:
    """Every correction over a period, so HR can find the one to cancel.

    **Manual punches only, by construction**: this reads `manual_punch` and
    nothing else, so there is no device punch for it to offer. Each row carries
    the id a cancellation names, the time, why, who entered it, and whether it
    has already been cancelled.
    """
    query = (
        select(ManualPunch, ManualPunchCancellation, Employee.employee_number)
        .outerjoin(ManualPunchCancellation,
                   ManualPunchCancellation.manual_punch_id == ManualPunch.id)
        .join(Employee, Employee.id == ManualPunch.employee_id)
        .where(
            ManualPunch.attendance_day >= start,
            ManualPunch.attendance_day <= end,
        )
        .order_by(ManualPunch.attendance_day, ManualPunch.recorded_at)
    )
    if employee_id is not None:
        query = query.where(ManualPunch.employee_id == employee_id)

    timezone = ZoneInfo(site_timezone(session))
    records = []
    for row, cancellation, number in session.execute(query).all():
        if row.path == GUARD:
            at = row.recorded_at.astimezone(timezone).replace(tzinfo=None)
            reason = session.get(CorrectionReason, row.reason_code)
            why = reason.label if reason else row.reason_code
            source = "guard entry"
        else:
            at = row.asserted_time
            why = row.reason
            source = "HR retroactive"
        records.append(PunchRecord(
            at=at,
            source=source,
            manual=True,
            attendance_day=row.attendance_day,
            who=row.made_by,
            why=why,
            recorded_at=row.recorded_at,
            evidence=f"manual_punch {row.id}, employee {number}",
            punch_id=row.id,
            cancelled=cancellation is not None,
            cancelled_by=cancellation.cancelled_by if cancellation else None,
            cancelled_why=cancellation.reason if cancellation else None,
            cancelled_at=cancellation.cancelled_at if cancellation else None,
        ))
    return records


def punches_for(session, employee_id: int, day: dt.date) -> list[PunchRecord]:
    """A day's punch detail for one employee: device punches and corrections
    together, each saying where it came from.

    This is a read, not a judgement. It does not decide first in, last out,
    lateness, presence or absence — no punch and an absence are not the same
    thing, and neither is decided here (SPEC §3).
    """
    records = device_punches_for(session, employee_id, day)
    records += manual_punches_for(session, employee_id, day)
    return sorted(records, key=lambda r: (r.at is None, r.at))


def correction_counts(session, start: dt.date, end: dt.date,
                      employee_id: int | None = None):
    """Manual punches per employee per period, split by path.

    A rising count for one employee is the signal SPEC §3 asks for: a bad
    enrollment, or a process being worked around.
    """
    query = (
        select(
            Employee.employee_number,
            ManualPunch.path,
            func.count().label("entries"),
            # **A cancelled correction is still counted.** The act happened,
            # and the signal §3 asks for is how often somebody had to make one
            # — not how many survived. It is reported separately so the two
            # facts stay apart.
            func.count(ManualPunchCancellation.id).label("cancelled"),
            func.min(ManualPunch.attendance_day).label("first_day"),
            func.max(ManualPunch.attendance_day).label("last_day"),
        )
        .join(Employee, Employee.id == ManualPunch.employee_id)
        .outerjoin(ManualPunchCancellation,
                   ManualPunchCancellation.manual_punch_id == ManualPunch.id)
        .where(
            ManualPunch.attendance_day >= start,
            ManualPunch.attendance_day <= end,
        )
        .group_by(Employee.employee_number, ManualPunch.path)
        .order_by(func.count().desc(), Employee.employee_number)
    )
    if employee_id is not None:
        query = query.where(ManualPunch.employee_id == employee_id)
    return session.execute(query).all()


def rebuild_attendance_days(session) -> int:
    """Recompute which attendance day each correction belongs to.

    The day is derived from the schedule in force, so a corrected schedule means
    these are rebuilt — the same reflex as replaying the parser rather than
    re-collecting from the device.
    """
    changed = 0
    for row in session.scalars(select(ManualPunch)).all():
        moment = row.asserted_time
        if moment is None:
            moment = row.recorded_at.astimezone(
                ZoneInfo(site_timezone(session))
            ).replace(tzinfo=None)
        day, schedule_id = attendance_day_of(session, row.employee_id, moment)
        if (day, schedule_id) != (row.attendance_day, row.schedule_id):
            row.attendance_day = day
            row.schedule_id = schedule_id
            changed += 1
    session.flush()
    return changed
