"""Layer 3: one row per employee per day, built from what is already recorded.

SPEC §3 sets the shape — first in, last out, late minutes, status — and says
every period total is a query over these rows. So this module builds rows and
computes nothing above them: no half-month total, no summary, no deduction.

What it does not decide, and why:

  * **Absence.** No punch is a fact and a status; absent is an HR judgement and
    needs leave, which is step 5. Nothing here writes one (SPEC §3, §13).
  * **A deduction.** Late minutes are a figure. The threshold and the decision
    are §5's and management's — a figure on screen is not a deduction.
  * **The attendance day.** That is `schedule.attendance_day_for`, through the
    schedule in force. A night-shift punch at 04:35 belongs to the previous
    day, and this module never re-derives that by comparing two times.
  * **Which employee a PIN is.** `corrections.punches_for` resolves it as of
    the punch's own date (A33), downstream of capture, as §13 requires.

Rebuilding is the normal case, not the exception. A schedule corrected today
moves yesterday's figures, and the way that reaches them is a rebuild — the
same reflex as replaying the parser instead of re-collecting from the device.
Two rebuilds from the same punches produce the same row.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import delete, select

from app.corrections import group_for, punches_for
from app.models import DailyAttendance, EmploymentPeriod
from app.schedule import day_context

# Status rows live in attendance_status. These are the codes seeded there.
PUNCHES_RECORDED = "punches_recorded"
ONE_PUNCH = "one_punch"
NO_PUNCH = "no_punch"


@dataclass
class BuiltDay:
    """What a build produced for one employee on one day, before it is written.

    Kept separate from the row so the gate can compare two builds without
    reading `built_at`, which is the only thing about a row that is allowed to
    differ between them.
    """

    employee_id: int
    attendance_day: dt.date
    values: dict

    @property
    def status_code(self) -> str:
        return self.values["status_code"]


def status_for(punch_count: int) -> str:
    """The punch fact, and nothing more.

    There is no branch here that could produce `absent`. Adding one would mean
    adding a row to attendance_status first, and §13 forbids the collapse that
    row would represent.
    """
    if punch_count >= 2:
        return PUNCHES_RECORDED
    if punch_count == 1:
        return ONE_PUNCH
    return NO_PUNCH


def late_minutes_for(scheduled_start: dt.datetime | None, grace_minutes: int | None,
                     first_in: dt.datetime | None) -> int | None:
    """Whole minutes late, or None when there is nothing to measure (A36).

    Measured from the scheduled start plus the grace period, both from the
    schedule row that was in force on that attendance day. None is not zero: a
    rest day has no scheduled start, and a day with no punch has no arrival —
    neither is an employee who arrived on time.
    """
    if scheduled_start is None or first_in is None:
        return None
    grace = grace_minutes or 0
    allowed = scheduled_start + dt.timedelta(minutes=grace)
    if first_in <= allowed:
        return 0
    # Floored to whole minutes: a punch at 08:00:59 against an 08:00:00 start
    # is not yet a minute late (A36).
    return int((first_in - allowed).total_seconds() // 60)


def drop_repushes(records: list) -> tuple[list, int]:
    """Device punches at the same instant are one punch (A37).

    Returns the punches to count, and how many pushes were dropped. The dropped
    ones are not deleted anywhere — the raw layer and the parsed layer both keep
    every copy, and this is only about what a day's figures are built from.
    """
    kept = []
    seen: set[dt.datetime] = set()
    dropped = 0
    for record in records:
        if not record.manual:
            if record.at in seen:
                dropped += 1
                continue
            seen.add(record.at)
        kept.append(record)
    return kept, dropped


def employees_on(session, day: dt.date) -> list[int]:
    """Employees employed on that date. Active and left are dates, so this is a
    range test rather than a flag (SPEC §2)."""
    return list(session.scalars(
        select(EmploymentPeriod.employee_id)
        .where(
            EmploymentPeriod.active_from <= day,
            (EmploymentPeriod.left_on.is_(None)) | (EmploymentPeriod.left_on >= day),
        )
        .order_by(EmploymentPeriod.employee_id)
    ))


def build_day(session, employee_id: int, day: dt.date) -> BuiltDay:
    """One employee, one day, from punches, corrections and the schedule.

    Nothing is read from today: the group is the one in force on `day`, and the
    schedule is that group's row in force on `day`.
    """
    group_code = group_for(session, employee_id, day)
    context = day_context(session, group_code, day) if group_code else None
    schedule = context.schedule if context else None

    records = punches_for(session, employee_id, day)
    timed = [r for r in records if r.at is not None]
    timed.sort(key=lambda r: r.at)

    # A device re-pushes a batch after a timeout, and the raw layer keeps every
    # copy on purpose (SPEC §12). This is the layer that deduplicates: the same
    # employee at the same second is one punch, however many times it arrived
    # (A37). A manual punch is never deduplicated — each one is a separate act
    # by a named person, and collapsing two would hide one of them.
    timed, duplicate_pushes = drop_repushes(timed)

    first = timed[0] if timed else None
    # One punch is a first in and nothing else (A35). Calling it a last out as
    # well would assert a departure nobody recorded.
    last = timed[-1] if len(timed) >= 2 else None

    scheduled_start = context.shift_start if context else None
    scheduled_end = context.shift_end if context else None
    grace = schedule.grace_minutes if schedule else None

    # A rest day or a closed holiday has no scheduled start to be late against
    # (A36). The punch still counts and still shows.
    if context is not None and context.closed:
        scheduled_start = None
        scheduled_end = None
        grace = None

    late = late_minutes_for(scheduled_start, grace, first.at if first else None)

    notes = []
    if group_code is None:
        notes.append("no group in force on this day, so no schedule and no figures")
    elif schedule is None:
        notes.append(f"no schedule in force for {group_code} on this day")
    if len(timed) == 1:
        notes.append("one punch only: a first in, and no last out (SPEC §9 A35)")
    if duplicate_pushes:
        notes.append(
            f"{duplicate_pushes} device pushes were copies of a punch already "
            "counted — the device re-pushing, not somebody punching (A37)"
        )
    if context is not None and context.closed and timed:
        notes.append("punched on a day the factory was closed")
    manual = [r for r in timed if r.manual]
    if manual:
        notes.append(
            f"{len(manual)} of {len(timed)} punches were entered by a person"
        )

    values = {
        "employee_id": employee_id,
        "attendance_day": day,
        "group_code": group_code,
        "schedule_id": schedule.id if schedule else None,
        "schedule_provisional": bool(schedule.provisional) if schedule else False,
        "scheduled_start": scheduled_start,
        "scheduled_end": scheduled_end,
        "grace_minutes": grace,
        "is_rest_day": bool(context.is_rest_day) if context else False,
        "holiday_name": context.holiday.name if context and context.holiday else None,
        "holiday_closes": (
            context.holiday.closes if context and context.holiday else None
        ),
        "first_in": first.at if first else None,
        "first_in_source": first.source if first else None,
        "first_in_manual": bool(first.manual) if first else False,
        "last_out": last.at if last else None,
        "last_out_source": last.source if last else None,
        "last_out_manual": bool(last.manual) if last else False,
        "punch_count": len(timed),
        "duplicate_pushes": duplicate_pushes,
        "device_punch_count": sum(1 for r in timed if not r.manual),
        "manual_punch_count": sum(1 for r in timed if r.manual),
        "late_minutes": late,
        "status_code": status_for(len(timed)),
        "note": "; ".join(notes) or None,
    }
    return BuiltDay(employee_id=employee_id, attendance_day=day, values=values)


def build_days(session, start: dt.date, end: dt.date,
               employee_ids: list[int] | None = None) -> list[BuiltDay]:
    """Rebuild every day in the range, replacing whatever was there.

    Replacing rather than updating in place, because a rebuild has to be able to
    remove a figure as well as change one — a schedule correction can turn a
    late arrival into an on-time one, and an in-place update that only ever
    writes non-null values would leave the old figure standing.
    """
    if end < start:
        raise ValueError(f"{end} is before {start}")

    built: list[BuiltDay] = []
    day = start
    while day <= end:
        ids = employee_ids if employee_ids is not None else employees_on(session, day)
        for employee_id in ids:
            built.append(build_day(session, employee_id, day))
        day += dt.timedelta(days=1)

    if built:
        pairs = {(b.employee_id, b.attendance_day) for b in built}
        session.execute(
            delete(DailyAttendance).where(
                DailyAttendance.attendance_day >= start,
                DailyAttendance.attendance_day <= end,
                DailyAttendance.employee_id.in_({b.employee_id for b in built}),
            )
        )
        session.flush()
        for item in built:
            if (item.employee_id, item.attendance_day) in pairs:
                session.add(DailyAttendance(**item.values))
        session.flush()
    return built


def days_for(session, employee_id: int, start: dt.date, end: dt.date):
    return list(session.scalars(
        select(DailyAttendance)
        .where(
            DailyAttendance.employee_id == employee_id,
            DailyAttendance.attendance_day >= start,
            DailyAttendance.attendance_day <= end,
        )
        .order_by(DailyAttendance.attendance_day)
    ))


def rows_in(session, start: dt.date, end: dt.date):
    return list(session.scalars(
        select(DailyAttendance)
        .where(
            DailyAttendance.attendance_day >= start,
            DailyAttendance.attendance_day <= end,
        )
        .order_by(DailyAttendance.attendance_day, DailyAttendance.employee_id)
    ))
