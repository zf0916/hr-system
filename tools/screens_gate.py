#!/usr/bin/env python3
"""The gate for step 10, piece 2: the read-only screens.

**Three claims, and none of them is held up by reading the code.**

  1. *The screen's cells are the sheet's cells.* Checked twice — once against
     the API payload, and once against the DOM of the page in a real browser,
     because the browser is the last place a cell could quietly change and it
     is the only place anybody reads it.
  2. *The download is the filed record.* Not "the same layout" — the same
     bytes as `hr sheet export`, compared byte for byte. Two exports of the
     same period are also compared with each other, because a file that
     differs from itself run to run cannot be checked against anything.
  3. *The HTTP layer computes nothing.* Checked structurally: what the module
     imports, and whether any route function contains a loop, a comprehension
     or an arithmetic operator. A screen that works an answer out for itself is
     a second place the answer lives (SPEC §7).

Runs on the host, because it needs three things at once: the database, the
running containers, and Docker for the browser.

    uv run python tools/screens_gate.py
    uv run python tools/screens_gate.py --month 2026-08 --no-dom

Exits non-zero on the first thing that disagrees.
"""

from __future__ import annotations

import argparse
import ast
import html.parser
import http.client
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

PLAYWRIGHT_IMAGE = "mcr.microsoft.com/playwright:v1.56.0-noble"
CHROME = ("/ms-playwright/chromium_headless_shell-1194/chrome-linux/"
          "headless_shell")


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


def ask(host: str, port: int, method: str, path: str) -> tuple[int, bytes, dict]:
    connection = http.client.HTTPConnection(host, port, timeout=60)
    try:
        connection.request(method, path, headers={"Connection": "close"})
        response = connection.getresponse()
        return response.status, response.read(), dict(response.getheaders())
    finally:
        connection.close()


def ask_json(host: str, port: int, path: str):
    status, body, _ = ask(host, port, "GET", path)
    if status != 200:
        raise SystemExit(f"GET {path} answered {status}: {body[:200]!r}")
    return json.loads(body)


# ---- reading the rendered page ------------------------------------------


class CellReader(html.parser.HTMLParser):
    """Pull the marked-up cells out of the page's DOM.

    The page carries `data-cell`, `data-kind` and `data-weekday` for exactly
    this: a check that reads the DOM by structure rather than by guessing which
    table column is which.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cells: dict[str, dict] = {}
        self.weekday_shaded: dict[str, bool] = {}
        self.notes: list[str] = []
        self.banner = None
        self.note_unread = False
        self.download = None
        # The frozen edges: what is sticky, how far in it is pinned, and how
        # wide the columns it is pinned past actually are.
        self.column_widths: list[int] = []
        self.frozen: dict[str, list[dict]] = {}
        self.day_heads: list[str] = []
        self.weekday_heads: list[str] = []
        self.grid_scroll: str | None = None
        self.list_headings: list[str] = []
        self._open: tuple[str, dict] | None = None
        self._depth = 0
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if self._open is not None and tag == self._open[0]:
            # Depth, not the first closing tag: a marked element with anything
            # nested inside it would otherwise be captured only as far as the
            # first `</...>`, and half a sentence usually still passes.
            self._depth += 1
        if "data-cell" in attributes or "data-note" in attributes:
            self._open = (tag, attributes)
            self._depth = 0
            self._text = []
        if "data-weekday" in attributes:
            self.weekday_shaded[attributes["data-weekday"]] = (
                "bg-slate-300" in (attributes.get("class") or ""))
        if "data-provisional-banner" in attributes:
            self._open = (tag, attributes)
            self._depth = 0
            self._text = []
        if "data-note-unread" in attributes:
            self.note_unread = True
        if "data-download" in attributes:
            self.download = attributes.get("href")
        if tag == "col" and attributes.get("style"):
            width = attributes["style"].replace("width:", "").replace("px", "")
            self.column_widths.append(int(float(width.strip().rstrip(";"))))
        if "data-frozen" in attributes:
            self.frozen.setdefault(attributes["data-frozen"], []).append({
                "class": attributes.get("class") or "",
                "style": attributes.get("style") or "",
            })
        if "data-day-number" in attributes:
            self.day_heads.append(attributes.get("class") or "")
        if "data-weekday" in attributes:
            self.weekday_heads.append(attributes.get("class") or "")
        if "data-grid-scroll" in attributes:
            self.grid_scroll = attributes.get("class") or ""
        if "data-column-heading" in attributes:
            self.list_headings.append(attributes.get("class") or "")

    def handle_data(self, data):
        if self._open is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if self._open is None or self._open[0] != tag:
            return
        if self._depth:
            self._depth -= 1
            return
        _, attributes = self._open
        text = "".join(self._text)
        if "data-cell" in attributes:
            self.cells[attributes["data-cell"]] = {
                "text": text,
                "kind": attributes.get("data-kind"),
                "shaded": attributes.get("data-shaded"),
                "underlined": "decoration-dotted" in (attributes.get("class") or ""),
            }
        elif "data-provisional-banner" in attributes:
            self.banner = text
        elif "data-note" in attributes:
            self.notes.append(text)
        self._open = None
        self._text = []


def rendered_dom(url: str) -> str:
    """The page as a browser has it, after React has drawn it.

    Chromium's own `--dump-dom` rather than a driver library: the browsers are
    already in the Playwright image, and this needs no package installed at run
    time and no network beyond the compose one.
    """
    result = subprocess.run(
        ["docker", "run", "--rm", "--network", "hr-system_default",
         PLAYWRIGHT_IMAGE, CHROME, "--no-sandbox", "--disable-gpu",
         "--dump-dom", "--virtual-time-budget=10000", url],
        capture_output=True, timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode()[-400:])
    return result.stdout.decode("utf-8", errors="replace")


# ---- reading the code ----------------------------------------------------


def module_source(module) -> ast.Module:
    return ast.parse(pathlib.Path(module.__file__).read_text())


def imports_of(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def route_functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    """Functions decorated with an `@app.get("/api/...")`."""
    routes: dict[str, ast.FunctionDef] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            path = next((a.value for a in decorator.args
                         if isinstance(a, ast.Constant)), None)
            if isinstance(path, str) and path.startswith("/api/"):
                routes[node.name] = node
    return routes


def computation_in(function: ast.FunctionDef) -> list[str]:
    """Anything in this function that works something out.

    A loop, a comprehension or an arithmetic operator in a route handler means
    the answer is being assembled at the HTTP layer, which is the second render
    §7 forbids. Formatting a string the service layer handed over is not that.
    """
    # A type annotation is not code. `str | None` parses to the same node an
    # arithmetic expression does, so the annotations are stepped over rather
    # than reported as four sums in a function that adds nothing.
    annotations: set[int] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.arg) and node.annotation is not None:
            annotations.update(id(inner) for inner in ast.walk(node.annotation))
        elif isinstance(node, ast.FunctionDef) and node.returns is not None:
            annotations.update(id(inner) for inner in ast.walk(node.returns))
        elif isinstance(node, ast.AnnAssign):
            annotations.update(id(inner) for inner in ast.walk(node.annotation))

    found: list[str] = []
    for node in ast.walk(function):
        if id(node) in annotations:
            continue
        if isinstance(node, (ast.For, ast.While, ast.AsyncFor)):
            found.append(f"a loop on line {node.lineno}")
        elif isinstance(node, (ast.ListComp, ast.DictComp, ast.SetComp,
                               ast.GeneratorExp)):
            found.append(f"a comprehension on line {node.lineno}")
        elif isinstance(node, ast.BinOp) and not isinstance(node.op, ast.Mod):
            found.append(f"arithmetic on line {node.lineno}")
    return found


# ---- the gate ------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--hr-port", type=int, default=8090)
    parser.add_argument("--month", default="2026-08")
    parser.add_argument("--employee", default="1627",
                        help="an employee with leave whose stated day count "
                             "differs from the range it covers")
    parser.add_argument("--no-dom", action="store_true",
                        help="skip the browser check (it needs Docker)")
    parser.add_argument("--dom-url", default=None,
                        help="how the browser container reaches the interface")
    parser.add_argument("--dom-url-root", default="http://api:8100/",
                        help="the same host, for the employee list")
    args = parser.parse_args()

    from app import detail as detail_view
    from app import screens, sheet as sheet_view
    from app.alert import (
        is_fixture_serial,
        suppressed_fixtures,
        thresholds,
        unwatched_serials,
    )
    from app.corrections import employee_by_number
    from app.db import Session
    from app.models import AlertSetting

    gate = Gate()
    host, port = args.host, args.hr_port

    # ---- 1. the sheet screen is the sheet ------------------------------
    print("\n-- the screen's cells are app.sheet.render's cells")
    payload = ask_json(host, port, f"/api/sheet?month={args.month}")
    with Session() as session:
        start, end = sheet_view.resolve_period(session, args.month)
        rendered = sheet_view.render(session, start, end)
        expected = sheet_view.to_json(rendered)

    for field_name in ("title", "period_start", "period_end", "headcount",
                       "page_count", "rows_per_page", "provisional_cells",
                       "note_top_left", "note_is_unread"):
        gate.check(payload[field_name] == expected[field_name],
                   f"{field_name} agrees: {expected[field_name]!r}",
                   f"screen {payload[field_name]!r}")

    gate.check(payload["cells"].keys() == expected["cells"].keys(),
               f"the same {len(expected['cells'])} cells, day for day",
               f"{len(payload['cells'])} on the screen")

    for attribute in ("text", "kind", "manual", "leave_code", "provisional"):
        wrong = [key for key in expected["cells"]
                 if payload["cells"].get(key, {}).get(attribute)
                 != expected["cells"][key][attribute]]
        gate.check(not wrong, f"every cell's {attribute} agrees",
                   f"{len(wrong)} differ, first {wrong[:3]}")

    for attribute in ("shaded", "day", "weekday", "shade_reason"):
        wrong = [c["date"] for c, e in zip(payload["columns"], expected["columns"])
                 if c[attribute] != e[attribute]]
        gate.check(not wrong, f"every column's {attribute} agrees",
                   f"{len(wrong)} differ: {wrong[:3]}")

    gate.check(payload["legend"] == expected["legend"],
               f"the legend agrees, {len(expected['legend'])} entries")
    gate.check(payload["notes"] == expected["notes"],
               f"the notes agree, {len(expected['notes'])}")
    gate.check([r["employee_number"] for r in payload["rows"]]
               == [r["employee_number"] for r in expected["rows"]],
               f"the same {len(expected['rows'])} employees, in the same order")

    # The sheet is only worth checking if it says something. A period with no
    # ticks, no times and no codes would pass every check above by being empty.
    kinds = {cell["kind"] for cell in expected["cells"].values()}
    gate.check({"tick", "time", "leave"} <= kinds,
               "and the period under test has ticks, times and leave codes on it",
               f"only {sorted(kinds)}")

    # ---- 2. the same, in a browser -------------------------------------
    if args.no_dom:
        print("\n-- SKIPPED: the browser check (--no-dom). The API agreeing "
              "with the render says nothing about what is drawn.")
    else:
        print("\n-- and the same again, read out of the rendered page")
        url = args.dom_url or f"http://api:8100/sheet?month={args.month}"
        reader = CellReader()
        reader.feed(rendered_dom(url))

        gate.check(reader.cells.keys() == expected["cells"].keys(),
                   f"the page draws all {len(expected['cells'])} cells",
                   f"it drew {len(reader.cells)}")
        wrong = [key for key in expected["cells"]
                 if reader.cells.get(key, {}).get("text")
                 != expected["cells"][key]["text"]]
        gate.check(not wrong, "every drawn cell reads exactly what render says",
                   f"{len(wrong)} differ, first {wrong[:3]}: "
                   + str([(reader.cells.get(k, {}).get('text'),
                           expected['cells'][k]['text']) for k in wrong[:3]]))
        wrong = [key for key in expected["cells"]
                 if reader.cells.get(key, {}).get("kind")
                 != expected["cells"][key]["kind"]]
        gate.check(not wrong, "and says what kind of thing it is",
                   f"{len(wrong)} differ: {wrong[:3]}")

        wrong = [column["date"] for column in expected["columns"]
                 if reader.weekday_shaded.get(column["date"]) != column["shaded"]]
        gate.check(not wrong, "every shaded day is shaded on the page",
                   f"{len(wrong)} differ: {wrong[:3]}")

        # The provisional warning is on the face of the screen, not in a
        # tooltip: a number taken off a provisional schedule should be hard to
        # quote without the warning coming with it.
        if expected["provisional_cells"]:
            gate.check(reader.banner is not None,
                       "the provisional-schedule warning is on the page")
            gate.check(str(expected["provisional_cells"]) in (reader.banner or ""),
                       f"and says how many cells rest on one "
                       f"({expected['provisional_cells']})",
                       f"banner reads {(reader.banner or '')[:80]!r}")
            underlined = sum(1 for cell in reader.cells.values()
                             if cell["underlined"])
            gate.check(underlined == expected["provisional_cells"],
                       "and every one of those cells is marked where it is",
                       f"{underlined} marked, {expected['provisional_cells']} "
                       "expected")
        gate.check(reader.note_unread == expected["note_is_unread"],
                   "the unread top-left note is marked unread, not left blank")
        gate.check(sorted(reader.notes) == sorted(expected["notes"]),
                   f"the page carries the render's {len(expected['notes'])} note(s)",
                   f"page has {len(reader.notes)}")
        gate.check(reader.download and "/api/sheet.xlsx" in reader.download,
                   "and offers the file the export writes",
                   f"download link is {reader.download!r}")

        # **Whole columns shade, cells included.** The file fills every cell in
        # a closed column; a header-only stripe says the day was closed at the
        # top of the sheet and says nothing on the row being read along.
        shaded_dates = {column["date"] for column in expected["columns"]
                        if column["shaded"]}
        wrong = [key for key, cell in reader.cells.items()
                 if (cell["shaded"] == "yes") != (key.split(":", 1)[1] in shaded_dates)]
        gate.check(not wrong, "every cell of a shaded column is shaded too",
                   f"{len(wrong)} differ: {wrong[:3]}")

        print("\n-- the grid freezes its edges, the way the file does")
        scroll = reader.grid_scroll or ""
        gate.check("overflow-auto" in scroll,
                   "the grid scrolls inside its own box", f"class {scroll!r}")
        gate.check("max-h-" in scroll,
                   "with a bounded height, so the horizontal scrollbar is "
                   "reachable without scrolling past every employee first",
                   f"class {scroll!r}")

        # **A sticky offset has to equal the width of everything to its left.**
        # An offset that is merely close pins the column *over* its neighbour
        # instead of beside it, and the days underneath disappear.
        offsets = [0]
        for width in reader.column_widths[:2]:
            offsets.append(offsets[-1] + width)
        for index in ("0", "1", "2"):
            cells = reader.frozen.get(index, [])
            gate.check(len(cells) == len(expected["rows"]) + 2,
                       f"column {index} is frozen in every row and both headers",
                       f"{len(cells)} of {len(expected['rows']) + 2}")
            gate.check(cells and all("sticky" in cell["class"] for cell in cells),
                       f"   and every one of them is sticky")
            found = {re.search(r"left:\s*(-?\d+)", cell["style"]).group(1)
                     for cell in cells if re.search(r"left:\s*(-?\d+)", cell["style"])}
            gate.check(found == {str(offsets[int(index)])},
                       f"   pinned at {offsets[int(index)]}px — the exact width "
                       f"of the columns to its left",
                       f"found {found}, columns are {reader.column_widths[:3]}")

        gate.check(reader.day_heads
                   and all("top-0" in head for head in reader.day_heads),
                   f"the day-number row is frozen to the top "
                   f"({len(reader.day_heads)} columns)")
        gate.check(reader.weekday_heads
                   and all("top-8" in head for head in reader.weekday_heads),
                   "and the weekday row is frozen directly under it")

        print("\n-- and the employee list keeps its headings")
        roster_reader = CellReader()
        roster_reader.feed(rendered_dom(args.dom_url_root or "http://api:8100/"))
        gate.check(len(roster_reader.list_headings) == 7,
                   f"the list draws its 7 headings",
                   f"drew {len(roster_reader.list_headings)}")
        gate.check(roster_reader.list_headings
                   and all("sticky" in heading and "top-0" in heading
                           for heading in roster_reader.list_headings),
                   "and every one of them stays put while the list scrolls",
                   f"{roster_reader.list_headings[:1]}")

    # ---- 3. the download is the filed record ---------------------------
    print("\n-- the download is byte-for-byte `hr sheet export`")
    status, downloaded, headers = ask(
        host, port, "GET", f"/api/sheet.xlsx?month={args.month}")
    gate.check(status == 200, "GET /api/sheet.xlsx is 200", str(status))

    hr = shutil.which("hr")
    with tempfile.TemporaryDirectory() as workspace:
        first = pathlib.Path(workspace) / "one.xlsx"
        second = pathlib.Path(workspace) / "two.xlsx"
        command = [hr] if hr else [sys.executable, "-m", "app.cli"]
        for index, target in enumerate((first, second)):
            # **On purpose, a second apart.** The clock is what leaks into a
            # spreadsheet — the archive's member times and the document's
            # modified stamp are both to the second — so two exports taken
            # inside one second would agree however careless the writer was.
            if index:
                time.sleep(1.1)
            result = subprocess.run(
                command + ["sheet", "export", "--month", args.month,
                           "--out", str(target)],
                capture_output=True)
            if result.returncode != 0:
                raise SystemExit(f"hr sheet export failed: "
                                 f"{result.stderr.decode()[-300:]}")
        exported = first.read_bytes()
        gate.check(exported == second.read_bytes(),
                   "two exports of one period are the same file",
                   f"{len(exported)} bytes vs {second.stat().st_size}")

    gate.check(downloaded == exported,
               f"the download is those exact bytes ({len(exported)})",
               f"download {len(downloaded)} bytes; "
               f"first difference at byte "
               + str(next((i for i, (a, b) in enumerate(zip(downloaded, exported))
                           if a != b), "the end")))
    gate.check(f'filename="attendance_{args.month}.xlsx"'
               in headers.get("content-disposition", ""),
               "and arrives named for the period it covers",
               headers.get("content-disposition", ""))
    gate.check("spreadsheetml" in headers.get("content-type", ""),
               "as a spreadsheet", headers.get("content-type", ""))

    # ---- 4. the HTTP layer computes nothing ----------------------------
    print("\n-- the HTTP layer imports service functions and computes nothing")
    import app.hr_app as hr_app_module
    from app.hr_app import hr_app

    tree = module_source(hr_app_module)
    imports = imports_of(tree)
    application_imports = {name for name in imports if name.split(".")[0] == "app"}
    allowed = {"app", "app.screens", "app.db"}
    gate.check(application_imports <= allowed,
               f"it imports only {sorted(allowed)} of the application",
               f"it also imports {sorted(application_imports - allowed)}")
    for forbidden in ("app.sheet", "app.detail", "app.models", "app.attendance",
                      "app.corrections", "app.hr_entry", "app.routes_iclock"):
        gate.check(forbidden not in imports,
                   f"and never {forbidden}")
    gate.check(not any(name.split(".")[0] == "sqlalchemy" for name in imports),
               "and reaches for no query builder of its own",
               f"{sorted(n for n in imports if n.startswith('sqlalchemy'))}")

    routes = route_functions(tree)
    gate.check(len(routes) >= 4,
               f"{len(routes)} API route functions found to inspect",
               "the inspection found nothing, which is not a pass")
    for name, function in sorted(routes.items()):
        found = computation_in(function)
        gate.check(not found, f"{name}() works nothing out",
                   "; ".join(found))

    # ---- 5. nothing on these screens writes ----------------------------
    print("\n-- piece 2 is read-only, by asking and by shape")
    for method, path in (("POST", "/api/sheet"), ("POST", "/api/employees"),
                         ("DELETE", f"/api/employees/{args.employee}/detail")):
        status, _, _ = ask(host, port, method, path)
        gate.check(status == 405, f"{method} {path} is refused (405)",
                   f"got {status}")
    source = pathlib.Path(hr_app_module.__file__).read_text()
    for verb in ("put", "patch", "delete"):
        gate.check(f"@app.{verb}(" not in source,
                   f"no @app.{verb} route exists on the interface")
    # **Three routes write, and this names all three** (pieces 3, 4 and 5).
    # The list grows one entry per entry screen and is written out rather than
    # counted, so a route that starts writing without a piece behind it is a
    # failure here rather than a number that moved. Piece 2's screens stay
    # read-only, which is what the 405s above are asking.
    posts = sorted(route.path for route in hr_app.routes
                   if "POST" in (getattr(route, "methods", None) or set())
                   and not route.path.startswith("/iclock"))
    gate.check(posts == ["/api/gatepass/entry", "/api/guard/entry",
                         "/api/leave/entry"],
               "the routes that write are the guard's entry, leave entry and "
               "gate pass entry",
               f"these accept POST: {posts}")

    # ---- 6. the detail screen is app.detail ----------------------------
    print("\n-- the detail screen is app.detail, and says what leave says")
    detail_payload = ask_json(
        host, port,
        f"/api/employees/{args.employee}/detail?month={args.month}")
    with Session() as session:
        employee = employee_by_number(session, args.employee)
        built = detail_view.render_detail(session, employee, start, end)
        expected_detail = detail_view.to_json(built)
    gate.check(detail_payload == expected_detail,
               f"every day, punch and leave line agrees "
               f"({len(expected_detail['days'])} days)",
               "the payload and the render differ")

    partial = [line for line in expected_detail["leave"] if line["counts_differ"]]
    gate.check(bool(partial),
               "the employee under test has leave whose stated days differ "
               "from its range",
               f"leave lines: {expected_detail['leave']}")
    if partial:
        line = partial[0]
        spanned = (dt_date(line["period_to"]) - dt_date(line["period_from"])).days + 1
        gate.check(float(line["days_stated"]) != spanned,
                   f"and the screen carries the form's {line['days_stated']} "
                   f"day(s), not the range's {spanned}",
                   "the two are the same number, so this proves nothing")
        gate.check(line["days_spanned"] == spanned,
                   "while still showing the range, so neither number is hidden")

    # ---- 7. the employee list agrees with the database ------------------
    print("\n-- the employee list is the assignment rows, on a date")
    listing = ask_json(host, port, "/api/employees?on=2026-08-31")
    with Session() as session:
        from sqlalchemy import text as sql

        count = session.execute(sql(
            "SELECT count(*) FROM employee_assignment "
            "WHERE effective_from <= :d AND (effective_to IS NULL "
            "OR effective_to >= :d)"), {"d": "2026-08-31"}).scalar()
        unmapped = session.execute(sql(
            "SELECT count(*) FROM employee_assignment a "
            "WHERE a.effective_from <= :d AND (a.effective_to IS NULL "
            "OR a.effective_to >= :d) AND NOT EXISTS (SELECT 1 FROM "
            "device_user_map m WHERE m.employee_id = a.employee_id "
            "AND m.effective_from <= :d AND (m.effective_to IS NULL "
            "OR m.effective_to >= :d))"), {"d": "2026-08-31"}).scalar()
    gate.check(listing["headcount"] == count,
               f"{count} people were assigned on 2026-08-31",
               f"the screen says {listing['headcount']}")
    gate.check(listing["not_enrolled"] == unmapped,
               f"{unmapped} of them have no PIN mapped",
               f"the screen says {listing['not_enrolled']}")
    gate.check(all(person["enrolled"] == bool(person["pins"])
                   for person in listing["people"]),
               "and nobody is marked enrolled without a PIN behind it")

    earlier = ask_json(host, port, "/api/employees?on=2026-05-31")
    gate.check(earlier["headcount"] != listing["headcount"]
               or earlier["people"] != listing["people"],
               "an earlier date gives an earlier roster, not today's",
               "May and August produced identical lists, so the date is "
               "being ignored")

    # ---- 8. gate serials are not devices --------------------------------
    print("\n-- a gate serial is recognisable, and is not on the alert's list")
    with Session() as session:
        pattern = thresholds(session)["alert.fixture_serial_pattern"]
        stray = unwatched_serials(session)
        fixtures = suppressed_fixtures(session)
        gate.check(bool(fixtures),
                   f"{len(fixtures)} fixture serial(s) have pushed and are "
                   "counted",
                   "none found, so the filter is untested here")
        gate.check(all(not is_fixture_serial(session, serial)
                       for serial, _, _ in stray),
                   "no fixture serial is on the unwatched list",
                   f"{[s for s, _, _ in stray if is_fixture_serial(session, s)]}")
        gate.check(all(is_fixture_serial(session, serial)
                       for serial, _, _ in fixtures),
                   "and everything suppressed matched the row that says so")

        # **The pattern must not be able to hide a device.** A real serial and
        # the shape ZKTeco actually ships are both checked against it.
        for real in ("PYA8262300072", "CGXH224160123", "SIM0000000001",
                     "AGATE-1", "GATEKEEPER"):
            gate.check(not is_fixture_serial(session, real),
                       f"{real} is not treated as a fixture")
        for invented in ("GATE-SERVING", "GATE-SHEET", "GATE-ATTENDANCE",
                         "GATE-UNWATCHED"):
            gate.check(is_fixture_serial(session, invented),
                       f"{invented} is")

        # The rule is a row: change the row, the answer changes. Rolled back —
        # a gate does not leave the database different from how it found it.
        row = session.get(AlertSetting, "alert.fixture_serial_pattern")
        gate.check(row is not None and row.value == pattern,
                   "the pattern is a row, not a constant in the code",
                   "there is no alert_setting row for it")
        if row is not None:
            row.value = r"^NOTHINGMATCHESTHIS"
            session.flush()
            gate.check(not is_fixture_serial(session, "GATE-SERVING"),
                       "and an UPDATE to that row changes what is suppressed")
            session.rollback()
        with Session() as check_session:
            gate.check(thresholds(check_session)["alert.fixture_serial_pattern"]
                       == pattern,
                       "the row is as the gate found it")

    print(f"\n{gate.checks} checks")
    if gate.failures:
        print(f"{len(gate.failures)} FAILED:")
        for failure in gate.failures:
            print(f"  - {failure}")
        return 1
    print("clean")
    return 0


def dt_date(text: str):
    import datetime

    return datetime.date.fromisoformat(text)


if __name__ == "__main__":
    raise SystemExit(main())
