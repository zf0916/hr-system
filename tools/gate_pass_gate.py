#!/usr/bin/env python3
"""The gate for step 10, piece 5: the gate pass entry screen.

**Five claims, each proven by making it fail.**

  1. *The fields are in §5's order* — name / no. pekerja, emp no., date, out
     time, in time, category, reason, destination — read out of the laid-out
     page and compared against the order the server states.
  2. *An hours field is refused, at four depths.* Not on the page, not in the
     payload, not a parameter of either service function, and not accepted by
     the generated column underneath. The hours are read back after saving.
  3. *A department field is refused.* §5's form has no line for one; the
     employee's section is looked up and never transcribed.
  4. *An in time before an out time is refused* — a pass carries one date and
     two times, so it begins and ends on the same day.
  5. *A fifth category is refused.* The four ticks are rows, and a fifth is one
     nobody has printed on the paper.

**Every write goes to a throwaway database** created for the run and dropped at
the end (`tools/throwaway.py`), including the posts that are supposed to be
refused: a break that turns a refusal into an acceptance turns those posts into
gate passes, and nothing rebuilds one. Reads go to the interface that is
actually serving. Whatever can be proven inside a rolled-back transaction is.

    uv run python tools/gate_pass_gate.py
    uv run python tools/gate_pass_gate.py --no-dom

Exits non-zero if any deliberate mistake was accepted.
"""

from __future__ import annotations

import argparse
import ast
import html.parser
import inspect
import json
import pathlib
import sys
from decimal import Decimal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tools.guard_gate import Gate, post  # noqa: E402
from tools.leave_entry_gate import FormReader  # noqa: E402
from tools.screens_gate import (  # noqa: E402
    ask,
    computation_in,
    imports_of,
    module_source,
    rendered_dom,
    route_functions,
)

# Anything that would let a person state the hours rather than the two times
# they follow from.
HOURS_WORDS = ("hour", "hrs", "duration", "elapsed", "total_time")
# And anything that would put a department on a form that has none.
DEPARTMENT_WORDS = ("department", "dept", "section")

EMPLOYEE = "0090"

# Filling the form in a real browser and reading back what the page holds.
# React ignores a value assigned straight onto an input, so the native setter
# is called and an input event dispatched — the same event a keystroke makes.
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
    controls: [...document.querySelectorAll('input, select, textarea')]
      .map(e => [e.type, e.getAttribute('data-emp-no') !== null ? 'emp-no' : '',
                 e.name || '', e.id || '', e.placeholder || '',
                 e.getAttribute('aria-label') || ''].join(' ').trim()),
    name: (document.querySelector('[data-employee-name]')||{}).textContent,
    section: (document.querySelector('[data-section]')||{}).textContent || null,
    notGuard: (document.querySelector('[data-not-guard]')||{}).textContent || null,
    saved: (document.querySelector('[data-saved]')||{}).textContent || null,
    savedHours: (document.querySelector('[data-saved-hours]')||{}).textContent || null,
    error: (document.querySelector('[data-error]')||{}).textContent || null,
    saveDisabled: (document.querySelector('[data-save]')||{}).disabled,
  });
  const steps = {};

  document.querySelector('[data-typist="hr-aslida"]').click();
  await wait();
  steps.after_typist = read();

  set('[data-emp-no]', '__EMPLOYEE__');
  await wait(500);
  steps.after_number = read();

  set('[data-date]', '2026-08-19');
  set('[data-out]', '14:00');
  set('[data-in]', '16:30');
  await wait();
  steps.before_category = read();

  document.querySelector('[data-category="PERSONAL"]').click();
  set('[data-reason]', 'clinic appointment');
  set('[data-destination]', 'Klinik Melaka');
  await wait();
  steps.ready = read();

  document.querySelector('[data-save]').click();
  await wait(1000);
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
    from app.models import GatePass

    from tools.throwaway import Throwaway

    gate = Gate()

    def written() -> int:
        with Session() as session:
            return session.scalar(select(func.count()).select_from(GatePass))

    at_the_start = written()

    scratch = Throwaway()
    scratch.start()
    try:
        scratch.run("hr", "seed", "--add-missing")
        return run(gate, args, scratch, at_the_start)
    finally:
        scratch.stop()


def run(gate, args, scratch, at_the_start: int) -> int:
    """Every check, with the throwaway already up."""
    from sqlalchemy import func, select, text

    from app import gate_pass_entry as entry
    from app.db import Session
    from app.hr_entry import record_gate_pass
    from app.models import GatePass, GatePassCategory

    host, port = args.host, args.hr_port

    def written() -> int:
        with Session() as session:
            return session.scalar(select(func.count()).select_from(GatePass))

    def base(**changes) -> dict:
        pass_ = {"entered_by": "hr-aslida", "employee_number": EMPLOYEE,
                 "pass_date": "2026-08-19", "out_time": "14:00",
                 "in_time": "16:30", "category_code": "PERSONAL"}
        pass_.update(changes)
        return pass_

    print("\n-- the screen is rows: who types it, and the four ticks")
    screen = json.loads(ask(host, port, "GET", "/api/gatepass/screen")[1])
    with Session() as session:
        from app.hr_entry import categories, typists

        people, ticks = typists(session), categories(session)
        gate.check([one["code"] for one in screen["typists"]]
                   == [one.code for one in people],
                   f"the typists are the same HR rows the leave screen uses: "
                   f"{[one.name for one in people]}")
        gate.check([one["code"] for one in screen["categories"]]
                   == [one.code for one in ticks],
                   "the categories come from gate_pass_category rows")
        gate.check([one["code"] for one in screen["categories"]]
                   == ["OFFICIAL", "PERSONAL", "MEDICAL_TREATMENT", "OTHERS"],
                   "and they are the four ticks, in the form's order (SPEC §5)",
                   f"got {[one['code'] for one in screen['categories']]}")
        gate.check(len(ticks) == 4, "exactly four of them",
                   f"found {[one.code for one in ticks]}")

    print("\n-- 1. §5's field order, and no department among them")
    expected = [one["field"] for one in screen["form_order"]]
    gate.check(expected == ["name", "emp_no", "date", "out_time", "in_time",
                            "category", "reason", "destination"],
               f"the server states the paper's order: {expected}",
               f"it states {expected}")
    offending = [field for field in expected
                 if any(word in field for word in DEPARTMENT_WORDS)]
    gate.check(not offending, "and no department is one of them (SPEC §5)",
               f"found {offending}")

    # ---- 2. the hours ---------------------------------------------------
    print("\n-- 2. no hours, at four depths")
    from app.hr_app import GatePassEntry as Payload

    fields = set(Payload.model_fields)
    gate.check(fields == {"entered_by", "employee_number", "pass_date",
                          "out_time", "in_time", "category_code", "reason",
                          "destination"},
               f"the request model carries exactly the form's fields: "
               f"{sorted(fields)}",
               f"it carries {sorted(fields)}")
    gate.check(Payload.model_config.get("extra") == "forbid",
               "and refuses any field it does not name",
               f"extra is {Payload.model_config.get('extra')!r}")
    for words, what in ((HOURS_WORDS, "an hours figure"),
                        (DEPARTMENT_WORDS, "a department")):
        offending = {name for name in fields
                     if any(word in name for word in words)}
        gate.check(not offending, f"and none of them is {what}",
                   f"it carries {sorted(offending)}")

    for function in (entry.record, record_gate_pass):
        parameters = set(inspect.signature(function).parameters)
        for words, what in ((HOURS_WORDS, "hours"),
                            (DEPARTMENT_WORDS, "a department")):
            offending = {name for name in parameters
                         if any(word in name for word in words)}
            gate.check(not offending,
                       f"{function.__module__}.{function.__name__}() takes no "
                       f"{what}", f"it takes {sorted(offending)}")

    print("\n-- a crafted request carrying one is refused")
    before = written()
    for smuggled, value in (("hours", "2.5"), ("duration", "2.5"),
                            ("department", "PACK ASSY"), ("section", "QC")):
        status, body = post(*scratch.published, "/api/gatepass/entry",
                            base(**{smuggled: value}))
        gate.check(status == 422, f"a payload carrying {smuggled!r} is a 422",
                   f"got {status} {str(body)[:120]}")
    gate.check(written() == before, "and none of them wrote anything",
               f"gate_pass went from {before} to {written()}")

    # **The same constraint the CLI hits.** If a route were ever changed to
    # accept hours, this is what would still be standing.
    with Session() as session:
        from app.corrections import employee_by_number

        employee = employee_by_number(session, EMPLOYEE)
        try:
            session.execute(text(
                "INSERT INTO gate_pass (employee_id, pass_date, category_code, "
                "out_time, in_time, hours, entered_by) VALUES "
                "(:e, '2026-08-19', 'PERSONAL', '14:00', '16:30', 9.0, 'gate')"),
                {"e": employee.id})
            session.flush()
            gate.check(False, "the database refuses a row that states its hours",
                       "it accepted one")
        except Exception as exc:
            gate.check("non-DEFAULT value into column \"hours\"" in str(exc)
                       or "generated" in str(exc).lower(),
                       "the database refuses a row that states its hours, by "
                       "the generated column the CLI hits too",
                       f"refused by something else: {str(exc)[:160]}")
        session.rollback()
    with Session() as session:
        try:
            session.execute(text("SELECT department FROM gate_pass LIMIT 1"))
            gate.check(False, "gate_pass has no department column",
                       "it has one")
        except Exception as exc:
            gate.check("department" in str(exc),
                       "and gate_pass has no department column to put one in",
                       str(exc)[:120])
        session.rollback()

    print("\n-- the hours are read back from what the database stored")
    with Session() as session:
        row = entry.record(
            session, entered_by="hr-aslida", employee_number=EMPLOYEE,
            pass_date="2026-08-19", out_time="14:00", in_time="16:30",
            category_code="PERSONAL", reason="clinic appointment",
            destination="Klinik Melaka")
        stored = session.get(GatePass, row["id"])
        gate.check(stored.hours == Decimal("2.50"),
                   "14:00 to 16:30 is 2.50 hours, generated by the database",
                   f"it stored {stored.hours}")
        gate.check(row["hours"] == str(stored.hours),
                   "and the screen is handed exactly what was stored",
                   f"the screen is told {row['hours']!r}, the row holds "
                   f"{stored.hours}")
        gate.check(row["section"] == "PACK ASSY",
                   "the section is looked up and shown",
                   f"it says {row['section']!r}")
        gate.check(not hasattr(stored, "department"),
                   "and the row it wrote has no department on it")
        gate.check(stored.entered_by == "Aslida",
                   "the row says who typed it, by the screen_user's name",
                   f"it says {stored.entered_by!r}")
        session.rollback()

    # ---- 3, 4, 5: the refusals ------------------------------------------
    print("\n-- 4. an in time before an out time is refused")
    before = written()
    for out, back, why in (("16:30", "14:00", "an in time before the out time"),
                           ("14:00", "14:00", "an in time equal to it")):
        status, body = post(*scratch.published, "/api/gatepass/entry",
                            base(out_time=out, in_time=back))
        gate.check(status == 400, f"{why} is refused", f"got {status}")
        gate.check("not after" in str(body),
                   "   and the refusal says why", f"said {str(body)[:140]}")

    print("\n-- 5. a fifth category is refused")
    for code, why in (("SMOKE_BREAK", "a fifth tick nobody printed"),
                      ("", "no tick at all"),
                      ("PERSONAL_URGENT", "a near miss for one that exists")):
        status, body = post(*scratch.published, "/api/gatepass/entry",
                            base(category_code=code))
        gate.check(status == 400, f"{why} is refused", f"got {status}")
    gate.check("OFFICIAL" in str(body) and "MEDICAL_TREATMENT" in str(body),
               "and the refusal names the four that are",
               f"said {str(body)[:160]}")

    print("\n-- and the rest of what a form must say")
    for payload, why in (
        (base(entered_by="guard-1"), "a guard cannot type a gate pass"),
        (base(entered_by="nobody"), "nor can somebody not on the HR list"),
        (base(employee_number="9999999"), "an unknown employee number"),
        (base(pass_date="19/08/2026"), "a date that is not a date"),
        (base(out_time="2pm"), "a time that is not a time"),
    ):
        status, body = post(*scratch.published, "/api/gatepass/entry", payload)
        gate.check(status == 400, f"{why} is refused", f"got {status}")
    gate.check(written() == before, "none of them wrote anything",
               f"gate_pass moved to {written()}")

    # ---- the code, read rather than described ---------------------------
    print("\n-- the route hands the form on and computes nothing")
    import app.hr_app as hr_app_module

    tree = module_source(hr_app_module)
    routes = route_functions(tree)
    for name in ("gate_pass_screen", "gate_pass_look_up", "gate_pass_record"):
        gate.check(name in routes, f"{name}() is an API route")
        found = computation_in(routes[name]) if name in routes else ["missing"]
        gate.check(not found, "   and works nothing out", "; ".join(found))

    passed = set()
    for node in ast.walk(routes["gate_pass_record"]):
        if isinstance(node, ast.Call):
            passed |= {keyword.arg for keyword in node.keywords if keyword.arg}
    gate.check(passed == set(Payload.model_fields),
               f"and hands on exactly the form's fields: {sorted(passed)}",
               f"it passes {sorted(passed)}")

    # **No route previews the hours.** A figure shown before the row exists
    # would be a second place they are worked out, and §5 has only one.
    gate.check(not any("hour" in path.lower() for path in
                       (route.path for route in hr_app_module.hr_app.routes)),
               "no route offers the hours before the pass is saved")

    source = pathlib.Path(entry.__file__).read_text()
    for forbidden in ("session.delete", "DELETE FROM", "session.commit",
                      "in_time - out_time", "/ 3600"):
        gate.check(forbidden not in source,
                   f"app/gate_pass_entry.py contains no {forbidden!r}")
    gate.check("record_gate_pass" in imports_of(module_source(entry))
               or "app.hr_entry" in imports_of(module_source(entry)),
               "and it writes through app.hr_entry — the same function "
               "`hr gatepass add` calls")
    gate.check("GatePass(" not in source,
               "it never builds a gate pass row itself",
               "app/gate_pass_entry.py builds one")

    import app.cli_hr_entry as cli_hr_entry

    gate.check("record_gate_pass" in pathlib.Path(
                   cli_hr_entry.__file__).read_text(),
               "`hr gatepass add` calls the same function")

    page = pathlib.Path("ui/src/screens/GatePassEntry.jsx").read_text()
    payload_block = page.split("await send('/api/gatepass/entry', {")[1] \
                        .split("})")[0]
    for word in HOURS_WORDS + DEPARTMENT_WORDS:
        gate.check(word not in payload_block.lower(),
                   f"the page never sends {word!r}",
                   f"GatePassEntry.jsx puts it in the payload")
    for arithmetic in ("* 60", "/ 3600", "getTime()", "Date.parse"):
        gate.check(arithmetic not in page,
                   f"and does not work the hours out for itself ({arithmetic})")

    # ---- the page ------------------------------------------------------
    if args.no_dom:
        print("\n-- SKIPPED: the browser checks (--no-dom). What a payload "
              "refuses says nothing about what the page offers.")
    else:
        print("\n-- the page, before anybody has said who is typing")
        reader = FormReader()
        reader.feed(rendered_dom(args.dom_root + "/gatepass?"))
        offered = sorted(marker for marker in reader.markers
                         if marker.startswith("data-typist="))
        gate.check(offered == ["data-typist=hr-aisyah",
                               "data-typist=hr-aslida"],
                   f"a fresh browser is asked who is typing: {offered}",
                   f"it offers {offered}")

        print("\n-- and the same page, pressed rather than read")
        from tools.browser import Browser

        with Browser(width=1280, height=1100) as browser:
            browser.go(scratch.base + "/gatepass")
            steps = json.loads(browser.evaluate_raw(
                FILL.replace("__EMPLOYEE__", EMPLOYEE)))

        drawn = steps["after_typist"]
        gate.check(drawn["fields"] == expected,
                   f"the form's fields are in §5's order: {expected}",
                   f"the page draws {drawn['fields']}")

        # **Every control on the page**, by what a person would use to find it.
        # An hours box called anything at all fails here.
        for state, when in ((steps["after_typist"], "on a blank form"),
                            (steps["ready"], "with the form filled in"),
                            (steps["after_save"], "after saving")):
            offending = [control for control in state["controls"]
                         if any(word in control.lower()
                                for word in HOURS_WORDS + DEPARTMENT_WORDS)]
            gate.check(not offending,
                       f"nothing to type hours or a department into, {when} "
                       f"({len(state['controls'])} controls)",
                       f"found {offending}")

        found = steps["after_number"]
        gate.check(found["name"].strip() and found["name"].strip() != "—",
                   f"typing {EMPLOYEE} reads the name back: "
                   f"{found['name'].strip()!r}",
                   f"the page shows {found['name']!r}")
        gate.check("PACK ASSY" in (found["section"] or "")
                   and "not on this form" in (found["section"] or ""),
                   "and the section beside it, marked as looked up rather than "
                   "a field on the form",
                   f"it shows {found['section']!r}")

        guard_note = drawn["notGuard"] or ""
        gate.check("not the guard entry screen" in guard_note,
                   "the page says beside the time boxes that this is not the "
                   "guard entry path (SPEC §3, §5)",
                   f"it says {guard_note[:160]!r}")
        gate.check("stamped by the server" in guard_note,
                   "and says what the other one does instead",
                   f"it says {guard_note[:160]!r}")

        gate.check(steps["before_category"]["saveDisabled"] is True,
                   "Save is not available until a tick is chosen",
                   "it was available with no category")
        gate.check(steps["ready"]["saveDisabled"] is False,
                   "and is once one is",
                   "it was still unavailable")
        gate.check(not steps["ready"]["saved"],
                   "and nothing is recorded until it is pressed")

        saved = steps["after_save"]
        gate.check(not saved["error"], "Save was accepted",
                   f"the page says {saved['error']!r}")
        gate.check(saved["savedHours"] == "2.50",
                   "and 14:00 to 16:30 comes back as 2.50 hours, from the "
                   "column the database generated",
                   f"it says {saved['savedHours']!r}")
        gate.check("Klinik Melaka" in (saved["saved"] or ""),
                   "with the destination that was typed",
                   f"it says {(saved['saved'] or '')[:200]!r}")
        gate.check("looked up" in (saved["saved"] or ""),
                   "and the section, still marked as looked up",
                   f"it says {(saved['saved'] or '')[:200]!r}")

        listed = scratch.run("hr", "gatepass", "list", "--from", "2026-08-01",
                             "--to", "2026-08-31")
        gate.check("2.50" in listed and "Aslida" in listed
                   and "PERSONAL" in listed,
                   "and the row reads back the same from the command line",
                   listed[-300:])
        gate.check("derived from its two times" in listed,
                   "which says where the hours came from", listed[-200:])

    gate.check(written() == at_the_start,
               f"and this gate wrote nothing: gate_pass is still "
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
