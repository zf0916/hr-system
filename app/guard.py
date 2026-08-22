"""Step 10, piece 3: the guard screen's service layer.

**One screen, and it does one thing** (SPEC §3): the guard records that an
employee is standing in front of him, because the device would not read them.

Three functions, and the third is the only one that writes:

  * `screen` — who may be on duty, and the two reasons. Both are rows.
  * `look_up` — the employee's name, for the guard to read back before he
    confirms anything. **This is the whole safeguard.** Nothing else stands
    between a mistyped number and a punch on the wrong person's day, so it
    happens before the entry exists rather than after.
  * `record` — the entry, through `corrections.record_guard_entry`, which is
    the same function `hr corrections guard` calls.

**There is no time anywhere in this module.** Not a parameter, not a default,
not a field on the way in. `record_guard_entry` takes no time and the database
refuses a guard row that carries one (`manual_punch_guard_cannot_state_a_time`).
A guard who could type a time is a guard who can be asked to type a different
one, and the whole reason this path exists is that it cannot be (SPEC §3, §13).

**Nothing here can undo an entry**, and no function is provided that could. A
confirmed entry is on the record; HR corrects mistakes. What should replace a
wrong one is parked, and it belongs to piece 6 — inventing an answer here would
settle it by accident.

**The entry reaches the sheet before the guard has put the phone down.** The
daily rows are built from punches, corrections and the schedule, so a
correction that does not rebuild its day is a correction the figures have not
heard of: the row went on saying three punches while four existed, and the day
detail listed all four under a count of three. `hr_corrections` has rebuilt the
day it touched since piece 6; this is the same reflex on the other correction
path, and the two paths behaving differently was the whole defect.
"""

from __future__ import annotations

from sqlalchemy import select

from app.attendance import build_days
from app.corrections import GUARD, employee_by_number, record_guard_entry
from app.models import CorrectionReason, EmployeeAssignment, ScreenUser


def on_duty(session) -> list[ScreenUser]:
    return list(session.scalars(
        select(ScreenUser)
        .where(ScreenUser.screen == GUARD, ScreenUser.active.is_(True))
        .order_by(ScreenUser.sort_order, ScreenUser.code)
    ))


def reasons(session) -> list[CorrectionReason]:
    """The reasons a guard entry may give. **Rows, not a list in the code** —
    a third reason is added by an INSERT, and until somebody does that, a third
    reason is refused (SPEC §3)."""
    return list(session.scalars(
        select(CorrectionReason)
        .where(CorrectionReason.path == GUARD)
        .order_by(CorrectionReason.code)
    ))


def _guard(session, code: str) -> ScreenUser:
    row = session.get(ScreenUser, (code or "").strip())
    if row is None or row.screen != GUARD or not row.active:
        allowed = [user.code for user in on_duty(session)]
        raise ValueError(
            f"{code!r} is not a guard on duty. Every entry says who made it "
            f"(SPEC §3). On the list: {allowed}"
        )
    return row


def _assignment_name(session, employee_id: int) -> str:
    from app.corrections import local_now

    _, local = local_now(session)
    row = session.scalars(
        select(EmployeeAssignment)
        .where(
            EmployeeAssignment.employee_id == employee_id,
            EmployeeAssignment.effective_from <= local.date(),
            (EmployeeAssignment.effective_to.is_(None))
            | (EmployeeAssignment.effective_to >= local.date()),
        )
        .order_by(EmployeeAssignment.effective_from.desc())
        .limit(1)
    ).first()
    return row.name if row else ""


def screen(session) -> dict:
    """Everything the screen needs to draw itself, in one answer."""
    guards = on_duty(session)
    return {
        "guards": [
            {
                "code": user.code,
                "name": user.name,
                "label": user.label,
                "provisional": user.provisional,
            }
            for user in guards
        ],
        "guards_provisional": any(user.provisional for user in guards),
        "reasons": [
            {"code": reason.code, "label": reason.label}
            for reason in reasons(session)
        ],
    }


def look_up(session, employee_number: str) -> dict:
    """The name behind a number, for the guard to read back.

    An unknown number is refused here, before anything is written — the entry
    is made from this same number a moment later, and a number that resolves to
    nobody must never get as far as a row.
    """
    employee = employee_by_number(session, employee_number)
    return {
        "employee_number": employee.employee_number,
        "name": _assignment_name(session, employee.id),
    }


def record(session, *, guard_code: str, employee_number: str,
           reason_code: str) -> dict:
    """Record the entry. **The three things the guard chose, and nothing else.**

    The signature is the payload: a guard, an employee and a reason. There is
    no fourth parameter to smuggle a moment through, and adding one would mean
    changing this line, `record_guard_entry`, and the check constraint behind
    it (SPEC §3).

    **It does not commit.** Committing here would make the entry unrollbackable
    by its own caller, which is how a gate that says it rolls back leaves a
    punch behind on every run. The caller commits — the route does, the CLI
    does, and a gate does not. `build_days` flushes and does not commit either,
    so the rebuild is inside whatever transaction the caller is holding.
    """
    guard = _guard(session, guard_code)
    employee = employee_by_number(session, employee_number)
    name = _assignment_name(session, employee.id)
    punch = record_guard_entry(
        session, employee, reason_code=reason_code, made_by=guard.name,
        note="entered on the guard screen",
    )
    # The day is rebuilt so the figures include the punch just recorded. One
    # employee, one day — the guard is standing at the door, not waiting on a
    # month.
    build_days(session, punch.attendance_day, punch.attendance_day,
               employee_ids=[employee.id])

    reason = session.get(CorrectionReason, punch.reason_code)
    return {
        "id": punch.id,
        "employee_number": employee.employee_number,
        "name": name,
        "reason_code": punch.reason_code,
        "reason_label": reason.label if reason else punch.reason_code,
        "made_by": punch.made_by,
        # Server-stamped, on the way out. The screen never sent a time and
        # could not have; this is what the server wrote down.
        "recorded_at": punch.recorded_at.isoformat(sep=" ", timespec="seconds"),
        "attendance_day": punch.attendance_day.isoformat(),
        "final": ("This entry is on the record and cannot be undone here. "
                  "HR corrects mistakes."),
    }
