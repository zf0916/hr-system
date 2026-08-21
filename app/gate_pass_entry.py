"""Step 10, piece 5: the gate pass entry screen's service layer.

**HR types a gate pass that has already been signed on paper** (SPEC §5),
out and in times included. This module is the face the screen puts on
`hr_entry.record_gate_pass` — the same function `hr gatepass add` calls — and
it computes nothing.

Three functions, and only the last one writes:

  * `screen` — who is at the keyboard, the four ticks, and the order the paper
    puts its fields in. The typists and the categories are rows.
  * `look_up` — the name behind an employee number, and the section that is
    **looked up rather than transcribed**, because the form has no department
    on it at all.
  * `record` — one gate pass, through `hr_entry.record_gate_pass`.

**There is no hours parameter, at any depth.** Not on this function, not on
`record_gate_pass`, not in the request model above them, and not in the
database: `gate_pass.hours` is a generated column, and Postgres refuses an
`INSERT` that names it. The form carries no hours field either — the guard
writes two times at the gate and the hours follow from the pair. **This is the
reverse of leave**, where the number of days is written on the form and is
stored exactly as written, never recomputed (§6). Two forms, two opposite
rules, and each one is enforced rather than trusted.

**There is no department parameter either.** §5's form has no department line;
the employee's section is looked up. `look_up` returns it so the person typing
can see who they have, and nothing writes it: the gate pass row has no column
for one.

**The two times are HR's, typed off the paper — and this is not the guard entry
path.** §3's guard entry stands in for a punch the device did not take, is
stamped by the server, and has no field for a time at all. A gate pass time is
HR transcribing an authorised absence somebody already signed for. The two acts
look alike from a distance and only one of them has a time box, so the screen
says which one it is.

**Nothing here approves anything.** The gate pass carries four signatures —
applicant, immediate supervisor, Head of Dept, HR — one fewer than the leave
form, because the Operation Manager does not sign a gate pass (§6). All four
were signed before the paper reached HR; routing an approval is Milestone 5.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from app.corrections import employee_by_number
from app.hr_entry import categories, record_gate_pass, typist, typists
from app.models import EmployeeAssignment, GatePassCategory

# **§5's own field order.** The name and the employee number are two lines on
# the paper — `Name / no. pekerja` is one line and `Emp no.` is a field of its
# own beside it — and the four ticks come after the two times, where the form
# puts them.
#
# **There is no department in this list and there is no room for one**, which
# is the difference from §6's leave form: that one has a Department line, this
# one has none at all.
FORM_ORDER = [
    ("name", "Name / no. pekerja"),
    ("emp_no", "Emp no."),
    ("date", "Date"),
    ("out_time", "Out time"),
    ("in_time", "In time"),
    ("category", "Category"),
    ("reason", "Reason"),
    ("destination", "Destination"),
]

# What the paper carries and this screen does not, said rather than dropped.
NOT_ON_THIS_SCREEN = [
    "The four signature boxes — applicant, immediate supervisor, Head of Dept "
    "and HR Dept. One fewer than the leave form, because the Operation Manager "
    "does not sign a gate pass. All four were signed before the paper reached "
    "HR; routing an approval is Milestone 5 (SPEC §5, §6).",
    "The hours. They are not written on the gate pass and there is nothing "
    "here to type them into: the database works them out from the two times "
    "and they are shown once the pass is saved (SPEC §5).",
    "A department. The form has no line for one, so neither does the record — "
    "the employee's section is looked up and shown, never transcribed "
    "(SPEC §5).",
]

# The sentence the screen prints beside the two time boxes. It lives here
# because it is a rule from §5, not a caption somebody chose.
NOT_THE_GUARD_PATH = (
    "These two times are typed by HR off the paper the guard filled in at the "
    "gate. **This is not the guard entry screen.** That one stands in for a "
    "punch the device did not take, is stamped by the server, and has no field "
    "for a time at all. The two acts look alike and only this one has time "
    "boxes (SPEC §3, §5)."
)


def _parse_date(value, field: str) -> dt.date:
    if isinstance(value, dt.date):
        return value
    try:
        return dt.datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"{field} is {value!r}, which is not a date. Dates "
                         "are YYYY-MM-DD") from None


def _parse_time(value, field: str) -> dt.time:
    """`HH:MM`, or `HH:MM:SS` as a browser's time box sometimes sends it."""
    if isinstance(value, dt.time):
        return value
    text = (value or "").strip()
    for shape in ("%H:%M", "%H:%M:%S"):
        try:
            return dt.datetime.strptime(text, shape).time()
        except ValueError:
            continue
    raise ValueError(f"{field} is {value!r}, which is not a time. Times are "
                     "HH:MM, as they are written on the form")


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
        # The four ticks, in the form's order. Rows, and exactly four: a fifth
        # is refused because nobody has printed one on the paper (SPEC §5).
        "categories": [{"code": row.code, "label": row.label}
                       for row in categories(session)],
        "not_the_guard_path": NOT_THE_GUARD_PATH,
        "not_on_this_screen": NOT_ON_THIS_SCREEN,
    }


def look_up(session, employee_number: str, on=None) -> dict:
    """The name behind a number, and the section that is looked up.

    **The section is not a field on this form and is not stored.** §5 says so
    in as many words: the gate pass has no department, and the employee's
    section is looked up rather than transcribed. It is returned so the person
    typing can see who they have — the same read-back the leave screen does for
    a name — and it goes nowhere else.
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
        "section": assignment.section_code if assignment else None,
        "as_of": on.isoformat(),
        "section_note": (
            "The section is looked up, not typed: the gate pass form has no "
            "department line and the record has no column for one (SPEC §5)."
        ),
    }


def record(session, *, entered_by: str, employee_number: str, pass_date: str,
           out_time: str, in_time: str, category_code: str,
           reason: str | None = None, destination: str | None = None) -> dict:
    """One gate pass, as the form has it.

    **No hours parameter and no department parameter.** Adding either would
    mean changing this line, `record_gate_pass`, the request model above and —
    for the hours — the generated column underneath, which refuses an `INSERT`
    that names it.

    **It does not commit.** The caller does — the route does, the CLI does, and
    a gate does not (CLAUDE.md).
    """
    who = typist(session, entered_by)
    employee = employee_by_number(session, employee_number)
    row = record_gate_pass(
        session, employee,
        pass_date=_parse_date(pass_date, "the date"),
        category_code=category_code,
        out_time=_parse_time(out_time, "the out time"),
        in_time=_parse_time(in_time, "the in time"),
        reason=reason,
        destination=destination,
        entered_by=who.name,
        note="typed on the gate pass entry screen",
    )
    assignment = _assignment(session, employee.id, row.pass_date)
    category = session.get(GatePassCategory, row.category_code)
    return {
        "id": row.id,
        "employee_number": employee.employee_number,
        "name": assignment.name if assignment else "",
        "section": assignment.section_code if assignment else None,
        "pass_date": row.pass_date.isoformat(),
        "out_time": row.out_time.strftime("%H:%M"),
        "in_time": row.in_time.strftime("%H:%M"),
        # **Read back, never worked out here.** The database generated this
        # column from the two times; this is what it stored.
        "hours": str(row.hours),
        "category_code": row.category_code,
        "category_label": category.label if category else row.category_code,
        "reason": row.reason,
        "destination": row.destination,
        "entered_by": row.entered_by,
        "month": row.pass_date.strftime("%Y-%m"),
        "derived": ("The hours were generated by the database from the two "
                    "times. There is no field for them on the form and none on "
                    "this screen (SPEC §5)."),
    }
