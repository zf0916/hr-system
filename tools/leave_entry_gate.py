#!/usr/bin/env python3
"""The gate for step 10, piece 4: the leave entry screen.

**Four claims, each proven by making it fail.**

  1. *The fields are in §6's order* — read out of the laid-out page and
     compared against the order the server states, not against the JSX.
  2. *A recomputed day count is refused.* The count is typed. Nothing in the
     entry path subtracts the two dates, the one function that counts a range
     is not reachable from the one that writes, and a count that disagrees with
     its range is stored exactly as typed.
  3. *The A48 suggestion is present, overridable, and absent for the four types
     the legend has no letter for* — checked by pressing the ticks in a
     browser and reading what the code box then holds.
  4. *A code typed with no type, and a type with no code, both save.* Either
     field may be empty; neither is filled in from the other.

**Where the writes happen, and why they happen there.** Everything that can be
proven inside a transaction is, and that transaction is rolled back — the
working database holds leave records nothing can rebuild, and §13 forbids
deleting one.

**Nothing this gate sends over HTTP reaches the working database.** Every
`POST` — including the ones that are supposed to be refused — goes to a
throwaway database created for the run and dropped at the end
(`tools/throwaway.py`). A refusal that stops being a refusal is exactly what a
deliberate mistake produces, and then those posts write: this gate wrote three
leave records against a real employee the first time one of its breaks was
run, and nothing may delete them (CLAUDE.md, BUILD.md). Reads still go to the
interface that is actually serving, because a read costs nothing and asking the
live one is the point.

    uv run python tools/leave_entry_gate.py
    uv run python tools/leave_entry_gate.py --no-dom

Exits non-zero if any deliberate mistake was accepted.
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import html.parser
import inspect
import json
import pathlib
import sys
from decimal import Decimal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tools.guard_gate import Gate, post  # noqa: E402
from tools.screens_gate import (  # noqa: E402
    ask,
    computation_in,
    imports_of,
    module_source,
    rendered_dom,
    route_functions,
)

# Anything on this screen that would mean approval, entitlement or a balance —
# all three are Milestone 5 and none of them is designed (SPEC §6).
MILESTONE_5 = ("approve", "approved", "approval", "entitle", "entitlement",
               "balance", "verified_by", "signature", "signed")

# The employee the demonstration types a form for.
EMPLOYEE = "0090"


class FormReader(html.parser.HTMLParser):
    """The form's fields in the order the page puts them, and its controls."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fields: list[str] = []
        self.inputs: list[dict] = []
        self.marks: dict[str, str] = {}
        self.markers: list[str] = []
        self._open: str | None = None
        self._tag: str | None = None
        self._depth = 0
        self._text: list[str] = []

    MARKED = ("data-count-note", "data-not-here", "data-saved", "data-error",
              "data-department", "data-employee-name")

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "data-form-field" in attributes:
            self.fields.append(attributes["data-form-field"])
        if tag in ("input", "select", "textarea"):
            self.inputs.append({"tag": tag, **attributes})
        for key in attributes:
            if key.startswith("data-"):
                self.markers.append(f"{key}={attributes[key] or 'yes'}")
        if self._open is not None and tag == self._tag:
            self._depth += 1
        for key in self.MARKED:
            if key in attributes:
                self._open, self._tag, self._depth = key, tag, 0
                self._text = []

    def handle_data(self, data):
        if self._open is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if self._open is None:
            return
        if tag == self._tag and self._depth:
            self._depth -= 1
            return
        if tag == self._tag:
            self.marks[self._open] = "".join(self._text).strip()
            self._open = self._tag = None
            self._text = []


# The script that fills the form in a real browser and reads back what the page
# then holds. React ignores a value assigned straight onto an input, so the
# native setter is called and an input event dispatched — the same event a
# keystroke produces.
FILL = """
(async () => {
  const wait = (ms) => new Promise(r => setTimeout(r, ms || 260));
  const set = (selector, value) => {
    const el = document.querySelector(selector);
    const proto = el.tagName === 'SELECT'
      ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
  };
  const read = () => ({
    fields: [...document.querySelectorAll('[data-form-field]')]
      .map(e => e.getAttribute('data-form-field')),
    name: (document.querySelector('[data-employee-name]')||{}).textContent,
    department: (document.querySelector('[data-department]')||{}).textContent,
    code: (document.querySelector('[data-sheet-code]')||{}).value,
    suggested: (document.querySelector('[data-suggested]')||{})
      .getAttribute ? document.querySelector('[data-suggested]')
      .getAttribute('data-suggested') : null,
    noSuggestion: document.querySelector('[data-no-suggestion]')
      ? document.querySelector('[data-no-suggestion]')
        .getAttribute('data-no-suggestion') : null,
    days: (document.querySelector('[data-days]')||{}).value,
    countNote: (document.querySelector('[data-count-note]')||{}).textContent,
    saved: (document.querySelector('[data-saved]')||{}).textContent || null,
    error: (document.querySelector('[data-error]')||{}).textContent || null,
  });
  const steps = {};

  document.querySelector('[data-typist="hr-aisyah"]').click();
  await wait();
  steps.after_typist = read();

  set('[data-staff-no]', '__EMPLOYEE__');
  await wait(500);
  steps.after_number = read();

  document.querySelector('[data-type="ANNUAL"]').click();
  await wait();
  steps.after_annual = read();

  document.querySelector('[data-type="MATERNITY"]').click();
  await wait();
  steps.after_maternity = read();

  document.querySelector('[data-type="ANNUAL"]').click();
  await wait();
  set('[data-sheet-code]', 'EL');
  await wait();
  steps.after_override = read();

  set('[data-applied]', '2026-07-20');
  set('[data-from]', '2026-08-07');
  set('[data-to]', '2026-08-08');
  set('[data-days]', '1.5');
  await wait(700);
  steps.after_dates = read();

  document.querySelector('[data-save]').click();
  await wait(900);
  steps.after_save = read();

  return JSON.stringify(steps);
})()
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--hr-port", type=int, default=8090)
    parser.add_argument("--dom-root", default="http://api:8100")
    parser.add_argument("--no-dom", action="store_true")
    args = parser.parse_args()

    from sqlalchemy import func, select

    from app.db import Session
    from app.models import LeaveRecord

    from tools.throwaway import Throwaway

    gate = Gate()

    def written() -> int:
        with Session() as session:
            return session.scalar(select(func.count()).select_from(LeaveRecord))

    at_the_start = written()

    # **Everything that writes goes here, and it is dropped at the end.**
    # Opened before the first check rather than around the browser only: a
    # `POST` that is supposed to be refused is still a `POST`, and a break that
    # makes it succeed writes a leave record nothing can rebuild.
    scratch = Throwaway()
    scratch.start()
    try:
        scratch.run("hr", "seed", "--add-missing")
        return run(gate, args, scratch, at_the_start)
    finally:
        scratch.stop()


def run(gate, args, scratch, at_the_start: int) -> int:
    """Every check, with the throwaway already up.

    `scratch` is where every write goes. Reads go to `host:port`, the interface
    that is actually serving.
    """
    from sqlalchemy import func, select

    from app import guard as guard_module
    from app import leave_entry as entry
    from app.db import Session
    from app.models import LeaveRecord, ScreenUser

    host, port = args.host, args.hr_port

    def written() -> int:
        with Session() as session:
            return session.scalar(select(func.count()).select_from(LeaveRecord))

    # ---- who types it ---------------------------------------------------
    print("\n-- who is typing comes from screen_user rows, HR's side")
    screen = json.loads(ask(host, port, "GET", "/api/leave/screen")[1])
    with Session() as session:
        people = entry.typists(session)
        gate.check([one["code"] for one in screen["typists"]]
                   == [one.code for one in people],
                   f"the typists are rows: "
                   f"{[one.name for one in people]}")
        gate.check([one.name for one in people] == ["Aisyah", "Aslida"],
                   "and they are Aisyah and Aslida",
                   f"they are {[one.name for one in people]}")
        gate.check(not any(one.provisional for one in people),
                   "neither is provisional — these are real names, not "
                   "placeholders, and the screen does not warn about them",
                   f"{[(one.name, one.provisional) for one in people]}")
        gate.check(all(one.screen == "hr" for one in people),
                   "all of them on the HR side of the same table the guard "
                   "screen picks from (SPEC §3)")
        guards = {one.code for one in guard_module.on_duty(session)}
        gate.check(not guards & {one.code for one in people},
                   "and no code is on both lists")

    print("\n-- and somebody not on that list cannot type one")
    before = written()
    for who in ("guard-1", "", "aisyah", "not-a-person"):
        status, body = post(*scratch.published, "/api/leave/entry", {
            "entered_by": who, "employee_number": EMPLOYEE,
            "period_from": "2026-08-07", "period_to": "2026-08-07",
            "days": "1", "leave_type_code": "ANNUAL"})
        gate.check(status == 400, f"{who!r} is refused", f"got {status}")
    gate.check("hr-aisyah" in str(body), "and the refusal names who is",
               f"said {str(body)[:140]}")
    gate.check(written() == before, "none of them wrote anything",
               f"leave_record moved to {written()}")

    # ---- the payload ----------------------------------------------------
    print("\n-- the payload names its fields, and nothing else")
    from app.hr_app import LeaveEntry as Payload

    fields = set(Payload.model_fields)
    gate.check(fields == {"entered_by", "employee_number", "period_from",
                          "period_to", "days", "date_of_application",
                          "leave_type_code", "sheet_code", "reason"},
               f"the request model carries exactly the form's fields: "
               f"{sorted(fields)}",
               f"it carries {sorted(fields)}")
    gate.check(Payload.model_config.get("extra") == "forbid",
               "and refuses any field it does not name, rather than dropping it",
               f"extra is {Payload.model_config.get('extra')!r}")
    offending = {name for name in fields
                 if any(word in name for word in MILESTONE_5)}
    gate.check(not offending,
               "none of them is an approval, an entitlement or a balance",
               f"it carries {sorted(offending)}")

    print("\n-- a crafted request carrying one of those is refused")
    before = written()
    for smuggled, value in (("sql_account_code", "PD01"),
                            ("days_spanned", "2"),
                            ("approved_by", "Head of Dept"),
                            ("entitlement", "14"),
                            ("balance", "6")):
        status, body = post(*scratch.published, "/api/leave/entry", {
            "entered_by": "hr-aisyah", "employee_number": EMPLOYEE,
            "period_from": "2026-08-07", "period_to": "2026-08-07",
            "days": "1", "leave_type_code": "ANNUAL", smuggled: value})
        gate.check(status == 422, f"a payload carrying {smuggled!r} is a 422",
                   f"got {status} {str(body)[:120]}")
    gate.check(written() == before, "and none of them wrote anything",
               f"leave_record went from {before} to {written()}")

    # ---- 2. the day count is typed, never derived -----------------------
    print("\n-- 2. the number of days is typed and nothing derives it")
    signature = inspect.signature(entry.record)
    gate.check("days" in signature.parameters,
               "leave_entry.record takes the count as an argument")
    gate.check(signature.parameters["days"].default is inspect.Parameter.empty,
               "and it is required — nothing defaults it to a span")
    source = inspect.getsource(entry.record)
    gate.check("period_to - period_from" not in source
               and "- period_from" not in source and ".days + 1" not in source,
               "and nothing in it subtracts the two dates",
               "the range is being counted inside leave_entry.record")
    # **Read out of the calls, not the prose.** The docstring above `record`
    # says it does not reach `range_check`; a substring search agrees with the
    # sentence rather than with the code, and would have passed a function that
    # called it on the next line.
    called = {node.func.id if isinstance(node.func, ast.Name)
              else getattr(node.func, "attr", "")
              for node in ast.walk(ast.parse(inspect.getsource(entry.record)))
              if isinstance(node, ast.Call)}
    gate.check("range_check" not in called,
               "and it cannot reach the one function that counts a range",
               f"leave_entry.record calls {sorted(called)}")
    check_signature = set(inspect.signature(entry.range_check).parameters)
    gate.check("days" in check_signature,
               "range_check is given the typed count rather than producing one")
    gate.check(not any(word in inspect.getsource(entry.range_check)
                       for word in ("record_leave", "session.add", "LeaveRecord")),
               "and it writes nothing at all",
               "range_check touches the record")

    # The one place that counts a range, doing its job: both numbers, and the
    # sentence saying which one is the form's.
    with Session() as session:
        differing = entry.range_check(session, "2026-08-07", "2026-08-08", "1.5")
        gate.check(differing["counts_differ"] is True
                   and differing["days_stated"] == "1.5"
                   and differing["days_spanned"] == 2,
                   "1.5 typed over a 2-day range is shown as both numbers",
                   f"got {differing}")
        gate.check("the form's number is the one that counts"
                   in differing["note"].lower(),
                   "and the screen says which one counts — the same words the "
                   "day detail screen already uses",
                   f"it says {differing['note']!r}")
        agreeing = entry.range_check(session, "2026-08-07", "2026-08-07", "1")
        gate.check(agreeing["counts_differ"] is False,
                   "a count that matches its range is not called a difference")

    print("\n-- and the count that disagrees is stored exactly as typed")
    with Session() as session:
        row = entry.record(
            session, entered_by="hr-aisyah", employee_number=EMPLOYEE,
            period_from="2026-08-07", period_to="2026-08-09", days="1.5",
            date_of_application="2026-07-20", leave_type_code="ANNUAL",
            sheet_code="AL")
        stored = session.get(LeaveRecord, row["id"])
        gate.check(stored.days == Decimal("1.5"),
                   "1.5 days over a 3-day range is stored as 1.5",
                   f"it was stored as {stored.days}")
        gate.check(row["days"] == "1.5",
                   "and the screen is told 1.5 back", f"it is told {row['days']}")
        gate.check(stored.sql_account_code is None,
                   "the SQL Account code stays empty (SPEC §8)",
                   f"it is {stored.sql_account_code!r}")
        gate.check(stored.entered_by == "Aisyah",
                   "and the row says who typed it, by the screen_user's name",
                   f"it says {stored.entered_by!r}")
        session.rollback()

    print("\n-- a half day is a fraction, and a quarter is not a day")
    with Session() as session:
        half = entry.record(
            session, entered_by="hr-aslida", employee_number=EMPLOYEE,
            period_from="2026-08-10", period_to="2026-08-10", days="0.5",
            leave_type_code="ANNUAL", sheet_code="AL")
        gate.check(half["days"] == "0.5", "0.5 is stored as 0.5 (SPEC §9 A15)",
                   f"got {half['days']}")
        session.rollback()
    with Session() as session:
        try:
            entry.record(session, entered_by="hr-aisyah",
                         employee_number=EMPLOYEE, period_from="2026-08-10",
                         period_to="2026-08-10", days="0.25",
                         leave_type_code="ANNUAL")
            gate.check(False, "a quarter day is refused", "it was accepted")
        except ValueError as exc:
            gate.check("whole or half day" in str(exc),
                       "a quarter day is refused", str(exc)[:120])
        session.rollback()

    before = written()
    for days, why in (("", "an empty count"), ("0", "zero days"),
                      ("two", "a count that is not a number")):
        status, body = post(*scratch.published, "/api/leave/entry", {
            "entered_by": "hr-aisyah", "employee_number": EMPLOYEE,
            "period_from": "2026-08-07", "period_to": "2026-08-08",
            "days": days, "leave_type_code": "ANNUAL"})
        gate.check(status in (400, 422), f"{why} is refused", f"got {status}")
    status, _ = post(*scratch.published, "/api/leave/entry", {
        "entered_by": "hr-aisyah", "employee_number": EMPLOYEE,
        "period_from": "2026-08-07", "period_to": "2026-08-08",
        "leave_type_code": "ANNUAL"})
    gate.check(status == 422, "and a payload with no count at all is refused",
               f"got {status}")
    gate.check(written() == before, "none of them wrote anything",
               f"leave_record moved to {written()}")

    # ---- 3. A48 ---------------------------------------------------------
    print("\n-- 3. the suggestion is offered for three types and no others")
    suggested = {one["code"]: one["suggested_sheet_code"]
                 for one in screen["types"]}
    gate.check(len(suggested) == 7, "seven ticks on the form",
               f"got {sorted(suggested)}")
    gate.check([one["code"] for one in screen["types"]]
               == ["ANNUAL", "COMPASSIONATE", "HOSPITALIZATION", "SOCSO",
                   "SICK", "MATERNITY", "UNPAID"],
               "in the form's own order (SPEC §6)",
               f"got {[one['code'] for one in screen['types']]}")
    gate.check(suggested.get("ANNUAL") == "AL" and suggested.get("SICK") == "MC"
               and suggested.get("UNPAID") == "UL",
               "three carry a suggested code (A48)", f"got {suggested}")
    without = sorted(code for code, value in suggested.items() if value is None)
    gate.check(without == ["COMPASSIONATE", "HOSPITALIZATION", "MATERNITY",
                           "SOCSO"],
               f"and four carry none, because the legend has none: {without}",
               f"got {without}")
    gate.check(len(screen["codes"]) == 9,
               "the nine legend codes are rows the screen is given",
               f"got {len(screen['codes'])}")

    # ---- 4. either field may be empty -----------------------------------
    print("\n-- 4. a type with no code, and a code with no type, both save")

    def saves(what: str, field: str, expected, **arguments) -> None:
        """One record that must save, and what it must hold afterwards.

        **A refusal is reported, not raised.** A break here refuses rather than
        storing the wrong thing, and a gate that dies on the first one says
        nothing about the rest.
        """
        with Session() as session:
            try:
                row = entry.record(
                    session, entered_by="hr-aisyah",
                    employee_number=EMPLOYEE, **arguments)
            except ValueError as exc:
                gate.check(False, what, f"it was refused: {str(exc)[:140]}")
                return
            finally:
                session.rollback()
        gate.check(row[field] == expected, what,
                   f"it stored {field}={row[field]!r}, wanted {expected!r}")

    saves("Maternity with no code saves, and stores no code",
          "sheet_code", None,
          period_from="2026-08-11", period_to="2026-08-11", days="1",
          leave_type_code="MATERNITY", sheet_code=None)
    saves("EL with no tick saves, and stores no type — nobody applies for "
          "emergency leave under that name (SPEC §6)",
          "leave_type_code", None,
          period_from="2026-08-12", period_to="2026-08-12", days="1",
          leave_type_code=None, sheet_code="EL")
    saves("and an overridden suggestion is stored as typed, not as suggested "
          "— A48 is an offer, not a mapping",
          "sheet_code", "EL",
          period_from="2026-08-13", period_to="2026-08-13", days="1",
          leave_type_code="ANNUAL", sheet_code="EL")

    with Session() as session:
        try:
            entry.record(session, entered_by="hr-aisyah",
                         employee_number=EMPLOYEE, period_from="2026-08-14",
                         period_to="2026-08-14", days="1")
            gate.check(False, "but a record that says neither is refused",
                       "it was accepted")
        except ValueError as exc:
            gate.check("says what it is" in str(exc),
                       "but a record that says neither is refused",
                       str(exc)[:120])
        session.rollback()

    # ---- the sheet shows it ---------------------------------------------
    print("\n-- and the sheet shows it, with nothing else run")
    from app import sheet as sheet_view
    from app.corrections import employee_by_number

    with Session() as session:
        employee = employee_by_number(session, EMPLOYEE)
        entry.record(session, entered_by="hr-aisyah",
                     employee_number=EMPLOYEE, period_from="2026-08-18",
                     period_to="2026-08-18", days="1",
                     leave_type_code="ANNUAL", sheet_code="AL")
        drawn = sheet_view.render(session, dt.date(2026, 8, 1),
                                  dt.date(2026, 8, 31))
        cell = drawn.cells.get((employee.id, dt.date(2026, 8, 18)))
        gate.check(cell is not None and cell.leave_code == "AL",
                   "the August cell carries the code the form was given",
                   f"the cell is {cell}")
        gate.check(cell is not None and cell.text == "AL",
                   "and prints it", f"it prints {cell.text if cell else None!r}")
        session.rollback()

    # ---- the code, read rather than described ---------------------------
    print("\n-- the route hands the form on and computes nothing")
    import app.hr_app as hr_app_module

    tree = module_source(hr_app_module)
    routes = route_functions(tree)
    for name in ("leave_screen", "leave_look_up", "leave_range_check",
                 "leave_record"):
        gate.check(name in routes, f"{name}() is an API route")
        found = computation_in(routes[name]) if name in routes else ["missing"]
        gate.check(not found, "   and works nothing out", "; ".join(found))

    passed = set()
    for node in ast.walk(routes["leave_record"]):
        if isinstance(node, ast.Call):
            passed |= {keyword.arg for keyword in node.keywords if keyword.arg}
    gate.check(passed == set(Payload.model_fields),
               f"and hands on exactly the form's fields: {sorted(passed)}",
               f"it passes {sorted(passed)}")
    gate.check("range_check" not in ast.dump(routes["leave_record"]),
               "the writing route cannot reach the range count either")

    entry_source = pathlib.Path(entry.__file__).read_text()
    for forbidden in ("session.delete", "DELETE FROM", "session.commit",
                      "def approve", "def undo"):
        gate.check(forbidden not in entry_source,
                   f"app/leave_entry.py contains no {forbidden!r}")
    gate.check("record_leave" in imports_of(module_source(entry))
               or "app.hr_entry" in imports_of(module_source(entry)),
               "and it writes through app.hr_entry — the same function "
               "`hr leave add` calls")
    gate.check("LeaveRecord" not in entry_source,
               "it never builds a leave row itself",
               "app/leave_entry.py mentions LeaveRecord")
    gate.check("sql_account" not in entry_source.replace(
                   "sql_account_code\": row.sql_account_code", ""),
               "and the SQL Account code is read back, never set",
               "app/leave_entry.py sets an SQL Account code")

    import app.cli_hr_entry as cli_hr_entry

    gate.check("record_leave" in pathlib.Path(cli_hr_entry.__file__).read_text(),
               "`hr leave add` calls the same function")

    page = pathlib.Path("ui/src/screens/LeaveEntry.jsx").read_text()
    for word in ("entitlement", "balance", "approved"):
        gate.check(word not in page,
                   f"the page's source has no {word!r} anywhere in it")
    # The page prints the SQL Account code back — empty, and labelled as such,
    # because a field that is deliberately blank is worth showing blank. What
    # it must not do is offer somewhere to put one, or send one.
    gate.check("sql_account_code:" not in page,
               "the page never sends an SQL Account code",
               "LeaveEntry.jsx puts one in the payload")
    gate.check("data-sql" not in page and "sql_account" not in page.split(
                   "const save")[0],
               "and has no control for one above the save",
               "LeaveEntry.jsx offers somewhere to type one")
    for arithmetic in ("Date.parse", "getTime()", "days_spanned)"):
        gate.check(arithmetic not in page,
                   f"and does not work a range out for itself ({arithmetic})")

    # ---- the page ------------------------------------------------------
    if args.no_dom:
        print("\n-- SKIPPED: the browser checks (--no-dom). What a payload "
              "accepts says nothing about the order the page prints.")
    else:
        print("\n-- the page, before anybody has said who is typing")
        reader = FormReader()
        reader.feed(rendered_dom(args.dom_root + "/leave?"))
        expected = [one["field"] for one in screen["form_order"]]
        offered = sorted(marker for marker in reader.markers
                         if marker.startswith("data-typist="))
        gate.check(offered == ["data-typist=hr-aisyah",
                               "data-typist=hr-aslida"],
                   f"a fresh browser is asked who is typing, and offered the "
                   f"two on the HR list: {offered}",
                   f"it offers {offered}")
        gate.check(not reader.fields,
                   "and is given no form to fill in until it is answered — "
                   "every leave record says who entered it (SPEC §6)",
                   f"it draws {reader.fields} already")

        print("\n-- and the same page, pressed rather than read")
        from tools.browser import Browser

        # **The same throwaway.** Pressing Save commits in another process and
        # no rollback here reaches it (CLAUDE.md).
        with Browser(width=1280, height=1000) as browser:
            browser.go(scratch.base + "/leave")
            steps = json.loads(browser.evaluate_raw(
                FILL.replace("__EMPLOYEE__", EMPLOYEE)))

        after_typist = steps["after_typist"]
        gate.check(after_typist["fields"] == expected,
                   f"the form's fields are in §6's order: {expected}",
                   f"the page draws {after_typist['fields']}")

        found = steps["after_number"]
        gate.check(EMPLOYEE and found["name"].strip()
                   and found["name"].strip() != "—",
                   f"typing {EMPLOYEE} reads the name back: "
                   f"{found['name'].strip()!r}",
                   f"the page shows {found['name']!r}")
        gate.check("PACK ASSY" in (found["department"] or ""),
                   "and the department, off the assignment row",
                   f"it shows {found['department']!r}")

        annual = steps["after_annual"]
        gate.check(annual["code"] == "AL",
                   "pressing Annual offers AL in the code box (A48)",
                   f"the box holds {annual['code']!r}")
        gate.check(annual["suggested"] == "AL",
                   "and the page says it is a suggestion",
                   f"it says {annual['suggested']!r}")
        gate.check(annual["noSuggestion"] is None,
                   "and does not also claim there is none")

        maternity = steps["after_maternity"]
        gate.check(maternity["code"] == "",
                   "pressing Maternity offers nothing at all — the legend "
                   "has no letter for it (SPEC §6)",
                   f"the box holds {maternity['code']!r}")
        gate.check(maternity["suggested"] is None,
                   "and no suggestion is shown",
                   f"it suggests {maternity['suggested']!r}")
        gate.check(maternity["noSuggestion"] == "MATERNITY",
                   "the page says so rather than leaving a blank box "
                   "unexplained",
                   f"it says {maternity['noSuggestion']!r}")

        override = steps["after_override"]
        gate.check(override["code"] == "EL",
                   "the suggestion is overridable — the box takes EL and "
                   "keeps it",
                   f"the box holds {override['code']!r}")
        gate.check(override["suggested"] == "AL",
                   "and the page still says what was suggested, so a "
                   "reader can see it was overridden",
                   f"it says {override['suggested']!r}")

        dated = steps["after_dates"]
        gate.check(dated["days"] == "1.5",
                   "the day count is still what was typed after the dates "
                   "were filled in — nothing recomputed it",
                   f"the box holds {dated['days']!r}")
        note = dated["countNote"] or ""
        gate.check("1.5" in note and "2-day range" in note,
                   "and both numbers are shown where they disagree",
                   f"the page says {note!r}")
        gate.check("the form's number is the one that counts"
                   in note.lower(),
                   "with the form's number named as the one that counts",
                   f"the page says {note!r}")

        saved = steps["after_save"]
        gate.check(not saved["error"], "Save was accepted",
                   f"the page says {saved['error']!r}")
        gate.check(saved["saved"] and "1.5 day" in saved["saved"],
                   "and the recorded line says 1.5 days",
                   f"it says {(saved['saved'] or '')[:160]!r}")
        gate.check(saved["saved"] and "EL" in saved["saved"],
                   "with the code that was typed, not the one suggested",
                   f"it says {(saved['saved'] or '')[:160]!r}")
        gate.check(saved["saved"] and "empty (SPEC §8)" in saved["saved"],
                   "and the SQL Account code empty",
                   f"it says {(saved['saved'] or '')[:160]!r}")

        # **What the throwaway's own database now holds**, read rather
        # than believed, and then the sheet drawn from it with nothing
        # else run.
        stored = json.loads(ask(*scratch.published, "GET",
                                "/api/sheet?month=2026-08")[1])
        row = next((one for one in stored["rows"]
                    if one["employee_number"] == EMPLOYEE), None)
        gate.check(row is not None, f"{EMPLOYEE} is on the August sheet")
        cells = [stored["cells"].get(f"{row['employee_id']}:2026-08-0{day}")
                 for day in (7, 8)] if row else []
        gate.check(all(cell and cell["leave_code"] == "EL"
                       for cell in cells),
                   "and both days of the range carry EL in the August "
                   "cells, with nothing else run",
                   f"the cells are {cells}")
        gate.check(cells and cells[0]["text"] == "EL",
                   "printed as the code",
                   f"the first prints {cells[0]['text'] if cells else None!r}")

        # The typed 1.5 is on the record, and the two cells it marks are a
        # different fact from the count — the span and the count differ on
        # purpose (SPEC §6).
        listed = scratch.run("hr", "leave", "list", "--from", "2026-08-01",
                             "--to", "2026-08-31")
        gate.check("1.50" in listed and "Aisyah" in listed,
                   "the record says 1.5 days, typed by Aisyah",
                   listed[-300:])

        print("\n-- nothing on this screen approves, entitles or balances")
        reader = FormReader()
        reader.feed(rendered_dom(args.dom_root + "/leave?"))
        offending = [control for control in reader.inputs
                     if any(word in json.dumps(control).lower()
                            for word in MILESTONE_5)]
        gate.check(not offending, "the page offers no control for one",
                   f"found {offending}")
        offending = [marker for marker in reader.markers
                     if "sql" in marker.lower()]
        gate.check(not offending, "and none for the SQL Account code",
                   f"found {offending}")

    # **The gate leaves the working database as it found it.** Everything it
    # wrote here went into a transaction it rolled back; the one write it could
    # not roll back happened somewhere that no longer exists.
    gate.check(written() == at_the_start,
               f"and this gate wrote nothing: leave_record is still "
               f"{at_the_start} rows",
               f"it went from {at_the_start} to {written()} — the gate is "
               f"leaving forms behind, and nothing rebuilds one")

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
