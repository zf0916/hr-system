#!/usr/bin/env python3
"""The gate for the employee importer: deliberate mistakes that must fail.

A checker that only ever sees good input has not been tested. Each case below
breaks the fixture or the mapping in a way a real load could plausibly be
broken, and the case passes only if the import refuses it *and* says something
a person could act on.

Nothing is committed. Every case runs inside a transaction that is rolled back,
so the gate can be run against a database with the list already loaded.

    uv run python tools/employee_import_gate.py

Exits non-zero if any deliberate mistake was accepted, or if the clean fixture
was refused.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from openpyxl import load_workbook

from app.db import Session
from app.employee_import import MappingError, run_import

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "fixtures" / "employees_sample.xlsx"

BASE_MAPPING = {
    "sheet": '"Staff List"',
    "header_row": "4",
    "first_data_row": "5",
    "last_data_row": "12",
    "date_format": '"%d/%m/%Y"',
}
BASE_COLUMNS = {
    "employee_number": "B",
    "name": "D",
    "section": "E",
    "role": "F",
    "group": "G",
    "active_from": "I",
    "left_on": "J",
    "device_pin": "K",
}


def write_mapping(path: Path, top: dict | None = None,
                  columns: dict | None = None) -> Path:
    settings = dict(BASE_MAPPING)
    settings.update(top or {})
    cols = dict(BASE_COLUMNS)
    cols.update(columns or {})
    lines = [f"{key} = {value}" for key, value in settings.items() if value is not None]
    lines.append("")
    lines.append("[columns]")
    lines += [f'{key} = "{value}"' for key, value in cols.items() if value is not None]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_workbook(path: Path, edits: list[tuple[int, int, object]]) -> Path:
    shutil.copy(FIXTURE, path)
    if not edits:
        return path
    workbook = load_workbook(path)
    sheet = workbook["Staff List"]
    for row, column, value in edits:
        # openpyxl's cell(value=...) ignores None, so blanking is an empty string.
        sheet.cell(row=row, column=column).value = value
    workbook.save(path)
    workbook.close()
    return path


class Gate:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.checks = 0

    def check(self, ok: bool, what: str, detail: str = "") -> None:
        self.checks += 1
        if ok:
            print(f"  ok      {what}")
        else:
            print(f"  FAIL    {what}" + (f" — {detail}" if detail else ""))
            self.failures.append(what)

    def attempt(self, workdir: Path, label: str, *, edits=None, top=None,
                columns=None, flags=(), allow_new=("group",),
                preload=False) -> tuple[list[str], dict]:
        """Run one import. Returns (messages, written) and never commits."""
        source = write_workbook(workdir / f"{label}.xlsx", edits or [])
        mapping = write_mapping(workdir / f"{label}.toml", top, columns)
        with Session() as session:
            try:
                if preload:
                    run_import(session, FIXTURE,
                               write_mapping(workdir / f"{label}-pre.toml"),
                               replace=True, allow_new={"group"},
                               accept_odd_numbers=False)
                result = run_import(
                    session,
                    source,
                    mapping,
                    replace="no-replace" not in flags and not preload,
                    allow_new=set(allow_new),
                    accept_odd_numbers="accept-odd-numbers" in flags,
                )
            except MappingError as exc:
                session.rollback()
                return [str(exc)], {}
            written = dict(result.written)
            messages = [f"{p.field}: {p.message}" for p in result.problems]
            session.rollback()
            return messages, written if not result.problems else {}

    def must_fail(self, workdir: Path, label: str, expected: str, **kwargs) -> None:
        messages, written = self.attempt(workdir, label, **kwargs)
        if not messages:
            self.check(False, label, f"accepted it and wrote {written}")
            return
        hit = any(expected.lower() in m.lower() for m in messages)
        self.check(hit, label, f"failed, but for another reason: {messages[:2]}")

    def must_pass(self, workdir: Path, label: str, expect_employees: int,
                  **kwargs) -> dict:
        messages, written = self.attempt(workdir, label, **kwargs)
        if messages:
            self.check(False, label, f"refused it: {messages[:3]}")
            return {}
        self.check(
            written.get("employee") == expect_employees,
            label,
            f"wrote {written.get('employee')} employees, expected {expect_employees}",
        )
        return written


def main() -> int:
    gate = Gate()
    if not FIXTURE.exists():
        print(f"no fixture at {FIXTURE} — run tools/make_employee_fixture.py",
              file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)

        print("\n-- the fixture, mapped correctly")
        gate.must_pass(work, "clean fixture loads", 8)

        print("\n-- a mapping pointing at the wrong column")
        gate.must_fail(
            work, "employee_number pointed at the running-number column",
            "expected shape",
            columns={"employee_number": "A"},
        )
        gate.must_fail(
            work, "employee_number pointed at the name column", "expected shape",
            columns={"employee_number": "D", "name": "B"},
        )
        gate.must_fail(
            work, "section and name swapped", "is not a known section",
            columns={"section": "D", "name": "E"},
        )
        gate.must_fail(
            work, "active_from and name swapped", "does not fit date_format",
            columns={"active_from": "D", "name": "I"},
        )
        gate.must_fail(
            work, "name pointed at the remarks column", "name: is empty",
            columns={"name": "C"},
        )
        gate.must_fail(
            work, "two fields pointed at one column",
            "point at the same column",
            columns={"name": "E"},
        )

        print("\n-- the data itself")
        gate.must_fail(
            work, "a duplicate employee number", "is already used on row",
            edits=[(7, 2, "0090")],
        )
        gate.must_fail(
            work, "two numbers that pad to the same key", "the same as row",
            edits=[(7, 2, "90")], flags=("accept-odd-numbers",),
        )
        gate.must_fail(
            work, "a left date before the active date", "is before active_from",
            edits=[(8, 10, "01/01/2019")],
        )
        gate.must_fail(
            work, "a number that is not four digits", "expected shape",
            edits=[(5, 2, "090")],
        )
        gate.must_fail(
            work, "one PIN on two employees", "cannot belong to two employees",
            edits=[(6, 11, "90")],
        )
        gate.must_fail(
            work, "an empty name", "name: is empty",
            edits=[(9, 4, "")],
        )
        gate.must_fail(
            work, "a date that does not fit the stated format",
            "does not fit date_format",
            edits=[(9, 9, "March 2019")],
        )

        print("\n-- the mapping's own shape")
        gate.must_fail(
            work, "a sheet name that is not in the workbook", "has no sheet named",
            top={"sheet": '"Staff Listing"'},
        )
        gate.must_fail(
            work, "the data starting on the header row", "expected shape",
            top={"first_data_row": "4"},
        )
        gate.must_fail(
            work, "no last_data_row, so the footer is read", "is empty",
            top={"last_data_row": None},
        )
        gate.must_fail(
            work, "a required field left out of the mapping", "is missing",
            columns={"group": None},
        )
        gate.must_fail(
            work, "a column given as a header name instead of a letter",
            "is not a column letter or number",
            columns={"name": "Employee Name"},
        )
        gate.must_fail(
            work, "text dates with no date_format stated",
            "no date_format",
            top={"date_format": None},
        )

        print("\n-- vocabulary and reloading")
        gate.must_fail(
            work, "a new group without --allow-new group", "is not a known group",
            edits=[(5, 7, "GROUP-NOBODY-HAS-SEEN")], allow_new=(),
        )
        gate.must_fail(
            work, "--allow-new group does not also allow new sections",
            "is not a known section",
            edits=[(5, 5, "SECTION-NOBODY-HAS-SEEN")],
        )
        gate.must_fail(
            work, "loading a second time without --replace", "is already loaded",
            flags=("no-replace",), preload=True,
        )

        print("\n-- what an odd number does when it is accepted deliberately")
        written = gate.must_pass(
            work, "--accept-odd-numbers loads it", 8,
            edits=[(5, 2, "090")],
            flags=("accept-odd-numbers",),
        )
        if written:
            print("           stored verbatim, matched on the padded key")

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
