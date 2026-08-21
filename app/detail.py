"""One employee, one period, every day of it — what Accounts reads instead of
the punch card (SPEC §7).

**Not a second sheet.** The sheet is a grid of one mark per day; this is the
day itself, and the two answer different questions. The sheet says a day was
late; this says by how long, against which schedule, from which punch, and who
entered it if a person did. Nothing here decides anything the daily row has not
already decided — it reads `daily_attendance`, the punch detail behind it, and
the leave records covering it.

**One render, two outputs**, the same reflex as the sheet: `render_detail`
builds the object, `to_text` draws it for a terminal and `to_json` for the
browser. A figure that differs between them would be a figure one of them
computed, and neither computes anything.

Two things this view says out loud, because a reader would otherwise have to
know to ask:

  * **A leave record's day count is what the form says, never what the range
    implies** (SPEC §6). Where the two differ, both numbers show, side by side.
    A three-day range carrying 2.5 days is not an error to reconcile — it is a
    half day, and hiding either number would make it look like one.
  * **A lateness figure measured against a provisional schedule is marked.**
    The punch time is real; whether it was late is arithmetic on a schedule row
    HR has never confirmed (SPEC §9 A31).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from app.attendance import days_for
from app.corrections import punches_for
from app.hr_entry import leave_by_day, leave_records, leave_types
from app.models import Employee
from app.schedule import effective_holiday


@dataclass
class PunchLine:
    """One punch behind a day, exactly as it was recorded.

    `counted` is false for a re-pushed copy and for a cancelled correction, and
    both are still listed. **A punch that disappears from view is
    indistinguishable from one that never happened** (SPEC §3), so nothing here
    drops a line — it says why the line does not count.
    """

    at: dt.datetime | None
    source: str
    manual: bool
    who: str | None
    why: str | None
    counted: bool
    evidence: str
    cancelled: bool = False
    cancelled_by: str | None = None
    cancelled_why: str | None = None


@dataclass
class DetailDay:
    date: dt.date
    weekday: str
    has_row: bool
    first_in: dt.datetime | None = None
    first_in_manual: bool = False
    last_out: dt.datetime | None = None
    last_out_manual: bool = False
    late_minutes: int | None = None
    provisional: bool = False
    scheduled_start: dt.datetime | None = None
    punch_count: int = 0
    manual_punch_count: int = 0
    duplicate_pushes: int = 0
    cancelled_punch_count: int = 0
    status_code: str = ""
    is_rest_day: bool = False
    holiday_name: str | None = None
    holiday_closes: bool | None = None
    leave_code: str | None = None
    leave_type_label: str | None = None
    leave_record_id: int | None = None
    punches: list[PunchLine] = field(default_factory=list)


@dataclass
class LeaveLine:
    """A leave record overlapping the period, and what it says about itself."""

    record_id: int
    type_code: str | None
    type_label: str | None
    sheet_code: str | None
    period_from: dt.date
    period_to: dt.date
    days_stated: Decimal
    days_spanned: int
    entered_by: str
    reason: str | None

    @property
    def counts_differ(self) -> bool:
        """Does the form's count differ from the number of days it covers?

        Not a discrepancy. A half day, a rest day inside the range, or a
        Saturday the factory closes for all produce this, and the form's number
        is the one that is true (SPEC §6).
        """
        return Decimal(self.days_spanned) != self.days_stated


@dataclass
class Detail:
    employee_number: str
    name: str
    section_code: str
    role_code: str
    group_code: str
    period_start: dt.date
    period_end: dt.date
    days: list[DetailDay]
    leave: list[LeaveLine]
    provisional_days: int = 0
    manual_days: int = 0


def _assignment(session, employee: Employee, on_date: dt.date):
    from sqlalchemy import select

    from app.models import EmployeeAssignment

    return session.scalars(
        select(EmployeeAssignment)
        .where(
            EmployeeAssignment.employee_id == employee.id,
            EmployeeAssignment.effective_from <= on_date,
            (EmployeeAssignment.effective_to.is_(None))
            | (EmployeeAssignment.effective_to >= on_date),
        )
        .order_by(EmployeeAssignment.effective_from.desc())
        .limit(1)
    ).first()


def render_detail(session, employee: Employee, start: dt.date,
                  end: dt.date, with_punches: bool = True) -> Detail:
    """Build one employee's period. Both outputs draw this."""
    if end < start:
        raise ValueError(f"{end} is before {start}")

    rows = {row.attendance_day: row
            for row in days_for(session, employee.id, start, end)}
    leave_days = leave_by_day(session, start, end)
    labels = {row.code: row.label for row in leave_types(session)}

    days: list[DetailDay] = []
    day = start
    while day <= end:
        row = rows.get(day)
        holiday = effective_holiday(session, day)
        leave = leave_days.get((employee.id, day))
        detail_day = DetailDay(
            date=day,
            weekday=day.strftime("%a"),
            has_row=row is not None,
            holiday_name=holiday.name if holiday else None,
            holiday_closes=holiday.closes if holiday else None,
            leave_code=leave.sheet_code if leave else None,
            leave_type_label=leave.type_label if leave else None,
            leave_record_id=leave.record_id if leave else None,
        )
        if row is not None:
            detail_day.first_in = row.first_in
            detail_day.first_in_manual = row.first_in_manual
            detail_day.last_out = row.last_out
            detail_day.last_out_manual = row.last_out_manual
            detail_day.late_minutes = row.late_minutes
            detail_day.provisional = row.schedule_provisional
            detail_day.scheduled_start = row.scheduled_start
            detail_day.punch_count = row.punch_count
            detail_day.manual_punch_count = row.manual_punch_count
            detail_day.duplicate_pushes = row.duplicate_pushes
            detail_day.cancelled_punch_count = row.cancelled_punch_count
            detail_day.status_code = row.status_code
            detail_day.is_rest_day = row.is_rest_day

        # **`cancelled_punch_count` is in this condition on purpose.** A day
        # whose only punch was cancelled has a punch count of zero, and without
        # it the cancelled punch would vanish from the one screen that exists
        # to show it.
        if with_punches and row is not None and (
                row.punch_count or row.cancelled_punch_count):
            seen: set = set()
            for record in punches_for(session, employee.id, day):
                # A re-pushed punch is kept and shown, and says it was not
                # counted. The raw layer never loses a copy and neither does
                # this view (SPEC §12, A37).
                counted = not record.cancelled
                if not record.manual:
                    if record.at in seen:
                        counted = False
                    else:
                        seen.add(record.at)
                detail_day.punches.append(PunchLine(
                    at=record.at, source=record.source, manual=record.manual,
                    who=record.who, why=record.why, counted=counted,
                    evidence=record.evidence,
                    cancelled=record.cancelled,
                    cancelled_by=record.cancelled_by,
                    cancelled_why=record.cancelled_why,
                ))

        days.append(detail_day)
        day += dt.timedelta(days=1)

    leave_lines = [
        LeaveLine(
            record_id=row.id,
            type_code=row.leave_type_code,
            type_label=labels.get(row.leave_type_code),
            sheet_code=row.sheet_code,
            period_from=row.period_from,
            period_to=row.period_to,
            days_stated=row.days,
            days_spanned=(row.period_to - row.period_from).days + 1,
            entered_by=row.entered_by,
            reason=row.reason,
        )
        for row in leave_records(session, employee.id, start, end)
    ]

    assignment = _assignment(session, employee, end)
    return Detail(
        employee_number=employee.employee_number,
        name=assignment.name if assignment else "",
        section_code=assignment.section_code if assignment else "",
        role_code=assignment.role_code if assignment else "",
        group_code=assignment.group_code if assignment else "",
        period_start=start,
        period_end=end,
        days=days,
        leave=leave_lines,
        provisional_days=sum(1 for d in days
                             if d.provisional and d.late_minutes is not None),
        manual_days=sum(1 for d in days if d.manual_punch_count),
    )


# ---- the terminal --------------------------------------------------------


def to_text(detail: Detail, with_punches: bool = False) -> str:
    out: list[str] = []
    out.append(f"{detail.employee_number}   {detail.name}   "
               f"{detail.section_code} / {detail.role_code} / "
               f"{detail.group_code}")
    out.append(f"{detail.period_start} → {detail.period_end}")
    out.append("per-day punch detail — what Accounts reads instead of the "
               "punch card (SPEC §7)")
    out.append("")
    out.append(f"{'date':<12}{'day':<5}{'first in':<10}{'last out':<10}"
               f"{'late':>6}  {'leave':<7}punches")

    for day in detail.days:
        first = day.first_in.strftime("%H:%M") if day.first_in else ""
        if day.first_in_manual:
            first += "*"
        last = day.last_out.strftime("%H:%M") if day.last_out else ""
        if day.last_out_manual:
            last += "*"
        late = "" if day.late_minutes is None else str(day.late_minutes)
        if day.provisional and late:
            late += "p"
        leave = day.leave_code or ("—" if day.leave_record_id else "-")
        marks = []
        if day.is_rest_day:
            marks.append("rest day")
        if day.holiday_name:
            marks.append(day.holiday_name
                         + ("" if day.holiday_closes else " (worked)"))
        if day.leave_record_id and not day.leave_code:
            marks.append(f"leave {day.leave_type_label or ''}".strip()
                         + " — the record carries no sheet code")
        out.append(f"{str(day.date):<12}{day.weekday:<5}{first:<10}{last:<10}"
                   f"{late:>6}  {leave:<7}"
                   + (f"{day.punch_count}" if day.has_row else "-")
                   + (f"   {'; '.join(marks)}" if marks else ""))
        if with_punches:
            for punch in day.punches:
                if punch.cancelled:
                    counted = "CANCELLED"
                elif punch.counted:
                    counted = "counted"
                else:
                    counted = "copy, not counted"
                who = f"  {punch.who}" if punch.who else ""
                out.append(f"            {punch.at}  {punch.source:<15}"
                           f"{counted:<18}{who}")
                if punch.cancelled:
                    # Said in full, because "CANCELLED" alone leaves the reader
                    # to guess who decided and why (SPEC §3).
                    out.append(f"            {'':20}  cancelled by "
                               f"{punch.cancelled_by} — {punch.cancelled_why}. "
                               "The punch row is unchanged")

    if detail.leave:
        out.append("")
        out.append("leave records covering this period:")
        for line in detail.leave:
            row = (f"  {line.record_id:>4}  {line.period_from} → "
                   f"{line.period_to}  {line.days_stated} day(s) as the form "
                   f"states")
            if line.counts_differ:
                row += (f", over a {line.days_spanned}-day range — the form's "
                        "number is the one that counts (SPEC §6)")
            out.append(row + f"   {line.type_label or line.type_code or '-'}"
                             f"  [{line.sheet_code or 'no sheet code'}]")

    out.append("")
    out.append("* entered by a person, not the device (SPEC §3)")
    if detail.provisional_days:
        out.append(f"p late minutes measured against a provisional schedule "
                   f"row — {detail.provisional_days} day(s) here")
    out.append("a blank is no punch, which is a fact and never an absence "
               "(SPEC §3)")
    return "\n".join(out)


# ---- the browser ---------------------------------------------------------


def to_json(detail: Detail) -> dict:
    """The same object, as plain data. Reads fields, formats dates, nothing else."""
    return {
        "employee_number": detail.employee_number,
        "name": detail.name,
        "section_code": detail.section_code,
        "role_code": detail.role_code,
        "group_code": detail.group_code,
        "period_start": detail.period_start.isoformat(),
        "period_end": detail.period_end.isoformat(),
        "provisional_days": detail.provisional_days,
        "manual_days": detail.manual_days,
        "days": [
            {
                "date": day.date.isoformat(),
                "weekday": day.weekday,
                "has_row": day.has_row,
                "first_in": day.first_in.strftime("%H:%M") if day.first_in else None,
                "first_in_manual": day.first_in_manual,
                "last_out": day.last_out.strftime("%H:%M") if day.last_out else None,
                "last_out_manual": day.last_out_manual,
                "late_minutes": day.late_minutes,
                "provisional": day.provisional,
                "punch_count": day.punch_count,
                "manual_punch_count": day.manual_punch_count,
                "duplicate_pushes": day.duplicate_pushes,
                "cancelled_punch_count": day.cancelled_punch_count,
                "status_code": day.status_code,
                "is_rest_day": day.is_rest_day,
                "holiday_name": day.holiday_name,
                "holiday_closes": day.holiday_closes,
                "leave_code": day.leave_code,
                "leave_type_label": day.leave_type_label,
                "leave_record_id": day.leave_record_id,
                "punches": [
                    {
                        "at": punch.at.isoformat(sep=" ") if punch.at else None,
                        "source": punch.source,
                        "manual": punch.manual,
                        "who": punch.who,
                        "why": punch.why,
                        "counted": punch.counted,
                        "evidence": punch.evidence,
                        "cancelled": punch.cancelled,
                        "cancelled_by": punch.cancelled_by,
                        "cancelled_why": punch.cancelled_why,
                    }
                    for punch in day.punches
                ],
            }
            for day in detail.days
        ],
        "leave": [
            {
                "record_id": line.record_id,
                "type_code": line.type_code,
                "type_label": line.type_label,
                "sheet_code": line.sheet_code,
                "period_from": line.period_from.isoformat(),
                "period_to": line.period_to.isoformat(),
                "days_stated": str(line.days_stated),
                "days_spanned": line.days_spanned,
                "counts_differ": line.counts_differ,
                "entered_by": line.entered_by,
                "reason": line.reason,
            }
            for line in detail.leave
        ],
    }
