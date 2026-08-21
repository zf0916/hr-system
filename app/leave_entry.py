"""Step 10, piece 4: the leave entry screen's service layer.

**HR types a form that has already been signed on paper** (SPEC §6). This
module is the face the screen puts on `hr_entry.record_leave` — the same
function `hr leave add` calls — and it computes nothing that ends up on the
row.

Four functions, and only the last one writes:

  * `screen` — who is at the keyboard (`hr_entry.typists`, shared with the gate
    pass screen), the seven ticks on the form, the nine legend codes, and the
    order the paper puts its fields in. All rows except the order, which is
    §6's own layout.
  * `look_up` — the name, the staff number and the department behind an
    employee number, so the person typing can read them against the paper.
  * `range_check` — **the one place the range is counted**, and it is counted
    to be *shown beside* the typed figure, never to become it.
  * `record` — one line of leave, through `hr_entry.record_leave`.

**The number of days is typed and is never derived.** `record` has no path to
`range_check`: the two do not call each other, and the count the screen sends
is the count the row keeps. Where the two numbers disagree the screen shows
both and says which one counts, which is what §6 asks for and what the day
detail screen already says in those words.

**The applied-for type and the sheet code stay two fields.** `screen` carries
each type's `suggested_sheet_code` so the entry screen can offer it before HR
touches it (A48) — three of seven have one, four have none — and nothing here
copies one field into the other. Whatever arrives is what is recorded.

**The SQL Account code is not on this screen and is not a parameter.** It stays
empty until Accounts answers what the payroll codes mean (SPEC §8).

**Nothing here approves, entitles or balances anything.** There is no parameter
for a signature, an entitlement or a balance; the paper was signed before it
reached HR, and routing an approval is Milestone 5 (SPEC §6).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from app.corrections import employee_by_number
from app.hr_entry import (
    HR,
    leave_codes,
    leave_types,
    record_leave,
    typist as _typist,
    typists,
)
from app.models import EmployeeAssignment

# **§6's own field order**, so a person reads down the page and down the paper
# together. Not an assumed value and not a preference: it is the order the
# printed form puts its fields in, and the screen is checked against it.
#
# The Reason line is deliberately not in this list. On the paper it is not a
# field of its own — it hangs off the Unpaid Leave tick, which is the one tick
# that carries a reason — so the screen shows it inside that field rather than
# as a ninth one.
FORM_ORDER = [
    ("name", "Name of applicant"),
    ("staff_no", "Staff no."),
    ("department", "Department"),
    ("date_of_application", "Date"),
    ("nature_of_leave", "Nature of leave"),
    ("period_from", "Period from"),
    ("period_to", "to"),
    ("days", "No. of days"),
]

# What the paper carries and this screen does not, said rather than dropped —
# the same reflex as the sheet screen listing what only the file can print.
NOT_ON_THIS_SCREEN = [
    "The five signature boxes — applicant, immediate supervisor, Head of Dept, "
    "Human Resource Dept and Operation Manager. The paper was signed before it "
    "reached HR; recording who signed is Milestone 5 (SPEC §6).",
    "The SQL Account code. It is carried on the record from the start and "
    "stays empty until Accounts answers what the payroll codes mean (SPEC §8).",
    "Entitlement, balance and approval. None of them is designed, none of them "
    "is on this screen, and nothing here checks one (SPEC §6).",
]


def _parse_date(value, field: str) -> dt.date:
    if isinstance(value, dt.date):
        return value
    try:
        return dt.datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"{field} is {value!r}, which is not a date. Dates "
                         "are YYYY-MM-DD") from None


def _assignment(session, employee_id: int, on: dt.date):
    return session.scalars(
        select(EmployeeAssignment)
        .where(
            EmployeeAssignment.employee_id == employee_id,
            EmployeeAssignment.effective_from <= on,
            (EmployeeAssignment.effective_to.is_(None))
            | (EmployeeAssignment.effective_to >= on),
        )
        .order_by(EmployeeAssignment.effective_from.desc())
        .limit(1)
    ).first()


def screen(session) -> dict:
    """Everything the screen needs to draw itself, in one answer."""
    people = typists(session)
    return {
        "form_order": [{"field": field, "label": label}
                       for field, label in FORM_ORDER],
        "typists": [
            {"code": user.code, "name": user.name, "label": user.label,
             "provisional": user.provisional}
            for user in people
        ],
        "typists_provisional": any(user.provisional for user in people),
        # The seven ticks, in the form's own order, each with the code the
        # screen offers beside it (A48) — and four of them with none.
        "types": [
            {
                "code": row.code,
                "label": row.label,
                "suggested_sheet_code": row.suggested_sheet_code,
                "reason_required": row.reason_required,
            }
            for row in leave_types(session)
        ],
        # The sheet legend. A separate field, and nothing fills it from the
        # tick above (SPEC §6).
        "codes": [{"code": row.code, "label": row.label}
                  for row in leave_codes(session)],
        "not_on_this_screen": NOT_ON_THIS_SCREEN,
    }


def look_up(session, employee_number: str, on=None) -> dict:
    """Name, staff number and department, to be read against the paper.

    **The department is read, not typed, and is not stored.** §6's leave record
    has no department field: the form's Department is the attendance sheet's
    Section, which the employee's assignment row already carries. It is read on
    the date the leave starts, because an assignment is effective-dated and a
    form typed in September may be for August (SPEC §2).
    """
    employee = employee_by_number(session, employee_number)
    on = _parse_date(on, "the date") if on else None
    if on is None:
        from app.corrections import local_now

        local, _ = local_now(session)
        on = local.date()
    assignment = _assignment(session, employee.id, on)
    return {
        "employee_number": employee.employee_number,
        "name": assignment.name if assignment else "",
        "department": assignment.section_code if assignment else None,
        "as_of": on.isoformat(),
        "department_note": (
            "Department is the attendance sheet's Section, read off the "
            "employee's assignment row as it stood on this date. The leave "
            "record has no department field (SPEC §6)."
        ),
    }


def range_check(session, period_from, period_to, days) -> dict:
    """The typed count beside the range it covers. **Shown, never stored.**

    This is the only place in the entry path that counts a range, and what it
    returns goes to the screen and nowhere else: `record` does not call it, has
    no parameter it could fill, and stores the number it was given. A half day,
    and a rest day or a closed holiday inside a range, all make the count and
    the span different numbers, and the form's number is the one that is true
    (SPEC §6).

    `session` is unused today. Every screen function takes one so the routes
    are one shape, and so the day a rule here needs the database — counting the
    closed days inside a range, say — the route above does not change.
    """
    start = _parse_date(period_from, "period from")
    end = _parse_date(period_to, "period to")
    if end < start:
        raise ValueError(f"{end} is before {start}")
    spanned = (end - start).days + 1

    try:
        stated = Decimal(str(days).strip())
        if stated.is_nan() or stated.is_infinite():
            raise InvalidOperation
    except (InvalidOperation, AttributeError, ValueError):
        return {
            "days_stated": None,
            "days_spanned": spanned,
            "counts_differ": None,
            "note": (f"{days!r} is not a number of days. The form states it — "
                     "1, 2, or 0.5 for a half day (SPEC §6)"),
        }

    differ = Decimal(spanned) != stated
    plural = "" if stated == 1 else "s"
    note = f"{stated} day{plural}, as the form states"
    if differ:
        note += (f" — over a {spanned}-day range. The form's number is the one "
                 "that counts; nothing here recomputes it (SPEC §6).")
    else:
        note += f", over a {spanned}-day range."
    return {
        "days_stated": str(stated),
        "days_spanned": spanned,
        "counts_differ": differ,
        "note": note,
    }


def record(session, *, entered_by: str, employee_number: str,
           period_from: str, period_to: str, days: str,
           date_of_application: str | None = None,
           leave_type_code: str | None = None, sheet_code: str | None = None,
           reason: str | None = None) -> dict:
    """One line of leave, as the form has it.

    **`days` is required and is passed straight through.** There is nothing in
    this function that subtracts the two dates, and `range_check` is not called
    from it — adding either would mean changing this line, `record_leave`, and
    the check constraint under it (SPEC §6).

    **It does not commit.** The caller does — the route does, the CLI does, and
    a gate does not. A service function that committed could not be rolled back
    by whoever called it (CLAUDE.md).
    """
    typist = _typist(session, entered_by)
    employee = employee_by_number(session, employee_number)
    row = record_leave(
        session, employee,
        leave_type_code=leave_type_code,
        sheet_code=sheet_code,
        period_from=_parse_date(period_from, "period from"),
        period_to=_parse_date(period_to, "period to"),
        days=days,
        date_of_application=(_parse_date(date_of_application, "the date")
                             if date_of_application else None),
        reason=reason,
        entered_by=typist.name,
        note="typed on the leave entry screen",
    )
    assignment = _assignment(session, employee.id, row.period_from)
    return {
        "id": row.id,
        "employee_number": employee.employee_number,
        "name": assignment.name if assignment else "",
        "department": assignment.section_code if assignment else None,
        "leave_type_code": row.leave_type_code,
        "sheet_code": row.sheet_code,
        "period_from": row.period_from.isoformat(),
        "period_to": row.period_to.isoformat(),
        # As typed. The screen prints this back, and it is the number that
        # counts wherever the two disagree.
        "days": str(row.days),
        "date_of_application": (row.date_of_application.isoformat()
                                if row.date_of_application else None),
        "reason": row.reason,
        "sql_account_code": row.sql_account_code,
        "entered_by": row.entered_by,
        # The month the sheet has to be looked at to see this line, so the
        # screen can offer it without working a date out for itself.
        "month": row.period_from.strftime("%Y-%m"),
        "stored": ("The number of days is stored as the form states it. "
                   "Nothing computed it from the range (SPEC §6)."),
    }
