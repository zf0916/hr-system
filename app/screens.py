"""What the read-only screens read. Step 10, piece 2.

**Every function here is a projection of something that already exists.** The
employee list is a query; the sheet is `sheet.render` passed to `sheet.to_json`;
the download is `sheet.to_bytes`, the same bytes `hr sheet export` writes; the
per-day detail is `detail.render_detail` passed to `detail.to_json`. Nothing in
this module decides what a cell says, what a day was, or how many days of leave
somebody took.

**That is the point, and it is structural rather than a matter of discipline.**
The HTTP layer imports this module and nothing else of the application, so
there is no ingredient on that side of the wall to compute a second answer
from. If the screen and the Excel file could disagree, one of them would have
had to work it out for itself, and neither is given the chance (SPEC §7, §13).

Read-only, deliberately: piece 2 has no route that writes. Entry is pieces 3 to
6, and each arrives with the record it writes.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select

from app import detail as detail_view
from app import sheet as sheet_view
from app.corrections import employee_by_number
from app.models import (
    DeviceUserMap,
    Employee,
    EmployeeAssignment,
    EmploymentPeriod,
)


def _today(session) -> dt.date:
    from app.corrections import local_now

    local, _ = local_now(session)
    return local.date()


def as_date(value) -> dt.date | None:
    """A `YYYY-MM-DD` string or a date, into a date. Parsing lives here rather
    than in the HTTP layer, so that layer has nothing to get wrong."""
    if value is None or isinstance(value, dt.date):
        return value
    try:
        return dt.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"{value!r} is not a date. Dates are YYYY-MM-DD") from None


def roster(session, on_date=None, section: str | None = None) -> dict:
    """Everybody the assignment rows cover on a date, and what they were then.

    **The date matters.** An assignment is effective-dated, so this is not "the
    employees" but "who was where on that day" — the same reason a sheet for
    last March reads March's rows (SPEC §2). Somebody who left in June is
    absent from an August roster and present in a May one, without anything
    being edited.

    Each row says whether a PIN is mapped to that employee. An employee with no
    mapping cannot produce a punch that reaches their name, however well the
    device is working, and that is worth seeing next to the name rather than
    discovering from an empty sheet row.
    """
    on_date = as_date(on_date) or _today(session)

    rows = session.execute(
        select(EmployeeAssignment, Employee.employee_number)
        .join(Employee, Employee.id == EmployeeAssignment.employee_id)
        .where(
            EmployeeAssignment.effective_from <= on_date,
            (EmployeeAssignment.effective_to.is_(None))
            | (EmployeeAssignment.effective_to >= on_date),
        )
        .order_by(EmployeeAssignment.section_code, Employee.employee_number)
    ).all()
    if section:
        rows = [row for row in rows if row[0].section_code == section]

    pins: dict[int, list[str]] = {}
    for employee_id, pin in session.execute(
        select(DeviceUserMap.employee_id, DeviceUserMap.pin)
        .where(
            DeviceUserMap.effective_from <= on_date,
            (DeviceUserMap.effective_to.is_(None))
            | (DeviceUserMap.effective_to >= on_date),
        )
        .order_by(DeviceUserMap.pin)
    ).all():
        pins.setdefault(employee_id, []).append(pin)

    employment = {
        employee_id: (active_from, left_on)
        for employee_id, active_from, left_on in session.execute(
            select(EmploymentPeriod.employee_id, EmploymentPeriod.active_from,
                   EmploymentPeriod.left_on)
            .where(
                EmploymentPeriod.active_from <= on_date,
                (EmploymentPeriod.left_on.is_(None))
                | (EmploymentPeriod.left_on >= on_date),
            )
        ).all()
    }

    people = []
    for assignment, number in rows:
        active_from, left_on = employment.get(assignment.employee_id, (None, None))
        people.append({
            "employee_id": assignment.employee_id,
            "employee_number": number,
            "name": assignment.name,
            "section_code": assignment.section_code,
            "role_code": assignment.role_code,
            "group_code": assignment.group_code,
            "pins": pins.get(assignment.employee_id, []),
            "enrolled": bool(pins.get(assignment.employee_id)),
            "active_from": active_from.isoformat() if active_from else None,
            "left_on": left_on.isoformat() if left_on else None,
        })

    total_employees = session.scalar(select(func.count()).select_from(Employee))
    return {
        "on_date": on_date.isoformat(),
        "section": section,
        "headcount": len(people),
        "employees_on_file": total_employees,
        "not_enrolled": sum(1 for person in people if not person["enrolled"]),
        "sections": sorted({person["section_code"] for person in people}),
        "people": people,
    }


def sheet_screen(session, month: str | None = None, start=None, end=None,
                 section: str | None = None) -> dict:
    """The sheet, for the browser. `render` decides everything; this hands it on."""
    period_start, period_end = sheet_view.resolve_period(session, month, start, end)
    sheet = sheet_view.render(session, period_start, period_end,
                              section_code=section)
    payload = sheet_view.to_json(sheet)
    payload["download"] = download_name(month, period_start, period_end, section)
    payload["section"] = section
    return payload


def download_name(month: str | None, start: dt.date, end: dt.date,
                  section: str | None) -> str:
    """What the downloaded file is called. The period is in the name, because a
    filed record with no period on it is a record somebody has to open to
    identify (SPEC §7)."""
    stem = f"attendance_{month}" if month else f"attendance_{start}_{end}"
    if section:
        stem += f"_{section}"
    return f"{stem}.xlsx"


def sheet_file(session, month: str | None = None, start=None, end=None,
               section: str | None = None) -> tuple[str, bytes]:
    """The Excel file, as a name and bytes.

    **The same function `hr sheet export` calls.** Not the same layout, not the
    same code path — the same bytes. A download that differed from the exported
    file by so much as a byte would mean two renders existed, which is the one
    thing §7 forbids about this sheet.
    """
    period_start, period_end = sheet_view.resolve_period(session, month, start, end)
    sheet = sheet_view.render(session, period_start, period_end,
                              section_code=section)
    return (download_name(month, period_start, period_end, section),
            sheet_view.to_bytes(sheet))


def day_detail(session, employee_number: str, month: str | None = None,
               start=None, end=None, with_punches: bool = True) -> dict:
    """One employee, one period, every day of it."""
    period_start, period_end = sheet_view.resolve_period(session, month, start, end)
    employee = employee_by_number(session, employee_number)
    built = detail_view.render_detail(session, employee, period_start,
                                      period_end, with_punches=with_punches)
    return detail_view.to_json(built)
