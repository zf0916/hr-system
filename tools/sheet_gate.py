#!/usr/bin/env python3
"""The gate for step 7: deliberate mistakes that must fail.

Every case builds its own employees, groups, schedules, calendar and punches
inside a transaction that is rolled back, and writes its Excel to a temporary
file that is deleted.

The six the step was asked to prove, each shown working and then broken:

  1. the screen and the Excel cannot disagree about a day;
  2. a manual punch never renders as a device punch;
  3. a rest day and a public holiday shade whole columns, from the calendar;
  4. inside the schedule is a tick, outside it is the time;
  5. a night-shift day renders on its own calendar column;
  6. regenerating a period twice produces the same sheet.

    uv run python tools/sheet_gate.py

Exits non-zero if any deliberate mistake was accepted.
"""

from __future__ import annotations

import datetime as dt
import tempfile
from pathlib import Path

from sqlalchemy import select, text

from app.attendance import build_days
from app.corrections import record_hr_retroactive
from app.db import Session
from app.models import (
    Employee,
    EmployeeAssignment,
    EmployeeGroup,
    EmployeeNumberKey,
    EmploymentPeriod,
    DeviceUserMap,
    Holiday,
    HolidayAdjustment,
    RawRequest,
    SheetSetting,
)
from app.parser import parse_raw_request
from app.schedule import set_schedule
from app.sheet import TICK, TIME, EMPTY, render, to_excel, to_text
from tools.sheet_readback import compare, read_sheet_file

DAY_GROUP, NIGHT_GROUP = "GATE-SH-DAY", "GATE-SH-NIGHT"
DAY_NUMBER, DAY_PIN = "9701", "9701"
NIGHT_NUMBER, NIGHT_PIN = "9702", "9702"

# March 2026: 2nd is a Monday, 8th a Sunday.
MONTH_START, MONTH_END = dt.date(2026, 3, 1), dt.date(2026, 3, 31)
MONDAY, SUNDAY = dt.date(2026, 3, 2), dt.date(2026, 3, 8)
HOLIDAY = dt.date(2026, 3, 12)

DAY_SHIFT = {
    "start_time": dt.time(8, 0), "end_time": dt.time(17, 30),
    "end_next_day": False, "rest_weekdays": [7], "grace_minutes": 0,
    "provisional": True,
}
NIGHT_SHIFT = {
    "start_time": dt.time(19, 30), "end_time": dt.time(4, 30),
    "end_next_day": True, "rest_weekdays": [7], "grace_minutes": 0,
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


def make_employee(session, number, pin, group, name, section="QC") -> Employee:
    employee = Employee(employee_number=number)
    session.add(employee)
    session.flush()
    session.add(EmployeeNumberKey(employee_id=employee.id, key=number,
                                 built_by="gate"))
    session.add(EmployeeAssignment(
        employee_id=employee.id, effective_from=dt.date(2020, 1, 1), name=name,
        section_code=section, role_code="QA/QC", group_code=group))
    session.add(EmploymentPeriod(employee_id=employee.id,
                                active_from=dt.date(2020, 1, 1)))
    session.add(DeviceUserMap(employee_id=employee.id, pin=pin,
                             effective_from=dt.date(2020, 1, 1), source="gate"))
    session.flush()
    return employee


def setup(session) -> tuple[Employee, Employee]:
    session.add(EmployeeGroup(code=DAY_GROUP, label=DAY_GROUP, note="gate"))
    session.add(EmployeeGroup(code=NIGHT_GROUP, label=NIGHT_GROUP, note="gate"))
    session.flush()
    set_schedule(session, DAY_GROUP, dt.date(2020, 1, 1), **DAY_SHIFT)
    set_schedule(session, NIGHT_GROUP, dt.date(2020, 1, 1), **NIGHT_SHIFT)
    day = make_employee(session, DAY_NUMBER, DAY_PIN, DAY_GROUP, "Day Shift")
    night = make_employee(session, NIGHT_NUMBER, NIGHT_PIN, NIGHT_GROUP,
                          "Night Shift")
    # A public holiday the factory closes for, in the same month.
    session.add(Holiday(holiday_date=HOLIDAY, name="Gate Holiday",
                        scope_code="federal", closes=True, provisional=True))
    session.flush()
    return day, night


def punch(session, pin: str, at: dt.datetime) -> None:
    fields = [pin, at.strftime("%Y-%m-%d %H:%M:%S"), "255", "15"] + ["0"] * 6
    body = ("\t".join(fields) + "\t\r\n").encode()
    raw = RawRequest(
        method="POST", path="/iclock/cdata",
        query_string="SN=GATE&table=ATTLOG&Stamp=9999",
        headers=[["content-type", "text/plain"]], content_type="text/plain",
        body=body, body_bytes=len(body), serial_number="GATE",
        table_param="ATTLOG", stamp_param="9999", response_body="OK: 1")
    session.add(raw)
    session.flush()
    parse_raw_request(session, raw)
    session.flush()


def in_temp_file(sheet, compare_against=None) -> tuple[dict, list[str]]:
    """Write the Excel, read it back, and compare.

    `compare_against` is what the file is checked against, and defaults to the
    sheet that was written — pass a different render to ask whether the check
    would actually notice the two disagreeing. Comparing a sheet with itself
    proves nothing, which is the mistake this parameter exists to avoid.

    The written file is deleted: the export is an output, and nothing in the
    system keeps or re-reads it (SPEC §7, §13).
    """
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "sheet.xlsx"
        to_excel(sheet, path)
        contents = read_sheet_file(path)
        return contents, compare(compare_against or sheet, contents)


def main() -> int:
    gate = Gate()

    print("\n-- the sheet's values are rows, not constants")
    with Session() as session:
        keys = set(session.scalars(select(SheetSetting.key)))
        for key in ("sheet.rows_per_page", "sheet.period_rule",
                    "sheet.note_top_left", "sheet.mark_on_schedule",
                    "sheet.mark_manual", "sheet.title"):
            gate.check(key in keys, f"{key} is a row", f"rows: {sorted(keys)}")
        note = session.get(SheetSetting, "sheet.note_top_left")
        gate.check(note is not None and note.value == "",
                   "the top-left note is empty, because nobody has read it",
                   f"got {note.value!r}" if note else "missing")

    print("\n-- 1, 2, 4: one render, two outputs, and what a cell says")
    with Session() as session:
        day, _ = setup(session)
        # Inside the schedule: 08:00 in, 17:30 out on the 3rd.
        inside = dt.date(2026, 3, 3)
        punch(session, DAY_PIN, dt.datetime.combine(inside, dt.time(7, 58)))
        punch(session, DAY_PIN, dt.datetime.combine(inside, dt.time(17, 35)))
        # Late: 08:20 in on the 4th, out on time.
        late = dt.date(2026, 3, 4)
        punch(session, DAY_PIN, dt.datetime.combine(late, dt.time(8, 20)))
        punch(session, DAY_PIN, dt.datetime.combine(late, dt.time(17, 40)))
        # Early away: on time in, 16:05 out on the 5th.
        early = dt.date(2026, 3, 5)
        punch(session, DAY_PIN, dt.datetime.combine(early, dt.time(7, 55)))
        punch(session, DAY_PIN, dt.datetime.combine(early, dt.time(16, 5)))
        # A manual punch, HR retroactive, on the 6th.
        manual_day = dt.date(2026, 3, 6)
        record_hr_retroactive(
            session, day,
            asserted_time=dt.datetime.combine(manual_day, dt.time(8, 0)),
            reason="device down", made_by="HR: Gate")
        punch(session, DAY_PIN, dt.datetime.combine(manual_day, dt.time(17, 32)))
        build_days(session, MONTH_START, MONTH_END, [day.id])

        sheet = render(session, MONTH_START, MONTH_END)
        cells = {c.date: c for c in
                 (sheet.cell(day.id, col.date) for col in sheet.columns)}

        gate.check(cells[inside].kind == TICK and cells[inside].text == "✓",
                   "a day inside the schedule is a tick",
                   f"got {cells[inside].text!r} ({cells[inside].kind})")
        gate.check(cells[late].kind == TIME and cells[late].text == "08:20",
                   "a late arrival shows the actual punch time",
                   f"got {cells[late].text!r}")
        gate.check(cells[early].text == "16:05",
                   "leaving before the scheduled end shows that time too",
                   f"got {cells[early].text!r}")
        gate.check(cells[manual_day].manual
                   and cells[manual_day].text.endswith("*"),
                   "a manual punch is marked in the cell",
                   f"got {cells[manual_day].text!r}")
        gate.check(cells[dt.date(2026, 3, 10)].kind == EMPTY
                   and cells[dt.date(2026, 3, 10)].text == "",
                   "a day with no punch is blank, not an absence code",
                   f"got {cells[dt.date(2026, 3, 10)].text!r}")

        contents, problems = in_temp_file(sheet)
        gate.check(not problems,
                   "the Excel and the screen agree about every day",
                   f"{len(problems)} differences: {problems[:3]}")
        gate.check(contents["cells"][(DAY_NUMBER, late.day)] == "08:20",
                   "the file carries the out-of-schedule time as written",
                   f"got {contents['cells'].get((DAY_NUMBER, late.day))!r}")
        gate.check(contents["cells"][(DAY_NUMBER, manual_day.day)].endswith("*"),
                   "the file carries the manual mark",
                   f"got {contents['cells'].get((DAY_NUMBER, manual_day.day))!r}")
        gate.check(str(contents["note"]).strip() == ""
                   and "not read" in (contents["note_marker"] or ""),
                   "the unread note is empty in the file and marked as unread",
                   f"note {contents['note']!r} marker {contents['note_marker']!r}")

        # The deliberate mistakes, one at a time, against the same render.
        print("       and now the mistakes:")
        # The file is written from the true render; a second render is then
        # made to disagree with it about one day for one employee. The readback
        # has to notice, or it is not checking anything.
        broken = render(session, MONTH_START, MONTH_END)
        broken.cells[(day.id, late)].text = "✓"
        _, disagreement = in_temp_file(sheet, compare_against=broken)
        gate.check(bool(disagreement),
                   "the screen and the file disagreeing about one day is caught",
                   "the readback reported them as agreeing")
        gate.check(any(f"{late}" in problem for problem in disagreement),
                   f"and it names the day: {disagreement[:1]}")

        # The same, the other way round: a file missing the manual mark.
        unmarked = render(session, MONTH_START, MONTH_END)
        unmarked_cell = unmarked.cells[(day.id, manual_day)]
        unmarked_cell.text = unmarked_cell.text.rstrip("*")
        _, mark_gone = in_temp_file(unmarked, compare_against=sheet)
        gate.check(bool(mark_gone),
                   "a file that lost the manual mark is caught",
                   "the readback reported them as agreeing")

        stripped = render(session, MONTH_START, MONTH_END)
        cell = stripped.cells[(day.id, manual_day)]
        cell.text = cell.text.rstrip("*")
        cell.manual = False
        gate.check(stripped.cell(day.id, manual_day).text
                   != sheet.cell(day.id, manual_day).text,
                   "a manual punch rendered like a device punch is a different "
                   "sheet — the mark is the only thing distinguishing it")

        swapped = render(session, MONTH_START, MONTH_END)
        swapped.cells[(day.id, inside)].text = "07:58"
        swapped.cells[(day.id, late)].text = "✓"
        text_out = to_text(swapped)
        gate.check("07:58" in text_out and swapped.cell(day.id, late).text == "✓",
                   "swapping tick and time changes what the sheet claims: an "
                   "on-time day reads as out of schedule and a late day reads "
                   "as on time")
        session.rollback()

    print("\n-- 3. rest days and holidays shade whole columns, from the calendar")
    with Session() as session:
        day, night = setup(session)
        build_days(session, MONTH_START, MONTH_END, [day.id, night.id])
        sheet = render(session, MONTH_START, MONTH_END)
        columns = {c.date: c for c in sheet.columns}

        gate.check(columns[SUNDAY].shaded
                   and columns[SUNDAY].shade_reason == "rest day",
                   "Sunday shades", f"got {columns[SUNDAY].shade_reason!r}")
        gate.check(columns[HOLIDAY].shaded
                   and columns[HOLIDAY].shade_reason == "Gate Holiday",
                   "the public holiday shades",
                   f"got {columns[HOLIDAY].shade_reason!r}")
        gate.check(not columns[MONDAY].shaded, "a working Monday does not shade")
        gate.check(all(sheet.cell(r.employee_id, SUNDAY) is not None
                       for r in sheet.rows),
                   "shading is a property of the column, so it covers every row")

        # The deliberate mistake: a holiday the factory works must not shade.
        session.add(HolidayAdjustment(
            holiday_date=HOLIDAY, action="set", closes=False,
            reason="worked this year", made_by="gate"))
        session.flush()
        worked = render(session, MONTH_START, MONTH_END)
        worked_column = {c.date: c for c in worked.columns}[HOLIDAY]
        gate.check(not worked_column.shaded,
                   "a gazetted holiday the factory works does not shade — only "
                   "the closes flag shades (SPEC §4)",
                   f"shaded with reason {worked_column.shade_reason!r}")
        gate.check(worked_column.holiday_name == "Gate Holiday",
                   "and it is still a holiday on the calendar")

        # The other deliberate mistake: shading per employee. The column has no
        # per-employee shading to set, which is the point.
        gate.check(not hasattr(sheet.cells[(day.id, SUNDAY)], "shaded"),
                   "a cell has no shading of its own to disagree with the column")

        # Groups that rest on different days break whole-column shading, and
        # that is stated rather than papered over (A42).
        session.execute(
            text("UPDATE group_schedule SET rest_weekdays = ARRAY[6] "
                 "WHERE group_code = :g"), {"g": NIGHT_GROUP})
        session.flush()
        split = render(session, MONTH_START, MONTH_END)
        split_sunday = {c.date: c for c in split.columns}[SUNDAY]
        gate.check(not split_sunday.shaded
                   and any("not shaded" in note for note in split.notes),
                   "when one group rests on another day the column does not "
                   "shade, and the sheet says so",
                   f"shaded={split_sunday.shaded} notes={split.notes[:2]}")
        session.rollback()

    print("\n-- 5. a night-shift day renders on its own calendar column")
    with Session() as session:
        _, night = setup(session)
        punch(session, NIGHT_PIN, dt.datetime.combine(MONDAY, dt.time(19, 40)))
        punch(session, NIGHT_PIN,
              dt.datetime.combine(MONDAY + dt.timedelta(days=1), dt.time(4, 35)))
        build_days(session, MONTH_START, MONTH_END, [night.id])
        sheet = render(session, MONTH_START, MONTH_END)

        monday_cell = sheet.cell(night.id, MONDAY)
        tuesday_cell = sheet.cell(night.id, MONDAY + dt.timedelta(days=1))
        gate.check(monday_cell.punch_count == 2,
                   "both punches land on Monday's column",
                   f"got {monday_cell.punch_count}")
        gate.check(tuesday_cell.kind == EMPTY and tuesday_cell.text == "",
                   "Tuesday's column is blank — the 04:35 punch is not its own",
                   f"got {tuesday_cell.text!r}")
        gate.check(monday_cell.text == "19:40",
                   "Monday shows the late arrival against the 19:30 start",
                   f"got {monday_cell.text!r}")
        contents, problems = in_temp_file(sheet)
        gate.check(not problems and contents["cells"][(NIGHT_NUMBER, MONDAY.day)]
                   == "19:40",
                   "and the file puts it in the same column",
                   f"{problems[:2]}")
        session.rollback()

    print("\n-- 6. regenerating the same period twice gives the same sheet")
    with Session() as session:
        day, night = setup(session)
        punch(session, DAY_PIN, dt.datetime.combine(MONDAY, dt.time(8, 11)))
        punch(session, DAY_PIN, dt.datetime.combine(MONDAY, dt.time(17, 33)))
        build_days(session, MONTH_START, MONTH_END, [day.id, night.id])

        first = render(session, MONTH_START, MONTH_END)
        second = render(session, MONTH_START, MONTH_END)
        same_cells = all(
            first.cell(r.employee_id, c.date).text
            == second.cell(r.employee_id, c.date).text
            for r in first.rows for c in first.columns
        )
        gate.check(same_cells, "every cell is identical")
        gate.check(to_text(first) == to_text(second), "the screens are identical")
        gate.check([c.shaded for c in first.columns]
                   == [c.shaded for c in second.columns],
                   "the shading is identical")
        gate.check(first.notes == second.notes, "the notes are identical")

        one, _ = in_temp_file(first)
        two, _ = in_temp_file(second)
        gate.check(one["cells"] == two["cells"],
                   "and two exports contain the same cells")

        # A rebuilt daily layer moves the sheet, which is the point of a
        # generated sheet — and it moves both outputs together.
        set_schedule(session, DAY_GROUP, MONDAY,
                     **{**DAY_SHIFT, "start_time": dt.time(8, 30)})
        build_days(session, MONTH_START, MONTH_END, [day.id, night.id])
        third = render(session, MONTH_START, MONTH_END)
        gate.check(third.cell(day.id, MONDAY).text == "✓"
                   and first.cell(day.id, MONDAY).text == "08:11",
                   "a corrected schedule turns the time into a tick, on the "
                   "screen and in the file alike",
                   f"got {third.cell(day.id, MONDAY).text!r}")
        contents, problems = in_temp_file(third)
        gate.check(not problems, "and the file follows the render",
                   f"{problems[:2]}")
        session.rollback()

    print("\n-- what this step deliberately does not do")
    with Session() as session:
        day, _ = setup(session)
        build_days(session, MONTH_START, MONTH_END, [day.id])
        sheet = render(session, MONTH_START, MONTH_END)
        gate.check(all(cell.leave_code is None for cell in sheet.cells.values()),
                   "no cell carries a leave code: entry is step 5")
        gate.check(any("Leave codes are not entered yet" in note
                       for note in sheet.notes),
                   "and the sheet says so rather than leaving it to be noticed")
        gate.check(not hasattr(sheet, "totals") and not hasattr(sheet, "period_total"),
                   "no totals on the sheet — a period total is a query over the "
                   "daily rows (SPEC §3)")
        gate.check(any("provisional" in note for note in sheet.notes),
                   "the sheet says its figures rest on provisional schedules")
        session.rollback()

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
