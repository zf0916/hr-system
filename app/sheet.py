"""Step 7: the Daily Workers Attendance sheet, in HR's existing layout.

**One render, two outputs.** `render` builds the sheet once, from the daily
attendance rows, and both emitters draw that same object: `to_text` for the
screen, which is the system, and `to_excel` for the file, which is the record
HR files (SPEC §7). Every mark a reader can see — the tick, the out-of-schedule
time, the manual asterisk, whether a column is shaded — is decided here and
carried on the cell. **The emitters choose fonts and column widths and nothing
else**, which is what makes it impossible for the file and the screen to
disagree about what a day says.

What a cell holds (SPEC §7): a tick when the punch is on schedule, the actual
punch time when it is outside it, or a leave code. **Leave codes do not exist
yet** — entry is step 5 — so the leave path is here, empty, and the sheet says
so rather than pretending.

Rest days and public holidays shade whole columns, from the calendar, never per
employee (SPEC §4). Manual punches are marked (SPEC §3).

Nothing here totals anything. Every period total is a query over the daily rows
and belongs to Milestone 3 (SPEC §3).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import select

from app.models import (
    DailyAttendance,
    Employee,
    EmployeeAssignment,
    GroupSchedule,
    SheetSetting,
)
from app.schedule import effective_holiday, is_rest_day, schedule_for

# What a cell is showing, so a reader never has to infer it from the text.
TICK = "tick"
TIME = "time"
LEAVE = "leave"
EMPTY = "empty"


@dataclass
class Cell:
    employee_id: int
    date: dt.date
    text: str
    kind: str
    manual: bool = False
    leave_code: str | None = None
    punch_count: int = 0
    late_minutes: int | None = None
    provisional: bool = False
    detail: str = ""


@dataclass
class Column:
    date: dt.date
    day: int
    weekday: str
    shaded: bool
    shade_reason: str
    holiday_name: str | None = None
    rest_for: tuple[str, ...] = ()
    provisional_holiday: bool = False


@dataclass
class Row:
    employee_id: int
    employee_number: str
    name: str
    section_code: str
    role_code: str
    group_code: str
    page: int


@dataclass
class Sheet:
    title: str
    period_start: dt.date
    period_end: dt.date
    note_top_left: str
    note_is_unread: bool
    rows_per_page: int
    page_count: int
    headcount: int
    columns: list[Column]
    rows: list[Row]
    cells: dict[tuple[int, dt.date], Cell]
    legend: list[tuple[str, str]]
    notes: list[str] = field(default_factory=list)

    def cell(self, employee_id: int, day: dt.date) -> Cell:
        return self.cells[(employee_id, day)]

    def page(self, number: int) -> list[Row]:
        return [row for row in self.rows if row.page == number]


def settings(session) -> dict[str, str]:
    return {row.key: row.value for row in session.scalars(select(SheetSetting))}


def period_for(session, month: str) -> tuple[dt.date, dt.date]:
    """The period one sheet covers, from the rule row (A40).

    `calendar_month` is the assumption. The 10th/15th/20th cut-offs on the paper
    sheet may be deadlines rather than boundaries (SPEC §5), and until that is
    answered this is a row that an UPDATE corrects.
    """
    rule = settings(session).get("sheet.period_rule", "calendar_month")
    year, mon = (int(part) for part in month.split("-"))
    start = dt.date(year, mon, 1)
    if rule != "calendar_month":
        raise ValueError(
            f"sheet.period_rule is {rule!r}, and only 'calendar_month' is "
            "implemented. The rule is a row; teach the renderer the new one "
            "before changing it"
        )
    end_month = start.replace(day=28) + dt.timedelta(days=4)
    end = end_month - dt.timedelta(days=end_month.day)
    return start, end


def _cell_for(row: DailyAttendance | None, employee_id: int, day: dt.date,
              marks: dict[str, str]) -> Cell:
    """One day for one employee, from its daily row and nothing else.

    A38: a tick when every punch that day is inside the schedule; otherwise the
    punch times that fall outside it — the first in when it is later than the
    scheduled start plus grace, the last out when it is earlier than the
    scheduled end. Both, when both are.
    """
    if row is None:
        return Cell(employee_id, day, "", EMPTY,
                    detail="no daily row: not employed, or not built")

    # Leave is step 5. The path exists and is empty; nothing is invented here.
    leave_code = None

    if row.punch_count == 0:
        return Cell(employee_id, day, "", EMPTY, punch_count=0,
                    provisional=row.schedule_provisional,
                    detail="no punch — a fact, not an absence (SPEC §3)")

    time_format = marks.get("sheet.time_format", "%H:%M")
    outside: list[str] = []
    if row.scheduled_start is None:
        # Nothing to be inside of: a rest day, a closed holiday, or no schedule.
        # The punch is real and shows as a time.
        if row.first_in:
            outside.append(row.first_in.strftime(time_format))
        if row.last_out:
            outside.append(row.last_out.strftime(time_format))
    else:
        if row.late_minutes:
            outside.append(row.first_in.strftime(time_format))
        if (row.last_out is not None and row.scheduled_end is not None
                and row.last_out < row.scheduled_end):
            outside.append(row.last_out.strftime(time_format))

    manual = bool(row.first_in_manual or row.last_out_manual)
    mark = marks.get("sheet.mark_manual", "*") if manual else ""

    if outside:
        text, kind = "/".join(outside), TIME
    else:
        text, kind = marks.get("sheet.mark_on_schedule", "✓"), TICK

    detail = f"{row.punch_count} punches"
    if row.manual_punch_count:
        detail += f", {row.manual_punch_count} entered by a person"
    if row.duplicate_pushes:
        detail += f", {row.duplicate_pushes} re-pushes dropped"

    return Cell(
        employee_id, day, text + mark, kind, manual=manual,
        leave_code=leave_code, punch_count=row.punch_count,
        late_minutes=row.late_minutes, provisional=row.schedule_provisional,
        detail=detail,
    )


def render(session, start: dt.date, end: dt.date,
           section_code: str | None = None) -> Sheet:
    """Build the sheet once. Both outputs draw this."""
    if end < start:
        raise ValueError(f"{end} is before {start}")

    marks = settings(session)
    rows_per_page = int(marks.get("sheet.rows_per_page", "30"))

    assignments = session.execute(
        select(EmployeeAssignment, Employee.employee_number)
        .join(Employee, Employee.id == EmployeeAssignment.employee_id)
        .where(
            EmployeeAssignment.effective_from <= end,
            (EmployeeAssignment.effective_to.is_(None))
            | (EmployeeAssignment.effective_to >= start),
        )
        .order_by(EmployeeAssignment.section_code, Employee.employee_number)
    ).all()
    if section_code:
        assignments = [a for a in assignments if a[0].section_code == section_code]

    sheet_rows: list[Row] = []
    for index, (assignment, number) in enumerate(assignments):
        sheet_rows.append(Row(
            employee_id=assignment.employee_id,
            employee_number=number,
            name=assignment.name,
            section_code=assignment.section_code,
            role_code=assignment.role_code,
            group_code=assignment.group_code,
            page=index // rows_per_page + 1,
        ))

    groups = sorted({row.group_code for row in sheet_rows})

    columns: list[Column] = []
    notes: list[str] = []
    day = start
    while day <= end:
        holiday = effective_holiday(session, day)
        closes = bool(holiday and holiday.closes)
        resting = []
        for group in groups:
            schedule = schedule_for(session, group, day)
            if schedule is not None and is_rest_day(schedule, day):
                resting.append(group)

        # A42: a column shades when the day is closed for everyone on the sheet.
        # Groups that rest on different days would break whole-column shading,
        # and that is a fact about the sheet rather than something to paper over.
        all_rest = bool(groups) and len(resting) == len(groups)
        shaded = closes or all_rest
        reason = ""
        if closes:
            reason = holiday.name
        elif all_rest:
            reason = "rest day"
        elif resting:
            reason = "rest day for some groups only — not shaded"
            notes.append(
                f"{day}: rest day for {', '.join(resting)} but not for "
                f"{', '.join(g for g in groups if g not in resting)}, so the "
                "column is not shaded (SPEC §9 A42)"
            )

        columns.append(Column(
            date=day, day=day.day, weekday=day.strftime("%a"), shaded=shaded,
            shade_reason=reason,
            holiday_name=holiday.name if holiday else None,
            rest_for=tuple(resting),
            provisional_holiday=bool(holiday and holiday.provisional),
        ))
        day += dt.timedelta(days=1)

    daily = {
        (row.employee_id, row.attendance_day): row
        for row in session.scalars(
            select(DailyAttendance).where(
                DailyAttendance.attendance_day >= start,
                DailyAttendance.attendance_day <= end,
            )
        )
    }
    cells = {
        (row.employee_id, column.date): _cell_for(
            daily.get((row.employee_id, column.date)), row.employee_id,
            column.date, marks)
        for row in sheet_rows
        for column in columns
    }

    if any(cell.provisional for cell in cells.values()):
        notes.append(
            "Times and lateness on this sheet rest on schedule rows HR has not "
            "confirmed. Every seeded schedule is marked provisional."
        )
    if any(column.provisional_holiday for column in columns):
        notes.append("Some holidays on this calendar are provisional.")
    notes.append(
        "Leave codes are not entered yet — HR entry is step 5. No cell on this "
        "sheet can hold one, and none is invented."
    )
    if not any(cell.punch_count for cell in cells.values()):
        notes.append("No punches fell in this period for anyone on the sheet.")

    note = marks.get("sheet.note_top_left", "")
    return Sheet(
        title=marks.get("sheet.title", "DAILY WORKERS ATTENDANCE"),
        period_start=start,
        period_end=end,
        note_top_left=note,
        note_is_unread=not note.strip(),
        rows_per_page=rows_per_page,
        page_count=max(1, (len(sheet_rows) + rows_per_page - 1) // rows_per_page),
        headcount=len(sheet_rows),
        columns=columns,
        rows=sheet_rows,
        cells=cells,
        legend=legend_rows(session),
        notes=notes,
    )


def legend_rows(session) -> list[tuple[str, str]]:
    """The sheet's own marks, from rows.

    **The leave codes are not here yet.** HR's paper legend carries them (SPEC
    §6) and §13 says a leave code is a row rather than a constant — so they
    arrive as rows with leave entry, which is step 5. Printing them from this
    file would be inventing a vocabulary that nothing can write into a cell.
    """
    marks = settings(session)
    return [
        (marks.get("sheet.mark_on_schedule", "✓"), "punch on schedule"),
        ("08:20", "the actual punch time, when it is outside the schedule"),
        (marks.get("sheet.mark_manual", "*"),
         "punch entered by a person, not the device"),
        ("(blank)", "no punch — a fact, never an absence (SPEC §3)"),
        ("(shaded)", "rest day or a public holiday the factory closes for"),
    ]


# ---- the screen ----------------------------------------------------------


def to_text(sheet: Sheet, width: int | None = None) -> str:
    """The screen. Same object, same marks, same shading — as text.

    The column is as wide as the widest thing in it. A cell showing both an
    out-of-schedule arrival and an out-of-schedule departure is eleven
    characters, and a fixed width would push the grid out of line — which is a
    reader mistaking one day for another.
    """
    out: list[str] = []

    def column_width(column: Column) -> int:
        if width is not None:
            return width
        longest = max(
            (len(sheet.cell(row.employee_id, column.date).text)
             for row in sheet.rows),
            default=0,
        )
        return max(5, longest + 1)

    widths = [column_width(column) for column in sheet.columns]
    out.append(sheet.title)
    out.append(f"{sheet.period_start} → {sheet.period_end}"
               f"   headcount {sheet.headcount}"
               f"   {sheet.page_count} page(s) of {sheet.rows_per_page}")
    if sheet.note_is_unread:
        out.append("note (top-left): [ ]  <- not read; nothing is guessed here "
                   "(SPEC §9 A41)")
    else:
        out.append(f"note (top-left): {sheet.note_top_left}")

    header = " " * 26 + "".join(
        f"{c.day:>{w}}" for c, w in zip(sheet.columns, widths))
    weekdays = " " * 26 + "".join(
        f"{c.weekday[:2]:>{w}}" for c, w in zip(sheet.columns, widths))
    shading = " " * 26 + "".join(
        f"{'▓▓▓▓' if c.shaded else '':>{w}}"
        for c, w in zip(sheet.columns, widths))
    out += ["", header, weekdays, shading]

    section = None
    for row in sheet.rows:
        if row.section_code != section:
            section = row.section_code
            out.append(f"-- {section}")
        line = f"{row.employee_number:>6} {row.name[:14]:<14} {row.role_code[:3]:<4}"
        for column, w in zip(sheet.columns, widths):
            line += f"{sheet.cell(row.employee_id, column.date).text:>{w}}"
        out.append(line)

    out.append("")
    out.append("legend: " + "  ".join(f"{code} {label}"
                                      for code, label in sheet.legend))
    for note in sheet.notes:
        out.append(f"note: {note}")
    for column in sheet.columns:
        if column.shaded:
            out.append(f"shaded: {column.date} — {column.shade_reason}")
    return "\n".join(out)


# ---- the file -----------------------------------------------------------


def to_excel(sheet: Sheet, path) -> None:
    """The record. The same object as the screen, drawn into HR's layout.

    Every string written here comes off the Sheet. Fonts, widths and fills are
    this function's business; **what a cell says is not** (SPEC §7).
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Attendance"

    grey = PatternFill("solid", fgColor="D9D9D9")
    manual_fill = PatternFill("solid", fgColor="FFF2CC")
    unread = PatternFill("solid", fgColor="FCE4D6")
    thin = Side(style="thin", color="999999")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    centre = Alignment(horizontal="center", vertical="center")

    worksheet["A1"] = sheet.title
    worksheet["A1"].font = Font(bold=True, size=14)
    worksheet["A2"] = f"{sheet.period_start} to {sheet.period_end}"
    worksheet["A3"] = f"headcount {sheet.headcount}"

    # The top-left note: the cell is empty, and the cell beside it says why.
    worksheet["A4"] = sheet.note_top_left
    if sheet.note_is_unread:
        worksheet["A4"].fill = unread
        worksheet["B4"] = "note not read — nothing guessed (SPEC §9 A41)"
        worksheet["B4"].font = Font(italic=True, color="C00000")

    header_row = 6
    worksheet.cell(header_row, 1, "No.").font = Font(bold=True)
    worksheet.cell(header_row, 2, "Name").font = Font(bold=True)
    worksheet.cell(header_row, 3, "Section").font = Font(bold=True)
    worksheet.cell(header_row, 4, "Role").font = Font(bold=True)
    for index, column in enumerate(sheet.columns):
        cell = worksheet.cell(header_row, 5 + index, column.day)
        cell.font = Font(bold=True)
        cell.alignment = centre
        cell.border = box
        weekday = worksheet.cell(header_row + 1, 5 + index, column.weekday)
        weekday.alignment = centre
        weekday.border = box
        if column.shaded:
            cell.fill = grey
            weekday.fill = grey

    first_data_row = header_row + 2
    for offset, row in enumerate(sheet.rows):
        excel_row = first_data_row + offset
        worksheet.cell(excel_row, 1, row.employee_number)
        worksheet.cell(excel_row, 2, row.name)
        worksheet.cell(excel_row, 3, row.section_code)
        worksheet.cell(excel_row, 4, row.role_code)
        for index, column in enumerate(sheet.columns):
            model_cell = sheet.cell(row.employee_id, column.date)
            cell = worksheet.cell(excel_row, 5 + index, model_cell.text or None)
            cell.alignment = centre
            cell.border = box
            if column.shaded:
                cell.fill = grey
            elif model_cell.manual:
                cell.fill = manual_fill

    note_row = first_data_row + len(sheet.rows) + 2
    worksheet.cell(note_row, 1, "Legend").font = Font(bold=True)
    for index, (code, label) in enumerate(sheet.legend, start=1):
        worksheet.cell(note_row + index, 1, code)
        worksheet.cell(note_row + index, 2, label)
    for index, note in enumerate(sheet.notes, start=len(sheet.legend) + 2):
        worksheet.cell(note_row + index, 1, note)

    worksheet.column_dimensions["A"].width = 8
    worksheet.column_dimensions["B"].width = 26
    worksheet.column_dimensions["C"].width = 14
    worksheet.column_dimensions["D"].width = 18
    worksheet.freeze_panes = worksheet.cell(first_data_row, 5)
    workbook.save(path)


def excel_layout() -> dict:
    """Where `to_excel` puts things, so a reader does not have to guess.

    Used by the readback check in `tools/`, which is a verification of the file
    this code wrote — never an ingest path. Nothing in the application reads a
    sheet back in (SPEC §7, §13).
    """
    return {
        "header_row": 6,
        "weekday_row": 7,
        "first_data_row": 8,
        "first_day_column": 5,
        "number_column": 1,
    }
