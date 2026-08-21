#!/usr/bin/env python3
"""The gate for piece 1: the two ports are two applications, proven by asking.

**The claim this exists to hold up:** a Tailscale tunnel can point at the HR
port because the device routes are not served there — not hidden, not
redirected, not behind a check somebody can disable. They are absent (SPEC
§14). The mirror holds too: the receiver serves no interface, because §12's
absolute rules are written for firmware that retries forever on anything but a
plain `200`, and a page has no business inside them.

Everything here is asked over HTTP, of the running containers, the way the
device and a browser would ask. The routing tables are inspected afterwards, so
that a wrong answer and a wrong shape are both caught.

    uv run python tools/serving_gate.py
    uv run python tools/serving_gate.py --hr-port 8090 --device-port 8081

Exits non-zero if either port answers something the other one should.
"""

from __future__ import annotations

import argparse
import http.client
import json
import sys
from dataclasses import dataclass, field


@dataclass
class Answer:
    status: int
    body: bytes
    content_type: str

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


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


def would_run(app, path: str, method: str = "GET") -> str:
    """The name of the handler this app would run for a path, or "" for none.

    Asked of the router rather than read off `app.routes`: `include_router`
    nests a router object instead of copying its routes in, so a walk over the
    top level reports an app carrying an entire receiver as carrying nothing.
    Matching is what the app itself does with a request, and it is what matters.
    """
    from starlette.routing import Match

    scope = {
        "type": "http", "method": method, "path": path, "root_path": "",
        "headers": [], "query_string": b"", "app": app,
    }
    for route in app.routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            endpoint = getattr(route, "endpoint", None)
            return getattr(endpoint, "__name__", None) or getattr(route, "name", "?")
    return ""


def imports_of(module) -> set[str]:
    """What a module imports, read from its source."""
    import ast
    import pathlib as _pathlib

    tree = ast.parse(_pathlib.Path(module.__file__).read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def ask(host: str, port: int, method: str, path: str,
        body: bytes = b"") -> Answer:
    conn = http.client.HTTPConnection(host, port, timeout=15)
    try:
        headers = {"Connection": "close"}
        if body:
            headers["Content-Type"] = "text/plain"
            headers["Content-Length"] = str(len(body))
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        return Answer(response.status, response.read(),
                      response.getheader("content-type") or "")
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--device-port", type=int, default=8081)
    parser.add_argument("--hr-port", type=int, default=8090)
    args = parser.parse_args()
    gate = Gate()

    print("\n-- the receiver still answers the device, on its own port")
    answer = ask(args.host, args.device_port, "GET",
                 "/iclock/getrequest?SN=GATE-SERVING")
    gate.check(answer.status == 200 and answer.text.strip().startswith("OK"),
               "GET /iclock/getrequest is 200 OK",
               f"{answer.status} {answer.text[:60]!r}")
    gate.check(answer.content_type.split(";")[0].strip() == "text/plain",
               "and plain text, as the firmware needs (SPEC §12)",
               f"content-type {answer.content_type!r}")

    print("\n-- the HR interface answers on its own port")
    answer = ask(args.host, args.hr_port, "GET", "/api/health")
    gate.check(answer.status == 200, "GET /api/health is 200",
               f"got {answer.status}")
    health = {}
    if answer.status == 200:
        health = json.loads(answer.text)
        gate.check(health.get("service") == "hr",
                   f"and it is the HR service: {health.get('service')!r}")
        gate.check("reachable" in health.get("database", ""),
                   f"with the database behind it: {health.get('database')!r}")

    answer = ask(args.host, args.hr_port, "GET", "/")
    gate.check(answer.status == 200 and b"<div id=\"root\">" in answer.body,
               "GET / is the built interface",
               f"{answer.status}, {answer.body[:60]!r}")
    gate.check("text/html" in answer.content_type,
               f"served as HTML: {answer.content_type!r}")

    print("\n-- a device path on the HR port is a 404, by asking")
    for method, path, body in (
        ("GET", "/iclock/cdata?SN=GATE&options=all", b""),
        ("POST", "/iclock/cdata?SN=GATE&table=ATTLOG&Stamp=9999",
         b"1\t2026-08-21 08:00:00\t255\t15\t0\t0\t0\t0\t0\t0\t\r\n"),
        ("GET", "/iclock/getrequest?SN=GATE", b""),
        ("POST", "/iclock/devicecmd?SN=GATE", b"ID=1&Return=0&CMD=REBOOT"),
        ("GET", "/iclock/anything/at/all", b""),
    ):
        answer = ask(args.host, args.hr_port, method, path, body)
        gate.check(answer.status == 404, f"{method} {path.split('?')[0]} → 404",
                   f"got {answer.status} {answer.text[:70]!r}")
        # **Not the single-page fallback.** A 200 with an HTML body would be a
        # device reading a web page as a protocol answer (SPEC §12).
        gate.check(b"<div id=\"root\">" not in answer.body,
                   f"   and not the interface's own page",
                   f"body {answer.body[:60]!r}")

    print("\n-- an HR path on the device port is a 404, by asking")
    for path in ("/", "/api/health", "/index.html", "/assets/index.js"):
        answer = ask(args.host, args.device_port, "GET", path)
        gate.check(answer.status == 404, f"GET {path} on the receiver → 404",
                   f"got {answer.status} {answer.text[:70]!r}")

    print("\n-- and the routing tables say the same thing")
    import app.hr_app as hr_app_module
    from app.hr_app import hr_app
    from app.main import app as device_app

    for path, method in (("/iclock/cdata", "GET"), ("/iclock/cdata", "POST"),
                         ("/iclock/getrequest", "GET"),
                         ("/iclock/devicecmd", "POST"),
                         ("/iclock/whatever", "GET")):
        handler = would_run(hr_app, path, method)
        gate.check(handler == "no_device_routes",
                   f"{method} {path} on the HR app runs the refusal, not a "
                   f"device handler",
                   f"it would run {handler!r}")

    for path in ("/", "/api/health", "/index.html"):
        handler = would_run(device_app, path)
        gate.check(handler in ("", "handle"),
                   f"{path} on the receiver runs nothing of the interface",
                   f"it would run {handler!r}")

    # **The interface does not import the receiver.** Mounting the device
    # routes into this app takes an import first, and an app that imports them
    # is one edit — one reordering — away from serving them on the tunnel's
    # port. Absence is the guarantee; a handler that shadows them is not.
    hr_imports = imports_of(hr_app_module)
    gate.check("app.routes_iclock" not in hr_imports,
               "and the HR app does not import the receiver's routes at all",
               f"imports {sorted(i for i in hr_imports if i.startswith('app'))}")
    gate.check(hr_app is not device_app,
               "the two are separate applications, not one with two doors")

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
