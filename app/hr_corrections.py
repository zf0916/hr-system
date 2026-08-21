"""Step 10, piece 6: the HR corrections screen's service layer.

**Two acts on one screen**, both through the same functions the CLI calls:

  * `record` — HR adds a punch the device did not take. A typed time, a reason
    in words, and who entered it. `corrections.record_hr_retroactive`, which is
    what `hr corrections retroactive` calls.
  * `cancel` — a correction is voided **by writing a row**.
    `corrections.cancel_manual_punch`, which is what `hr corrections cancel`
    calls. The original punch is not touched, and the database refuses an
    `UPDATE` that changes anything a person recorded and a `DELETE` of any kind
    (SPEC §3, §13).

And one read, because HR has to find the punch before it can be cancelled:

  * `listing` — the corrections for an employee over a period, each with the id
    a cancellation names, the time, why, who entered it, and whether it has
    already been cancelled. **`corrections.manual_punches_in` reads
    `manual_punch` and nothing else**, so there is no device punch for it to
    offer.

**This is the HR path and it is not the guard path.** §3 keeps them apart on
purpose: a guard entry is server-stamped and has no field for a time, because a
guard who can type one can be asked to type a different one. This screen types
a time — that is the whole difference between the two, and it is why they are
two screens, two service functions, two payloads, and not one form with a
switch on it. **Nothing here reaches the guard's path**: `app.guard` is not
imported, `record_guard_entry` is not called, and the screen carries no way to
open `/guard`.

**Cancelling does not undo the day's figures by itself.** The daily rows are
rebuilt from punches, corrections and the schedule, so the day a cancellation
touches is rebuilt here — the same reflex as replaying the parser after a
parser change. Without it the cancelled punch would still be sitting in the
figure that was built before it.
"""

from __future__ import annotations

import datetime as dt

from app.attendance import build_days
from app.corrections import (
    cancel_manual_punch,
    employee_by_number,
    manual_punches_in,
    record_hr_retroactive,
)
from app.hr_entry import typist, typists
from app.models import ManualPunch

# What this screen is for, and what it is not. Stated here so the screen prints
# it rather than a caption somebody wrote once.
NOT_THE_GUARD_PATH = (
    "This is the HR path. HR types the time it is correcting, off whatever "
    "says what happened — a day the device was down, a punch somebody forgot. "
    "The guard's screen is a different act on a different device in a different "
    "place: it records that an employee is standing in front of the guard now, "
    "the server stamps the moment, and there is no field for a time at all. "
    "The two are not reachable from one another (SPEC §3)."
)

CANNOT_UNDO = (
    "A cancellation is itself on the record and cannot be undone here. It is a "
    "row, like the correction it voids: the punch keeps its time, its reason "
    "and its author, and nothing is edited or deleted (SPEC §3, §13)."
)


def _parse_date(value, field: str) -> dt.date:
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    try:
        return dt.datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"{field} is {value!r}, which is not a date. Dates "
                         "are YYYY-MM-DD") from None


def _parse_moment(value, field: str) -> dt.datetime:
    """`YYYY-MM-DD HH:MM`, or the `T` a browser's datetime box sends."""
    text = (value or "").strip().replace("T", " ")
    for shape in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(text, shape)
        except ValueError:
            continue
    raise ValueError(
        f"{field} is {value!r}, which is not a moment. A retroactive entry "
        "states the time it is correcting, as 'YYYY-MM-DD HH:MM' (SPEC §3)")


def screen(session) -> dict:
    """Everything the screen needs to draw itself, in one answer."""
    people = typists(session)
    return {
        "typists": [
            {"code": user.code, "name": user.name, "label": user.label,
             "provisional": user.provisional}
            for user in people
        ],
        "not_the_guard_path": NOT_THE_GUARD_PATH,
        "cannot_undo": CANNOT_UNDO,
    }


def _line(record) -> dict:
    return {
        "punch_id": record.punch_id,
        "attendance_day": record.attendance_day.isoformat(),
        "at": record.at.isoformat(sep=" ") if record.at else None,
        "source": record.source,
        "why": record.why,
        "who": record.who,
        "recorded_at": (record.recorded_at.isoformat(sep=" ", timespec="seconds")
                        if record.recorded_at else None),
        "cancelled": record.cancelled,
        "cancelled_by": record.cancelled_by,
        "cancelled_why": record.cancelled_why,
        "cancelled_at": (record.cancelled_at.isoformat(sep=" ",
                                                       timespec="seconds")
                         if record.cancelled_at else None),
        "evidence": record.evidence,
    }


def listing(session, employee_number: str, start, end) -> dict:
    """The corrections HR can choose from, and nothing else.

    **Manual punches only, by construction.** The read underneath this touches
    `manual_punch` and no other table, so a device punch cannot appear on it —
    there is no filter to get wrong and nothing to switch off. A device punch is
    a fact from the hardware (SPEC §3).
    """
    employee = employee_by_number(session, employee_number)
    start, end = _parse_date(start, "the start"), _parse_date(end, "the end")
    if end < start:
        raise ValueError(f"{end} is before {start}")
    records = manual_punches_in(session, employee.id, start, end)
    return {
        "employee_number": employee.employee_number,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "corrections": [_line(record) for record in records],
        "cancelled": sum(1 for record in records if record.cancelled),
        "only_manual": (
            "Only corrections appear here. A device punch is a fact from the "
            "hardware and nothing on this screen touches it (SPEC §3)."
        ),
    }


def record(session, *, entered_by: str, employee_number: str, at: str,
           reason: str) -> dict:
    """One retroactive entry, through the same function the CLI calls.

    **The time is a parameter here and there is no path from this function to
    the guard's**, which has none. It does not commit; the caller does.
    """
    who = typist(session, entered_by)
    employee = employee_by_number(session, employee_number)
    punch = record_hr_retroactive(
        session, employee,
        asserted_time=_parse_moment(at, "the time"),
        reason=(reason or "").strip(),
        made_by=who.name,
        note="entered on the HR corrections screen",
    )
    # The day is rebuilt so the figures include the punch that was just added.
    build_days(session, punch.attendance_day, punch.attendance_day,
               employee_ids=[employee.id])
    return {
        "punch_id": punch.id,
        "employee_number": employee.employee_number,
        "at": punch.asserted_time.isoformat(sep=" "),
        "attendance_day": punch.attendance_day.isoformat(),
        "reason": punch.reason,
        "made_by": punch.made_by,
        "recorded_at": punch.recorded_at.isoformat(sep=" ", timespec="seconds"),
        "path": punch.path,
        "marked": ("It is marked as entered by a person wherever it is read, "
                   "and counted per employee per period (SPEC §3)."),
    }


def cancel(session, *, cancelled_by: str, punch_id: int, reason: str) -> dict:
    """Void one correction by writing a row.

    **Nothing is edited and nothing is deleted.** The punch's every recorded
    column is untouched and the database refuses to let it be otherwise; this
    adds a row beside it. The day it belongs to is then rebuilt, so the figures
    stop counting it — a cancellation that left yesterday's total standing
    would be a cancellation in name only.

    It does not commit; the caller does.
    """
    who = typist(session, cancelled_by)
    row = cancel_manual_punch(
        session, punch_id, reason=(reason or "").strip(),
        cancelled_by=who.name, note="cancelled on the HR corrections screen")
    punch = session.get(ManualPunch, punch_id)
    build_days(session, punch.attendance_day, punch.attendance_day,
               employee_ids=[punch.employee_id])
    return {
        "cancellation_id": row.id,
        "punch_id": punch.id,
        "attendance_day": punch.attendance_day.isoformat(),
        "cancelled_by": row.cancelled_by,
        "reason": row.reason,
        "cancelled_at": row.cancelled_at.isoformat(sep=" ", timespec="seconds"),
        "punch_unchanged": (
            f"manual_punch {punch.id} is exactly as it was written: "
            f"{punch.path}, {punch.asserted_time or punch.recorded_at}, "
            f"entered by {punch.made_by}."
        ),
        "final": CANNOT_UNDO,
    }
