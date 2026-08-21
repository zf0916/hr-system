#!/usr/bin/env python3
"""The gate for step 10, piece 3: the guard screen.

**Four claims, each proven by making it fail.**

  1. *No time input exists* — not on the page, not in the payload. Checked in
     the rendered DOM of every step of the screen, and in the request model.
  2. *A crafted request carrying a time is refused* — twice over: by the
     payload model before it reaches the service layer, and by the same check
     constraint the CLI hits if anything ever got past that.
  3. *A third reason is refused.* The two reasons are rows, and a reason
     belonging to the other path is refused as firmly as one that does not
     exist at all.
  4. *An unknown employee number is refused before anything is written.*

**The success path is exercised at the service layer, inside a transaction that
is rolled back — deliberately, not for convenience.** Recording one over HTTP
would leave a correction on the record, and §13 forbids deleting one; a gate
that ran daily would silently accumulate punches against a real employee. What
the route adds over the service function is checked structurally instead: it
takes the three names and hands them straight on, and the AST says so.

    uv run python tools/guard_gate.py
    uv run python tools/guard_gate.py --no-dom

Exits non-zero if any deliberate mistake was accepted.
"""

from __future__ import annotations

import argparse
import ast
import html.parser
import json
import pathlib
import sys
from dataclasses import dataclass, field

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tools.screens_gate import (  # noqa: E402
    ask,
    computation_in,
    imports_of,
    module_source,
    rendered_dom,
    route_functions,
)

# Anything that would let a person state a moment. `text` and `search` are not
# here: the employee box is a text box, and a number is not a time.
TIME_INPUT_TYPES = {"time", "date", "datetime-local", "month", "week"}
TIME_WORDS = ("time", "date", "hour", "minute", "clock", "when", "stamp")


@dataclass
class Gate:
    failures: list = field(default_factory=list)
    checks: int = 0

    def check(self, ok: bool, what: str, detail: str = "") -> bool:
        self.checks += 1
        print(f"  {'ok  ' if ok else 'FAIL'}    {what}"
              + ("" if ok else f" — {detail}"))
        if not ok:
            self.failures.append(what)
        return ok


class ScreenReader(html.parser.HTMLParser):
    """Every control on the page, and the marked landmarks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.inputs: list[dict] = []
        self.marks: dict[str, str] = {}
        self.buttons: list[str] = []
        self._open: str | None = None
        self._tag: str | None = None
        self._depth = 0
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in ("input", "select", "textarea"):
            self.inputs.append({"tag": tag, **attributes})
        elif self._open is not None and tag == self._tag:
            # **Depth, not the first closing tag.** A marked paragraph with a
            # `<strong>` inside it closes that first, and a reader that stopped
            # there would quietly capture half the sentence — and pass, because
            # the half it kept still contained what was being looked for.
            self._depth += 1
        for key in ("data-name-back", "data-cannot-undo", "data-error",
                    "data-recorded", "data-provisional-guards"):
            if key in attributes:
                self._open, self._tag, self._depth = key, tag, 0
                self._text = []
        for key in ("data-reason", "data-guard-choice", "data-submit",
                    "data-dialog-cancel", "data-dialog-confirm", "data-pick"):
            if key in attributes:
                self.buttons.append(f"{key}={attributes[key] or 'yes'}")

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


def post(host: str, port: int, path: str, body: dict) -> tuple[int, dict]:
    import http.client

    payload = json.dumps(body).encode()
    connection = http.client.HTTPConnection(host, port, timeout=30)
    try:
        connection.request("POST", path, body=payload, headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
            "Connection": "close",
        })
        response = connection.getresponse()
        raw = response.read()
        try:
            return response.status, json.loads(raw)
        except ValueError:
            return response.status, {"raw": raw.decode(errors="replace")}
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--hr-port", type=int, default=8090)
    parser.add_argument("--employee", default="0090")
    parser.add_argument("--dom-root", default="http://api:8100")
    parser.add_argument("--no-dom", action="store_true")
    args = parser.parse_args()

    from sqlalchemy import func, select, text

    from app import guard as guard_module
    from app.corrections import GUARD, employee_by_number, record_guard_entry
    from app.db import Session
    from app.models import CorrectionReason, ManualPunch, ScreenUser

    gate = Gate()
    host, port = args.host, args.hr_port

    def written() -> int:
        with Session() as session:
            return session.scalar(select(func.count()).select_from(ManualPunch))

    # What the database held before this gate touched anything. Checked again
    # at the end — see the last check, and read its comment.
    at_the_start = written()

    print("\n-- the screen is rows: who is on duty, and the two reasons")
    screen = json.loads(ask(host, port, "GET", "/api/guard/screen")[1])
    with Session() as session:
        guards = guard_module.on_duty(session)
        reasons = guard_module.reasons(session)
        gate.check([one["code"] for one in screen["guards"]]
                   == [one.code for one in guards],
                   f"the {len(guards)} guards come from screen_user rows")
        gate.check([one["code"] for one in screen["reasons"]]
                   == [one.code for one in reasons],
                   f"the reasons come from correction_reason rows: "
                   f"{[one.code for one in reasons]}")
        gate.check(len(reasons) == 2,
                   "and there are exactly two of them (SPEC §3)",
                   f"found {[one.code for one in reasons]}")

    print("\n-- no time input exists in the payload")
    from app.hr_app import GuardEntry

    fields = set(GuardEntry.model_fields)
    gate.check(fields == {"guard_code", "employee_number", "reason_code"},
               f"the request model carries exactly three names: {sorted(fields)}",
               f"it carries {sorted(fields)}")
    gate.check(GuardEntry.model_config.get("extra") == "forbid",
               "and refuses any field it does not name, rather than dropping it",
               f"extra is {GuardEntry.model_config.get('extra')!r}")

    # The service function it calls has no time parameter either, and neither
    # does the one *that* calls. Three signatures, none of them able to carry a
    # moment inwards.
    import inspect

    for function in (guard_module.record, record_guard_entry):
        parameters = set(inspect.signature(function).parameters)
        offending = {name for name in parameters
                     if any(word in name for word in TIME_WORDS)}
        gate.check(not offending,
                   f"{function.__module__}.{function.__name__}() takes no time",
                   f"it takes {sorted(offending)}")

    print("\n-- a crafted request carrying a time is refused")
    before = written()
    for smuggled in ("asserted_time", "recorded_at", "punch_time", "at"):
        status, body = post(host, port, "/api/guard/entry", {
            "guard_code": "guard-1",
            "employee_number": args.employee,
            "reason_code": "biometric_failed",
            smuggled: "2026-08-01 07:00:00",
        })
        gate.check(status == 422, f"a payload carrying {smuggled!r} is a 422",
                   f"got {status} {str(body)[:120]}")
    gate.check(written() == before,
               "and none of them wrote anything",
               f"manual_punch went from {before} to {written()}")

    # **The same constraint the CLI hits.** If a route were ever changed to
    # accept a time, this is what would still be standing.
    with Session() as session:
        employee = employee_by_number(session, args.employee)
        try:
            session.execute(text(
                "INSERT INTO manual_punch (employee_id, path, asserted_time, "
                "attendance_day, reason_code, made_by) VALUES "
                "(:e, 'guard', '2026-08-01 07:00:00', '2026-08-01', "
                "'biometric_failed', 'gate')"), {"e": employee.id})
            session.flush()
            gate.check(False, "the database refuses a guard row that states a time",
                       "it accepted one")
        except Exception as exc:
            gate.check("manual_punch_guard_cannot_state_a_time" in str(exc),
                       "the database refuses a guard row that states a time, by "
                       "the constraint the CLI hits too",
                       f"refused by something else: {str(exc)[:160]}")
        session.rollback()

    print("\n-- a third reason is refused")
    before = written()
    status, body = post(host, port, "/api/guard/entry", {
        "guard_code": "guard-1", "employee_number": args.employee,
        "reason_code": "forgot_card"})
    gate.check(status == 400, "a reason that is not a row is refused",
               f"got {status}")
    gate.check("biometric_failed" in str(body) and "not_enrolled" in str(body),
               "and the refusal names the two that are",
               f"said {str(body)[:140]}")

    # A reason that exists but belongs to HR's path is refused just as firmly —
    # the guard's list is the rows whose path is 'guard', not every reason in
    # the table. Made and rolled back inside this transaction.
    with Session() as session:
        session.add(CorrectionReason(code="gate-hr-only", label="HR only",
                                     path="hr_retroactive", note="gate"))
        session.flush()
        employee = employee_by_number(session, args.employee)
        try:
            guard_module.record(session, guard_code="guard-1",
                                employee_number=args.employee,
                                reason_code="gate-hr-only")
            gate.check(False, "a reason from HR's path is refused too",
                       "it was accepted")
        except ValueError as exc:
            gate.check("not a reason a guard entry may give" in str(exc),
                       "a reason from HR's path is refused too",
                       str(exc)[:140])
        session.rollback()
    gate.check(written() == before, "and nothing was written by either",
               f"manual_punch moved to {written()}")

    print("\n-- an unknown employee number is refused before anything is written")
    before = written()
    for number in ("9999999", "", "not-a-number"):
        status, body = post(host, port, "/api/guard/entry", {
            "guard_code": "guard-1", "employee_number": number,
            "reason_code": "biometric_failed"})
        gate.check(status == 400, f"{number!r} is refused", f"got {status}")
    status, body = post(host, port, "/api/guard/employee/9999999", {})
    gate.check(ask(host, port, "GET", "/api/guard/employee/9999999")[0] == 400,
               "and looking one up says so before the guard can confirm")
    gate.check(written() == before, "nothing was written by any of them",
               f"manual_punch moved to {written()}")

    # A guard who is not on the list is refused too: every entry says who made
    # it, and a name nobody chose is not attribution.
    before = written()
    status, body = post(host, port, "/api/guard/entry", {
        "guard_code": "not-a-guard", "employee_number": args.employee,
        "reason_code": "biometric_failed"})
    gate.check(status == 400, "a guard who is not on the list is refused",
               f"got {status}")
    gate.check(written() == before, "and wrote nothing")

    print("\n-- the entry it does make, in a transaction that is rolled back")
    with Session() as session:
        employee = employee_by_number(session, args.employee)
        result = guard_module.record(session, guard_code="guard-1",
                                     employee_number=args.employee,
                                     reason_code="biometric_failed")
        punch = session.get(ManualPunch, result["id"])
        gate.check(punch.asserted_time is None,
                   "the row it writes states no time",
                   f"asserted_time is {punch.asserted_time}")
        gate.check(punch.recorded_at is not None,
                   f"and carries the server's own stamp: {punch.recorded_at}")
        gate.check(punch.path == GUARD, "on the guard path")
        gate.check(punch.made_by == session.get(ScreenUser, "guard-1").name,
                   "attributed to the guard who was picked, by the row's name",
                   f"made_by is {punch.made_by!r}")
        gate.check(punch.reason_code == "biometric_failed",
                   "with the reason he chose")
        gate.check(result["name"] == guard_module.look_up(
                       session, args.employee)["name"],
                   "and it answers with the name that was shown back")
        gate.check("cannot be undone" in result["final"],
                   "and says the entry cannot be undone")
        session.rollback()

    print("\n-- nothing here can undo one")
    tree = module_source(guard_module)
    source = pathlib.Path(guard_module.__file__).read_text()
    for forbidden in ("session.delete", "DELETE FROM", "def void", "def undo"):
        gate.check(forbidden not in source,
                   f"app/guard.py contains no {forbidden!r}")
    gate.check("record_guard_entry" in imports_of(tree)
               or "app.corrections" in imports_of(tree),
               "and it writes through app.corrections, not by hand")
    gate.check("ManualPunch" not in source,
               "it never builds a punch row itself — the CLI's function does",
               "app/guard.py mentions ManualPunch")

    import app.cli_corrections as cli_corrections

    gate.check("record_guard_entry" in pathlib.Path(
                   cli_corrections.__file__).read_text(),
               "the CLI's guard entry calls the same function")

    print("\n-- the route hands the three names on and computes nothing")
    import app.hr_app as hr_app_module

    hr_tree = module_source(hr_app_module)
    routes = route_functions(hr_tree)
    for name in ("guard_entry", "guard_look_up", "guard_screen"):
        gate.check(name in routes, f"{name}() is an API route")
        found = computation_in(routes[name]) if name in routes else ["missing"]
        gate.check(not found, f"   and works nothing out", "; ".join(found))
    imported_names = {
        alias.name
        for node in ast.walk(hr_tree) if isinstance(node, ast.ImportFrom)
        for alias in node.names
    } | imports_of(hr_tree)
    gate.check("guard" in imported_names or "app.guard" in imported_names,
               "the interface reaches the guard through its service module",
               f"it imports {sorted(imported_names)}")

    # **What the route actually passes on**, read out of the call rather than
    # out of the prose around it: three keywords, and the service function has
    # no fourth to receive anything else.
    passed = set()
    for node in ast.walk(routes["guard_entry"]):
        if isinstance(node, ast.Call):
            passed |= {keyword.arg for keyword in node.keywords if keyword.arg}
    gate.check(passed == {"guard_code", "employee_number", "reason_code"},
               f"and hands on exactly three names: {sorted(passed)}",
               f"it passes {sorted(passed)}")
    offending = {name for name in passed
                 if any(word in name for word in TIME_WORDS)}
    gate.check(not offending, "none of which is a time",
               f"it passes {sorted(offending)}")

    # The declared fields, which is where a time would have to appear to be
    # accepted at all. The class's own prose says there is none; this is the
    # part a reader cannot talk themselves into.
    annotations = {
        node.target.id
        for klass in ast.walk(hr_tree)
        if isinstance(klass, ast.ClassDef) and klass.name == "GuardEntry"
        for node in klass.body if isinstance(node, ast.AnnAssign)
    }
    gate.check(annotations == {"guard_code", "employee_number", "reason_code"},
               f"the request model declares only {sorted(annotations)}",
               f"it declares {sorted(annotations)}")

    if args.no_dom:
        print("\n-- SKIPPED: the browser check (--no-dom). What a payload "
              "refuses says nothing about what the page offers.")
    else:
        print("\n-- and the page itself, read out of a browser at phone width")
        steps = {
            "pick a guard": "/guard",
            "pick an employee": "/guard?guard=guard-1",
            "confirm": f"/guard?guard=guard-1&employee={args.employee}",
        }
        readers = {}
        for label, path in steps.items():
            reader = ScreenReader()
            reader.feed(rendered_dom(args.dom_root + path))
            readers[label] = reader
            offending = [
                control for control in reader.inputs
                if control.get("type") in TIME_INPUT_TYPES
                or any(word in (control.get(attribute) or "").lower()
                       for attribute in ("name", "id", "placeholder", "aria-label")
                       for word in TIME_WORDS)
            ]
            gate.check(not offending,
                       f"“{label}” offers nothing to type a time into "
                       f"({len(reader.inputs)} control(s))",
                       f"found {offending}")

        confirm = readers["confirm"]
        with Session() as session:
            expected = guard_module.look_up(session, args.employee)["name"]
        gate.check(confirm.marks.get("data-name-back") == expected,
                   f"the name shows back before anything is submitted: "
                   f"{expected!r}",
                   f"the page shows {confirm.marks.get('data-name-back')!r}")
        gate.check("cannot be undone" in confirm.marks.get("data-cannot-undo", ""),
                   "and the page says the entry cannot be undone")
        gate.check("HR" in confirm.marks.get("data-cannot-undo", ""),
                   "and that HR fixes mistakes")
        reasons_offered = sorted(b for b in confirm.buttons
                                 if b.startswith("data-reason="))
        gate.check(reasons_offered == ["data-reason=biometric_failed",
                                       "data-reason=not_enrolled"],
                   "exactly two reasons are offered on it",
                   f"offered {reasons_offered}")
        gate.check(any(b.startswith("data-submit") for b in confirm.buttons),
                   "and one Submit button")
        gate.check(not any("undo" in b or "void" in b or "delete" in b
                           for b in confirm.buttons),
                   "and nothing that undoes anything")
        gate.check(readers["pick a guard"].marks.get("data-provisional-guards"),
                   "the guard list says its names are placeholders",
                   "it does not say so, and they are")

        page = pathlib.Path("ui/src/screens/Guard.jsx").read_text()
        for kind in TIME_INPUT_TYPES:
            gate.check(f'type="{kind}"' not in page,
                       f"the page's source has no <input type=\"{kind}\">")

        # ---- what only a laid-out, clickable page can answer -------------
        from tools.browser import Browser

        # The longest name on the roster, because a page that fits “Ravi Tan”
        # is not a page that fits.
        roster = json.loads(ask(host, port, "GET", "/api/employees")[1])
        longest = max(roster["people"], key=lambda person: len(person["name"]))
        print(f"     (measuring with {longest['employee_number']} "
              f"{longest['name']!r}, the longest name on the roster)")

        for width in (390, 360, 320):
            with Browser(width=width, height=780) as browser:
                for label, path in (
                    ("pick a guard", "/guard"),
                    ("pick an employee", "/guard?guard=guard-1"),
                    ("confirm", f"/guard?guard=guard-1&employee="
                                f"{longest['employee_number']}"),
                ):
                    # **Against `clientWidth`, never `innerWidth`.** When a
                    # page overflows, the layout viewport grows to fit it and
                    # `innerWidth` grows with it — so `scrollWidth <=
                    # innerWidth` is true of a page that is 16px too wide and
                    # of one that is not. `clientWidth` stays at the device.
                    measured = json.loads(browser.evaluate(
                        args.dom_root + path,
                        "JSON.stringify({scroll: "
                        "document.documentElement.scrollWidth, "
                        "view: document.documentElement.clientWidth, "
                        "over: [...document.querySelectorAll('*')].filter("
                        "e => e.getBoundingClientRect().right > "
                        "document.documentElement.clientWidth + 0.5).map("
                        "e => e.tagName + ' ' + (e.textContent||'')"
                        ".trim().slice(0, 24)).slice(0, 3)})"))
                    gate.check(
                        measured["scroll"] <= measured["view"],
                        f"“{label}” does not scroll sideways at {width}px",
                        f"it is {measured['scroll']}px wide in a "
                        f"{measured['view']}px viewport, pushed out by "
                        f"{measured['over']}")

        # The dialog, by pressing the page rather than by reading it.
        with Browser(width=390, height=780) as browser:
            browser.go(args.dom_root + f"/guard?guard=guard-1&employee="
                                       f"{args.employee}")
            # **A tick between the clicks.** Choosing a reason re-renders the
            # page and only then is Submit enabled; both clicks in one tick hit
            # a disabled button and prove nothing.
            opened = browser.evaluate_raw(
                "(async () => {"
                "const wait = () => new Promise(r => setTimeout(r, 250));"
                "document.querySelector('[data-reason=\"biometric_failed\"]')"
                ".click(); await wait();"
                "document.querySelector('[data-submit]').click(); await wait();"
                "const d = document.querySelector('[data-dialog]');"
                "return JSON.stringify({open: d.open, "
                "text: d.querySelector('[data-dialog-question]').textContent, "
                "focused: document.activeElement.hasAttribute('data-dialog-cancel')"
                " ? 'cancel' : document.activeElement.outerHTML.slice(0, 60)"
                "});})()")
            opened = json.loads(opened)
            gate.check(opened["open"],
                       "Submit opens a dialog rather than recording")
            with Session() as session:
                expected = guard_module.look_up(session, args.employee)
            gate.check(expected["name"] in opened["text"],
                       f"the dialog repeats the name: {expected['name']!r}",
                       f"it says {opened['text']!r}")
            gate.check(expected["employee_number"] in opened["text"],
                       f"and the number: {expected['employee_number']!r}",
                       f"it says {opened['text']!r}")
            gate.check(opened["focused"] == "cancel",
                       "and cancel holds the focus — the default answer is no",
                       f"the focus is on {opened['focused']}")

            # The dialog is on top of the page, and a dialog wider than the
            # phone is the same defect one layer up.
            wide = json.loads(browser.evaluate_raw(
                "JSON.stringify({scroll: "
                "document.documentElement.scrollWidth, view: "
                "document.documentElement.clientWidth, dialog: Math.round("
                "document.querySelector('[data-dialog]')"
                ".getBoundingClientRect().width)})"))
            gate.check(wide["scroll"] <= wide["view"],
                       f"the open dialog does not widen the page "
                       f"({wide['dialog']}px in {wide['view']}px)",
                       f"the page became {wide['scroll']}px")

            closed = browser.evaluate_raw(
                "(async () => {"
                "document.querySelector('[data-dialog-cancel]').click();"
                "await new Promise(r => setTimeout(r, 250));"
                "return document.querySelector('[data-dialog]').open;})()")
            gate.check(closed is False, "cancel closes it",
                       f"open is still {closed!r}")

            # And pressing Submit on its own writes nothing at all: only the
            # dialog's own button does, and this gate never presses that one.
            gate.check(written() == at_the_start,
                       "and pressing Submit wrote nothing",
                       f"manual_punch moved to {written()}")

    # **The gate leaves the database as it found it.** This is here because it
    # did not: `guard.record` used to commit, so every run left a guard entry
    # attributed to a guard who was not there, and the `session.rollback()`
    # underneath it did nothing at all. A rollback nobody checks is a comment.
    gate.check(written() == at_the_start,
               f"and this gate wrote nothing: manual_punch is still "
               f"{at_the_start} rows",
               f"it went from {at_the_start} to {written()} — the gate is "
               f"leaving corrections behind, and §13 forbids deleting them")

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
