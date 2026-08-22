#!/usr/bin/env python3
"""The gate for step 10, piece 6: the HR corrections screen.

**Four claims, each proven by making it fail.**

  1. *A cancellation that edits or deletes the original is refused.* The
     database refuses both — an `UPDATE` that changes anything a person
     recorded, and a `DELETE` of any kind — and the punch's every recorded
     column is compared before and after a cancellation to show it did not
     move. Only `attendance_day` and `schedule_id` stay rebuildable, because
     they are derived.
  2. *A cancelled punch still counted in the daily row fails.* The day is
     rebuilt after a cancellation, first in and last out stop counting it, and
     the row records how many it left out. The per-day detail still shows the
     punch, marked cancelled — a punch that vanishes is indistinguishable from
     one that never happened.
  3. *A device punch offered for cancellation fails.* The list is read from
     `manual_punch` and no other table, and an id that is not a manual punch is
     refused by name.
  4. *The guard path reachable from an HR screen fails.* Two screens, two
     service modules, two payloads: this one has a time and the guard's cannot
     have one. The HR screen does not import, link to, or post to the guard's,
     and the guard's page carries none of the HR chrome.

**Every write goes to a throwaway database** created for the run and dropped at
the end (`tools/throwaway.py`) — including the posts that must be refused, and
including the deliberate mistakes, which is the rule for a write path
(CLAUDE.md). Reads go to the interface that is actually serving.

    uv run python tools/corrections_screen_gate.py
    uv run python tools/corrections_screen_gate.py --no-dom

Exits non-zero if any deliberate mistake was accepted.
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import inspect
import json
import pathlib
import re
import sys

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

EMPLOYEE = "0090"
# **Two days, because the interesting cases are different.** The demonstration
# punches stop at the end of August, so a September day has nothing on it at
# all — that is where a cancelled punch has to leave the day empty. And a day
# that already has device punches is where a cancelled correction has to
# disappear from the figures *without* taking the device punches with it.
CLEAR_DAY = "2026-09-15"
BUSY_DAY = "2026-08-27"
DAY = CLEAR_DAY

# The columns a person recorded. None of them may move, ever.
RECORDED = ("employee_id", "path", "recorded_at", "asserted_time",
            "reason_code", "reason", "made_by", "note")
# The two that are derived from the schedule and are rebuilt when it changes.
DERIVED = ("attendance_day", "schedule_id")

FILL = """
(async () => {
  const wait = (ms) => new Promise(r => setTimeout(r, ms || 300));
  const set = (selector, value) => {
    const el = document.querySelector(selector);
    Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype, 'value').set.call(el, value);
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
  };
  const read = () => ({
    added: (document.querySelector('[data-added]')||{}).textContent || null,
    cancelled: (document.querySelector('[data-cancel-done]')||{}).textContent || null,
    unchanged: (document.querySelector('[data-punch-unchanged]')||{}).textContent || null,
    notGuard: (document.querySelector('[data-not-guard]')||{}).textContent || null,
    cannotUndo: (document.querySelector('[data-cannot-undo]')||{}).textContent || null,
    onlyManual: (document.querySelector('[data-only-manual]')||{}).textContent || null,
    rows: [...document.querySelectorAll('[data-correction]')].map(e => ({
      id: e.getAttribute('data-correction'),
      cancelled: e.getAttribute('data-cancelled'),
      text: e.textContent.trim().slice(0, 120),
    })),
    guardLinks: [...document.querySelectorAll('a, button')]
      .filter(e => (e.getAttribute('href')||'').includes('/guard')
                || (e.textContent||'').toLowerCase().includes('guard entry'))
      .map(e => e.outerHTML.slice(0, 80)),
    error: (document.querySelector('[data-error]')||{}).textContent || null,
    dialogOpen: (document.querySelector('[data-dialog]')||{}).open,
    dialogText: (document.querySelector('[data-dialog-question]')||{}).textContent || null,
    focused: document.activeElement
      && document.activeElement.hasAttribute('data-dialog-keep') ? 'keep' : 'other',
  });
  const steps = {};

  document.querySelector('[data-typist="hr-aisyah"]').click();
  await wait();
  steps.start = read();

  set('[data-add-employee]', '__EMPLOYEE__');
  set('[data-add-at]', '__DAY__T08:05');
  set('[data-add-why]', 'device down all morning');
  await wait();
  document.querySelector('[data-add]').click();
  await wait(1200);
  steps.after_add = read();

  set('[data-look-employee]', '__EMPLOYEE__');
  set('[data-look-from]', '__DAY__');
  set('[data-look-to]', '__DAY__');
  await wait();
  document.querySelector('[data-look]').click();
  await wait(1000);
  steps.after_look = read();

  const id = steps.after_look.rows[0].id;
  document.querySelector('[data-cancel="' + id + '"]').click();
  await wait();
  steps.dialog = read();

  document.querySelector('[data-dialog-keep]').click();
  await wait();
  steps.after_keep = read();

  document.querySelector('[data-cancel="' + id + '"]').click();
  await wait();
  set('[data-cancel-why]', 'wrong employee');
  await wait();
  document.querySelector('[data-dialog-cancel-it]').click();
  await wait(1500);
  steps.after_cancel = read();
  steps.punch_id = id;

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
    from app.models import ManualPunch, ManualPunchCancellation

    from tools.throwaway import Throwaway

    gate = Gate()

    def counts() -> tuple[int, int]:
        with Session() as session:
            return (
                session.scalar(select(func.count()).select_from(ManualPunch)),
                session.scalar(
                    select(func.count()).select_from(ManualPunchCancellation)),
            )

    at_the_start = counts()

    scratch = Throwaway()
    scratch.start()
    try:
        scratch.run("hr", "seed", "--add-missing")
        return run(gate, args, scratch, at_the_start)
    finally:
        scratch.stop()


def run(gate, args, scratch, at_the_start) -> int:
    """Every check, with the throwaway already up."""
    from sqlalchemy import func, select, text

    from app import corrections as corrections_module
    from app import hr_corrections as entry
    from app.attendance import build_days, days_for
    from app.corrections import employee_by_number
    from app.db import Session
    from app.detail import render_detail
    from app.models import Employee, ManualPunch, ManualPunchCancellation

    host, port = args.host, args.hr_port
    day = dt.date.fromisoformat(DAY)

    def counts() -> tuple[int, int]:
        with Session() as session:
            return (
                session.scalar(select(func.count()).select_from(ManualPunch)),
                session.scalar(
                    select(func.count()).select_from(ManualPunchCancellation)),
            )

    # ---- 1. a cancellation is a row -------------------------------------
    print("\n-- 1. the database refuses an edit and a delete")
    with Session() as session:
        employee = employee_by_number(session, EMPLOYEE)
        punch = corrections_module.record_hr_retroactive(
            session, employee, asserted_time=dt.datetime.combine(day, dt.time(8, 5)),
            reason="device down", made_by="Aisyah")
        session.flush()
        before = {name: getattr(punch, name) for name in RECORDED}

        for column, value in (("made_by", "'somebody else'"),
                              ("asserted_time", "'2026-08-27 07:00'"),
                              ("reason", "'a better reason'"),
                              ("employee_id", "employee_id + 1"),
                              ("recorded_at", "now()")):
            savepoint = session.begin_nested()
            try:
                session.execute(text(
                    f"UPDATE manual_punch SET {column} = {value} WHERE id = :i"),
                    {"i": punch.id})
                session.flush()
                gate.check(False, f"an UPDATE of {column} is refused",
                           "it was accepted")
            except Exception as exc:
                gate.check("cannot be edited" in str(exc),
                           f"an UPDATE of {column} is refused",
                           f"refused by something else: {str(exc)[:140]}")
            finally:
                savepoint.rollback()

        savepoint = session.begin_nested()
        try:
            session.execute(text("DELETE FROM manual_punch WHERE id = :i"),
                            {"i": punch.id})
            session.flush()
            gate.check(False, "a DELETE is refused", "it was accepted")
        except Exception as exc:
            gate.check("cannot be deleted" in str(exc), "a DELETE is refused",
                       f"refused by something else: {str(exc)[:140]}")
        finally:
            savepoint.rollback()

        # The two derived columns are still rebuildable — that exception is
        # what `hr corrections rebuild-days` runs on.
        savepoint = session.begin_nested()
        try:
            session.execute(text(
                "UPDATE manual_punch SET attendance_day = attendance_day, "
                "schedule_id = schedule_id WHERE id = :i"), {"i": punch.id})
            session.flush()
            gate.check(True, "and the two derived columns are still rebuildable")
        except Exception as exc:
            gate.check(False, "and the two derived columns are still rebuildable",
                       str(exc)[:140])
        finally:
            savepoint.rollback()

        print("\n-- and cancelling one leaves it exactly as it was")
        # **Reported, not raised.** A cancellation that edits the punch is
        # refused by the database, and a gate that died on the traceback would
        # say nothing about the eleven checks after this one.
        try:
            entry.cancel(session, cancelled_by="hr-aslida", punch_id=punch.id,
                         reason="wrong employee")
        except Exception as exc:
            gate.check(False, "cancelling a correction is accepted",
                       f"it was refused: {str(exc)[:200]}")
            print(f"\n{gate.checks} checks")
            print(f"{len(gate.failures)} FAILED:")
            for failure in gate.failures:
                print(f"  - {failure}")
            return 1
        session.expire(punch)
        after = {name: getattr(punch, name) for name in RECORDED}
        gate.check(before == after,
                   f"every recorded column is unchanged: {sorted(RECORDED)}",
                   f"{[k for k in RECORDED if before[k] != after[k]]} moved")
        cancellation = session.scalars(
            select(ManualPunchCancellation)
            .where(ManualPunchCancellation.manual_punch_id == punch.id)).first()
        gate.check(cancellation is not None,
                   "and the cancellation is a row of its own")
        gate.check(cancellation.cancelled_by == "Aslida",
                   "carrying who cancelled it",
                   f"it says {cancellation.cancelled_by!r}")
        gate.check(cancellation.reason == "wrong employee",
                   "and why", f"it says {cancellation.reason!r}")

        # Twice is not two facts.
        try:
            entry.cancel(session, cancelled_by="hr-aisyah", punch_id=punch.id,
                         reason="again")
            gate.check(False, "cancelling twice is refused", "it was accepted")
        except ValueError as exc:
            gate.check("already cancelled" in str(exc),
                       "cancelling twice is refused", str(exc)[:120])

        # And the cancellation itself cannot be edited or deleted away.
        for statement, what in (
            ("UPDATE manual_punch_cancellation SET reason = 'x' WHERE id = :i",
             "an UPDATE of a cancellation"),
            ("DELETE FROM manual_punch_cancellation WHERE id = :i",
             "a DELETE of a cancellation"),
        ):
            savepoint = session.begin_nested()
            try:
                session.execute(text(statement), {"i": cancellation.id})
                session.flush()
                gate.check(False, f"{what} is refused", "it was accepted")
            except Exception as exc:
                gate.check("append-only" in str(exc), f"{what} is refused",
                           f"refused by something else: {str(exc)[:140]}")
            finally:
                savepoint.rollback()

        # ---- 2. the day stops counting it ------------------------------
        print("\n-- 2. the daily row stops counting it, and says so")
        built = build_days(session, day, day, employee_ids=[employee.id])[0]
        gate.check(built.values["punch_count"] == 0,
                   "a day whose only punch was cancelled counts none",
                   f"it counts {built.values['punch_count']}")
        gate.check(built.values["first_in"] is None
                   and built.values["last_out"] is None,
                   "with no first in and no last out",
                   f"first_in is {built.values['first_in']}")
        gate.check(built.values["cancelled_punch_count"] == 1,
                   "and the row records that one was cancelled",
                   f"it records {built.values['cancelled_punch_count']}")
        gate.check("cancelled" in (built.values["note"] or ""),
                   "and says so in words",
                   f"the note is {built.values['note']!r}")

        row = days_for(session, employee.id, day, day)[0]
        gate.check(row.punch_count == 0 and row.cancelled_punch_count == 1,
                   "the stored row agrees",
                   f"{row.punch_count} counted, {row.cancelled_punch_count} "
                   "cancelled")

        print("\n-- and the day detail shows it rather than hiding it")
        detail = render_detail(session, employee, day, day, with_punches=True)
        lines = detail.days[0].punches
        gate.check(len(lines) == 1,
                   "the cancelled punch is still listed — a punch that "
                   "disappears is indistinguishable from one that never "
                   "happened (SPEC §3)",
                   f"the detail lists {len(lines)} punches")
        if lines:
            gate.check(lines[0].cancelled is True, "marked cancelled",
                       f"cancelled is {lines[0].cancelled}")
            gate.check(lines[0].counted is False, "and not counted",
                       f"counted is {lines[0].counted}")
            gate.check(lines[0].cancelled_by == "Aslida"
                       and lines[0].cancelled_why == "wrong employee",
                       "saying who cancelled it and why",
                       f"{lines[0].cancelled_by!r} / {lines[0].cancelled_why!r}")
        session.rollback()

    print("\n-- and on a day that already has device punches, it removes one "
          "figure and leaves the rest")
    busy = dt.date.fromisoformat(BUSY_DAY)
    with Session() as session:
        employee = employee_by_number(session, EMPLOYEE)
        before_row = build_days(session, busy, busy,
                                employee_ids=[employee.id])[0].values
        gate.check(before_row["device_punch_count"] >= 2,
                   f"{BUSY_DAY} has {before_row['device_punch_count']} device "
                   "punches to start with",
                   f"it has {before_row['device_punch_count']}")

        # Earlier than any device punch, so it becomes the day's first in.
        earlier = before_row["first_in"] - dt.timedelta(hours=1)
        added = corrections_module.record_hr_retroactive(
            session, employee, asserted_time=earlier,
            reason="gate: a punch to be cancelled", made_by="Aisyah")
        session.flush()
        with_it = build_days(session, busy, busy,
                             employee_ids=[employee.id])[0].values
        gate.check(with_it["first_in"] == earlier,
                   "adding a correction moves the day's first in",
                   f"first_in is {with_it['first_in']}")
        gate.check(with_it["punch_count"] == before_row["punch_count"] + 1,
                   "and adds one to the count",
                   f"{before_row['punch_count']} became {with_it['punch_count']}")

        entry.cancel(session, cancelled_by="hr-aisyah", punch_id=added.id,
                     reason="gate: cancelled")
        after_row = build_days(session, busy, busy,
                               employee_ids=[employee.id])[0].values
        gate.check(after_row["first_in"] == before_row["first_in"],
                   "cancelling it puts the first in back where it was",
                   f"first_in is {after_row['first_in']}, was "
                   f"{before_row['first_in']}")
        gate.check(after_row["last_out"] == before_row["last_out"],
                   "and the last out")
        gate.check(after_row["punch_count"] == before_row["punch_count"],
                   "and the count",
                   f"{after_row['punch_count']} against "
                   f"{before_row['punch_count']}")
        gate.check(after_row["device_punch_count"]
                   == before_row["device_punch_count"],
                   "**and the device punches are untouched** — a cancellation "
                   "takes out one correction, not the day",
                   f"{after_row['device_punch_count']} against "
                   f"{before_row['device_punch_count']}")
        gate.check(after_row["cancelled_punch_count"] == 1,
                   "with one recorded as cancelled",
                   f"it records {after_row['cancelled_punch_count']}")

        detail = render_detail(session, employee, busy, busy, with_punches=True)
        shown = detail.days[0].punches
        gate.check(len(shown) == len(
                       [p for p in shown if not p.cancelled]) + 1,
                   "and the detail still lists the cancelled one beside the "
                   f"device punches ({len(shown)} lines)",
                   f"it lists {[(p.source, p.cancelled) for p in shown]}")
        session.rollback()

    # ---- 3. only a correction can be cancelled --------------------------
    print("\n-- 3. a device punch is not offered and is not accepted")
    with Session() as session:
        from app.models import ParsedPunch

        device_ids = list(session.scalars(
            select(ParsedPunch.id).where(ParsedPunch.parse_ok.is_(True))
            .limit(3)))
        gate.check(bool(device_ids),
                   f"there are device punches on file to try: {device_ids}")
        manual_ids = set(session.scalars(select(ManualPunch.id)))
        for parsed_id in device_ids:
            if parsed_id in manual_ids:
                continue
            try:
                entry.cancel(session, cancelled_by="hr-aisyah",
                             punch_id=parsed_id, reason="should not work")
                gate.check(False,
                           f"parsed_punch {parsed_id} is refused",
                           "it was accepted")
            except Exception as exc:
                # **Any exception, not just a ValueError.** A refusal has to be
                # a sentence a person can read; a crash on the way past the
                # check is a different failure and is reported as one.
                gate.check("is not a manual punch" in str(exc),
                           f"parsed_punch {parsed_id} is refused, by name",
                           f"{type(exc).__name__}: {str(exc)[:140]}")
            session.rollback()

    # **Read out of the code, not out of the prose around it.** Both of these
    # functions have a docstring saying they never touch the parsed layer; a
    # substring search agrees with the sentence rather than with the code, and
    # would pass a function that reached it on the next line.
    def names_in(function) -> set:
        tree = ast.parse(inspect.getsource(function).lstrip())
        body = tree.body[0].body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)):
            body = body[1:]          # step over the docstring
        found = set()
        for statement in body:
            for node in ast.walk(statement):
                if isinstance(node, ast.Name):
                    found.add(node.id)
                elif isinstance(node, ast.Attribute):
                    found.add(node.attr)
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    found.add(node.value)
        return found

    used = names_in(corrections_module.cancel_manual_punch)
    gate.check("ParsedPunch" not in used and "parsed_punch" not in used,
               "cancel_manual_punch never reaches the parsed layer",
               f"it names {sorted(n for n in used if 'unch' in n)}")
    used = names_in(corrections_module.manual_punches_in)
    gate.check("ParsedPunch" not in used and "device_punches_for" not in used,
               "and the list it is chosen from reads manual_punch only",
               f"it names {sorted(n for n in used if 'unch' in n)}")

    # And over HTTP, on the throwaway.
    before = counts()
    for punch_id, why in ((999999, "an id that is nothing"),
                          (0, "zero"), (-1, "a negative id")):
        status, body = post(*scratch.published, "/api/corrections/cancel", {
            "cancelled_by": "hr-aisyah", "punch_id": punch_id,
            "reason": "should not work"})
        gate.check(status == 400, f"{why} is refused", f"got {status}")
    gate.check(counts() == before, "and none of them wrote anything",
               f"the counts moved to {counts()}")

    # ---- 4. the two paths do not meet -----------------------------------
    print("\n-- 4. the HR path and the guard path do not meet")
    from app.hr_app import Cancellation, GuardEntry, Retroactive

    hr_fields = set(Retroactive.model_fields)
    guard_fields = set(GuardEntry.model_fields)
    gate.check(hr_fields == {"entered_by", "employee_number", "at", "reason"},
               f"the HR payload is {sorted(hr_fields)}",
               f"it is {sorted(hr_fields)}")
    gate.check("at" in hr_fields and not (guard_fields & {"at"}),
               "the HR payload carries a time and the guard's does not — the "
               "whole difference between the two paths (SPEC §3)")
    gate.check(not (hr_fields & guard_fields) - {"employee_number"},
               "and they share nothing but the employee number",
               f"they share {sorted(hr_fields & guard_fields)}")
    for model in (Retroactive, Cancellation):
        gate.check(model.model_config.get("extra") == "forbid",
                   f"{model.__name__} refuses a field it does not name")

    before = counts()
    for smuggled, value in (("reason_code", "biometric_failed"),
                            ("guard_code", "guard-1"),
                            ("made_by", "Guard 1")):
        status, body = post(*scratch.published, "/api/corrections/retroactive", {
            "entered_by": "hr-aisyah", "employee_number": EMPLOYEE,
            "at": f"{DAY} 08:05", "reason": "device down", smuggled: value})
        gate.check(status == 422, f"a payload carrying {smuggled!r} is a 422",
                   f"got {status} {str(body)[:120]}")
    gate.check(counts() == before, "and none of them wrote anything",
               f"the counts moved to {counts()}")

    entry_tree = module_source(entry)
    imported = imports_of(entry_tree)
    gate.check("app.guard" not in imported and "guard" not in imported,
               "app/hr_corrections.py does not import the guard's module",
               f"it imports {sorted(imported)}")

    # The module's own prose explains at length what it does not reach, which
    # is exactly what a substring search would trip over. Strip the docstrings
    # and read what is left.
    stripped = ast.parse(pathlib.Path(entry.__file__).read_text())
    for node in ast.walk(stripped):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    code = ast.unparse(ast.fix_missing_locations(stripped))
    for forbidden in ("record_guard_entry", "/guard",
                      "session.delete", "DELETE FROM", "session.commit"):
        gate.check(forbidden not in code,
                   f"and its code contains no {forbidden!r}",
                   "it does")

    # The guard's path constant, by name and by value. A substring search finds
    # `NOT_THE_GUARD_PATH` — the module's own constant, which is the sentence
    # explaining that it does *not* reach the guard.
    guardish = set()
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.Name) and node.id == "GUARD":
            guardish.add("the name GUARD")
        elif isinstance(node, ast.Constant) and node.value == "guard":
            guardish.add("the value 'guard'")
    gate.check(not guardish,
               "and it never names the guard's path, by name or by value",
               f"it uses {sorted(guardish)}")
    gate.check("record_hr_retroactive" in imported
               or "app.corrections" in imported,
               "it writes through app.corrections — the same functions the CLI "
               "calls")

    page = pathlib.Path("ui/src/screens/Corrections.jsx").read_text()
    for forbidden in ("/guard", "api/guard", "guard_code", "reason_code"):
        gate.check(forbidden not in page,
                   f"the page's source has no {forbidden!r} in it")

    nav = pathlib.Path("ui/src/App.jsx").read_text()
    header = nav.split("<nav")[1].split("</nav>")[0]
    gate.check("/guard" not in header,
               "and the interface's nav does not offer the guard's screen",
               "it does")

    print("\n-- the routes hand the form on and compute nothing")
    import app.hr_app as hr_app_module

    tree = module_source(hr_app_module)
    routes = route_functions(tree)
    for name in ("corrections_screen", "corrections_list",
                 "corrections_retroactive", "corrections_cancel"):
        gate.check(name in routes, f"{name}() is an API route")
        found = computation_in(routes[name]) if name in routes else ["missing"]
        gate.check(not found, "   and works nothing out", "; ".join(found))
    passed = set()
    for node in ast.walk(routes["corrections_cancel"]):
        if isinstance(node, ast.Call):
            passed |= {keyword.arg for keyword in node.keywords if keyword.arg}
    gate.check(passed == set(Cancellation.model_fields),
               f"the cancel route hands on exactly {sorted(passed)}",
               f"it passes {sorted(passed)}")

    import app.cli_corrections as cli

    cli_source = pathlib.Path(cli.__file__).read_text()
    gate.check("cancel_manual_punch" in cli_source,
               "`hr corrections cancel` calls the same function")
    for forbidden in ("session.delete", "DELETE FROM", "def cmd_edit"):
        gate.check(forbidden not in cli_source,
                   f"and the CLI contains no {forbidden!r}")

    # ---- the page ------------------------------------------------------
    if args.no_dom:
        print("\n-- SKIPPED: the browser checks (--no-dom).")
    else:
        print("\n-- the page, before anybody has said who is correcting")
        reader = FormReader()
        reader.feed(rendered_dom(args.dom_root + "/corrections?"))
        offered = sorted(marker for marker in reader.markers
                         if marker.startswith("data-typist="))
        gate.check(offered == ["data-typist=hr-aisyah",
                               "data-typist=hr-aslida"],
                   f"a fresh browser is asked who is correcting: {offered}",
                   f"it offers {offered}")

        print("\n-- and the same page, pressed rather than read")
        from tools.browser import Browser

        with Browser(width=1280, height=1200) as browser:
            browser.go(scratch.base + "/corrections")
            steps = json.loads(browser.evaluate_raw(
                FILL.replace("__EMPLOYEE__", EMPLOYEE).replace("__DAY__", DAY)))

        start = steps["start"]
        gate.check(not start["guardLinks"],
                   "the screen offers no way to reach the guard's",
                   f"found {start['guardLinks']}")
        gate.check("HR path" in (start["notGuard"] or ""),
                   "and says which path it is",
                   f"it says {(start['notGuard'] or '')[:120]!r}")
        gate.check("no field for a time at all" in (start["notGuard"] or ""),
                   "and what the other one is",
                   f"it says {(start['notGuard'] or '')[:200]!r}")

        added = steps["after_add"]
        gate.check(not added["error"], "adding a punch was accepted",
                   f"the page says {added['error']!r}")
        gate.check(f"{DAY} 08:05" in (added["added"] or ""),
                   "and the recorded line states the time that was typed",
                   f"it says {(added['added'] or '')[:200]!r}")
        gate.check("Aisyah" in (added["added"] or ""),
                   "and who entered it",
                   f"it says {(added['added'] or '')[:200]!r}")

        looked = steps["after_look"]
        gate.check(len(looked["rows"]) == 1,
                   "the correction is found by employee and period",
                   f"it found {len(looked['rows'])}")
        gate.check("Only corrections appear here" in (looked["onlyManual"] or ""),
                   "and the list says a device punch is not among them",
                   f"it says {looked['onlyManual']!r}")
        # **A guard entry is the one that carries microseconds**, because its
        # time is the server's own stamp rather than a typed one — and it is
        # the entry this screen most often lists. An HR retroactive punch is
        # typed to the minute and would pass this check whatever the format.
        scratch.run("hr", "corrections", "guard", "--employee", EMPLOYEE,
                    "--reason", "biometric_failed", "--by", "Guard: gate")
        stamped = scratch.run("hr", "corrections", "list", "--employee",
                              EMPLOYEE, "--from", "2020-01-01",
                              "--to", "2030-12-31")
        gate.check("guard" in stamped,
                   "a guard entry — server-stamped, so its time is not a typed "
                   "one — is on the record to look at",
                   stamped[-200:])
        shown = json.loads(ask(*scratch.published, "GET",
                               f"/api/corrections/list?employee={EMPLOYEE}"
                               "&from=2020-01-01&to=2030-12-31")[1])
        offending = [line["at"] for line in shown["corrections"]
                     if line["at"] and re.search(r":\d{2}\.\d", line["at"])]
        gate.check(not offending,
                   "and every time the screen lists is to the second, not the "
                   "microsecond",
                   f"these carry a fraction: {offending}")

        # **Where the dialog actually is.** It opened in the top-left corner
        # once, because Tailwind's preflight sets `margin: 0` on everything and
        # that includes the `margin: auto` a modal dialog centres itself with.
        # A dumped DOM cannot see that; a laid-out page can.
        with Browser(width=1280, height=1200) as browser:
            browser.go(scratch.base + "/corrections")
            placed = json.loads(browser.evaluate_raw(
                "(async () => {"
                "const wait = (ms) => new Promise(r => setTimeout(r, ms||300));"
                "document.querySelector('[data-typist=\"hr-aisyah\"]').click();"
                "await wait();"
                "const d = document.querySelector('[data-dialog]');"
                "d.showModal(); await wait();"
                "const b = d.getBoundingClientRect();"
                "return JSON.stringify({left: Math.round(b.left), "
                "top: Math.round(b.top), width: Math.round(b.width), "
                "view: document.documentElement.clientWidth, "
                "height: document.documentElement.clientHeight});})()"))
            centred = abs(
                placed["left"] - (placed["view"] - placed["width"]) / 2) <= 2
            gate.check(centred,
                       f"the dialog is centred in the viewport "
                       f"({placed['left']}px from the left of {placed['view']})",
                       f"it sits at {placed['left']},{placed['top']} — a modal "
                       "dialog in the corner is one Tailwind reset away")
            gate.check(0 <= placed["top"] <= placed["height"],
                       "and inside it vertically",
                       f"its top is {placed['top']}")

        dialog = steps["dialog"]
        gate.check(dialog["dialogOpen"] is True,
                   "Cancel this opens a dialog rather than cancelling")
        gate.check(EMPLOYEE in (dialog["dialogText"] or "")
                   and "08:05" in (dialog["dialogText"] or ""),
                   "which names the punch it is about to void",
                   f"it says {dialog['dialogText']!r}")
        # **To the second, in a headline a person is meant to read.** Six
        # decimal places of a second is noise standing where the fact is, and
        # the fact is the whole reason the dialog repeats it rather than asking
        # "are you sure?".
        gate.check(not re.search(r"\d{2}:\d{2}:\d{2}\.\d",
                                 dialog["dialogText"] or ""),
                   "and states the time to the second, not the microsecond",
                   f"it says {dialog['dialogText']!r}")
        gate.check(dialog["focused"] == "keep",
                   "and Keep it holds the focus — the default answer is no",
                   f"the focus is on {dialog['focused']}")
        gate.check(steps["after_keep"]["dialogOpen"] is False
                   and not steps["after_keep"]["cancelled"],
                   "Keep it closes it and cancels nothing",
                   f"{steps['after_keep']['cancelled']!r}")

        done = steps["after_cancel"]
        gate.check(not done["error"], "the cancellation was accepted",
                   f"the page says {done['error']!r}")
        gate.check("Aslida" not in (done["cancelled"] or "")
                   and "Aisyah" in (done["cancelled"] or ""),
                   "recorded against whoever was at the keyboard",
                   f"it says {(done['cancelled'] or '')[:200]!r}")
        gate.check("wrong employee" in (done["cancelled"] or ""),
                   "with the reason that was typed",
                   f"it says {(done['cancelled'] or '')[:200]!r}")
        gate.check("exactly as it was written" in (done["unchanged"] or ""),
                   "and the page says the punch row is unchanged",
                   f"it says {done['unchanged']!r}")
        gate.check(done["rows"] and done["rows"][0]["cancelled"] == "yes",
                   "the list shows it as cancelled rather than dropping it",
                   f"the rows are {done['rows']}")

        # And what the throwaway's own database now holds, read rather than
        # believed — including the sheet's own figures for that day.
        punches = scratch.run("hr", "punches", "--employee", EMPLOYEE,
                              "--day", DAY)
        gate.check("CANCELLED by Aisyah" in punches,
                   "the punch detail marks it cancelled",
                   punches[-300:])
        gate.check("1 punches, 1 entered by a person, 1 cancelled and not "
                   "counted" in punches,
                   "and counts it as not counted",
                   punches[-300:])
        listed = scratch.run("hr", "corrections", "list", "--employee",
                             EMPLOYEE, "--from", DAY, "--to", DAY)
        gate.check("CANCELLED by Aisyah — wrong employee" in listed,
                   "the CLI listing agrees", listed[-300:])
        scratch.run("hr", "attendance", "build", "--from", DAY, "--to", DAY)
        sheet = json.loads(ask(*scratch.published, "GET",
                               f"/api/sheet?from={DAY}&to={DAY}")[1])
        person = next((r for r in sheet["rows"]
                       if r["employee_number"] == EMPLOYEE), None)
        cell = sheet["cells"].get(f"{person['employee_id']}:{DAY}") if person else None
        gate.check(cell is not None and cell["punch_count"] == 0,
                   "and the sheet cell counts no punch that day",
                   f"the cell is {cell}")

    written = counts()
    gate.check(written == at_the_start,
               f"and this gate wrote nothing: (manual_punch, cancellations) is "
               f"still {at_the_start}",
               f"it went from {at_the_start} to {written}")

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
