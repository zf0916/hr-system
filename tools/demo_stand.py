#!/usr/bin/env python3
"""Stand the demo up on its own database, from the fixtures. Never the working
one.

**Why this exists.** The working database holds what people actually did, and
§13 says none of it can be deleted — including three leave records that a
deliberate mistake in piece 4 invented against employee `0090`, and two
overlapping annual-leave records for the same employee typed by two different
people. `0090`'s August therefore lists five leave records, three of them
identical, and it is the first thing anybody opening a day detail would see.
None of that is a defect in the software. It is real history on a real
database, and the answer is to demonstrate somewhere else rather than to start
deleting the evidence.

So the demo runs against `hr_demo`: a database created from the committed
fixtures, with a plausible August of punches pushed through the receiver's own
route, and a handful of leave records and one gate pass so the sheet has codes
on it. **Every one of those says `DEMO FIXTURE` in its "entered by" column**, so
anything HR types during the demo is distinguishable from what was there
before them.

    uv run python tools/demo_stand.py up      # build it and leave it running
    uv run python tools/demo_stand.py status  # what is on it
    uv run python tools/demo_stand.py down    # drop the database, kill the container

The working database is never opened by this tool. The names it creates are
fixed and obvious, and the drop at the end is of that name only.
"""

from __future__ import annotations

import argparse
import http.client
import os
import subprocess
import sys
import time

IMAGE = "hr-system-api"
NETWORK = "hr-system_default"
CONTAINER = "hr-demo"
DATABASE = "hr_demo"

# Its own ports. 8081 and 8090 belong to the working stack and are not touched;
# 8099 is the throwaway the gates use.
HR_PORT = 8095
RECEIVER_PORT = 8085

MONTH_START, MONTH_END = "2026-08-01", "2026-08-31"

# Leave and one gate pass, so the sheet has codes on it and the day detail has
# a pass on it. **Spread across several employees on purpose** — a demo where
# everything hangs off one number teaches the audience that number, and that is
# how `0090` became the number everybody typed.
#
# One coded, one with a type the legend has no letter for, and one half day:
# between them they show A48, A49 and A15 on the face of the sheet.
LEAVE = [
    # employee, type, from, to, days, sheet code, applied, reason
    ("0657", "ANNUAL", "2026-08-10", "2026-08-12", "3", "AL", "2026-08-03",
     None),
    ("2001", "SICK", "2026-08-17", "2026-08-18", "1.5", "MC", "2026-08-17",
     None),
    ("2014", "COMPASSIONATE", "2026-08-06", "2026-08-06", "1", None,
     "2026-08-05", None),
    ("2027", "UNPAID", "2026-08-24", "2026-08-25", "2", "UL", "2026-08-14",
     "family matter at home"),
]
GATE_PASS = ("2005", "2026-08-19", "PERSONAL", "14:00", "16:30",
             "Klinik Bandar", "dental appointment")

BY = "DEMO FIXTURE"


def compose(*arguments: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *arguments],
                          capture_output=True, text=True, check=check)


def psql(sql: str) -> None:
    result = compose("exec", "-T", "db", "psql", "-U", "hr", "-d", "postgres",
                     "-c", sql, check=False)
    if result.returncode:
        raise SystemExit(result.stderr.strip()[-400:])


def hr(*arguments: str, quiet: bool = False) -> str:
    result = subprocess.run(["docker", "exec", CONTAINER, "hr", *arguments],
                            capture_output=True, text=True)
    if result.returncode:
        raise SystemExit(f"hr {' '.join(arguments)} failed:\n"
                         f"{result.stdout[-800:]}\n{result.stderr[-800:]}")
    if not quiet:
        print("   " + "\n   ".join(result.stdout.strip().splitlines()[-4:]))
    return result.stdout


def wait_for_port(port: int, path: str) -> None:
    for _ in range(180):
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port,
                                                    timeout=2)
            connection.request("GET", path)
            if connection.getresponse().status < 500:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise SystemExit(f"nothing answered on 127.0.0.1:{port}{path}")


def down() -> int:
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    psql(f'DROP DATABASE IF EXISTS "{DATABASE}"')
    print(f"dropped {DATABASE} and {CONTAINER}. "
          "The working database was not opened")
    return 0


def up(hr_port: int, receiver_port: int) -> int:
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True,
                          check=True).stdout.strip()

    print(f"-- a clean {DATABASE}, beside the working one")
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    psql(f'DROP DATABASE IF EXISTS "{DATABASE}"')
    psql(f'CREATE DATABASE "{DATABASE}" OWNER hr')

    subprocess.run(
        ["docker", "run", "-d", "--name", CONTAINER, "--network", NETWORK,
         "-e", f"DATABASE_URL=postgresql+psycopg://hr:hr@db:5432/{DATABASE}",
         "-p", f"127.0.0.1:{hr_port}:8100",
         "-p", f"127.0.0.1:{receiver_port}:8000",
         "-v", f"{root}/fixtures:/srv/fixtures:ro",
         IMAGE],
        capture_output=True, text=True, check=True)
    wait_for_port(hr_port, "/api/health")

    print("-- the schema, and the fixture employee list")
    hr("seed")
    hr("employees", "import", "/srv/fixtures/employees_punch_demo.xlsx",
       "--mapping", "/srv/fixtures/employees_punch_demo.mapping.toml",
       "--allow-new", "group", "--accept-leading-zero-pins")

    print("-- the provisional schedules and the 2026 calendar")
    hr("schedule", "seed-provisional")
    hr("calendar", "import", "/srv/fixtures/holidays_provisional_2026.xlsx",
       "--mapping", "/srv/fixtures/holidays.mapping.toml", "--year", "2026")

    # **Before the punches, deliberately.** `make_month_fixture` skips a day a
    # leave record already covers, so an `AL` cell never sits over a day the
    # same employee punched in and out of — which it would if the forms were
    # typed after the month was generated.
    print("-- leave and a gate pass, so the sheet has codes on it")
    for number, kind, start, end, days, code, applied, reason in LEAVE:
        arguments = ["leave", "add", "--employee", number, "--type", kind,
                     "--from", start, "--to", end, "--days", days,
                     "--applied", applied, "--by", BY]
        if reason:
            arguments += ["--reason", reason]
        arguments += ["--code", code] if code else ["--no-code"]
        hr(*arguments, quiet=True)
    number, date, category, out, back, destination, reason = GATE_PASS
    hr("gatepass", "add", "--employee", number, "--date", date,
       "--category", category, "--out", out, "--in", back,
       "--destination", destination, "--reason", reason, "--by", BY,
       quiet=True)
    print(f"   {len(LEAVE)} leave record(s) and 1 gate pass, all entered by "
          f"{BY!r}")

    print("-- a plausible August, pushed at the receiver the way the device "
          "would")
    # **The same route and the same ten-field line the device sends.** Nothing
    # here writes a punch, a daily row or a cell directly, so the demo's data
    # arrives the way real data will.
    environment = dict(os.environ)
    environment["DATABASE_URL"] = (
        f"postgresql+psycopg://hr:hr@127.0.0.1:5432/{DATABASE}")
    result = subprocess.run(
        [sys.executable, "tools/make_month_fixture.py",
         "--port", str(receiver_port), "--from", MONTH_START,
         "--to", MONTH_END],
        capture_output=True, text=True, env=environment, cwd=root)
    if result.returncode:
        raise SystemExit(f"the month fixture failed:\n{result.stdout[-800:]}"
                         f"\n{result.stderr[-800:]}")
    print("   " + "\n   ".join(result.stdout.strip().splitlines()[:5]))

    print("-- the daily rows")
    hr("attendance", "build", "--from", MONTH_START, "--to", MONTH_END)

    print()
    print(f"the demo is up on {DATABASE}, and the working database was never "
          "opened")
    print(f"  the screens      http://127.0.0.1:{hr_port}/")
    print(f"  the sheet        http://127.0.0.1:{hr_port}/sheet?month=2026-08")
    print(f"  the guard        http://<this machine>:{hr_port}/guard")
    print(f"  the receiver     127.0.0.1:{receiver_port}  (no real device is "
          "pointed here)")
    print("  drop it with     uv run python tools/demo_stand.py down")
    return 0


def status(hr_port: int) -> int:
    running = subprocess.run(
        ["docker", "ps", "--filter", f"name=^{CONTAINER}$", "--format",
         "{{.Status}}"], capture_output=True, text=True).stdout.strip()
    if not running:
        print(f"{CONTAINER} is not running. `demo_stand.py up` builds it")
        return 1
    print(f"{CONTAINER}: {running}")
    print(hr("status", quiet=True))
    print(f"  http://127.0.0.1:{hr_port}/")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("up", "down", "status"))
    parser.add_argument("--hr-port", type=int, default=HR_PORT)
    parser.add_argument("--receiver-port", type=int, default=RECEIVER_PORT)
    arguments = parser.parse_args()

    if arguments.action == "down":
        return down()
    if arguments.action == "status":
        return status(arguments.hr_port)
    return up(arguments.hr_port, arguments.receiver_port)


if __name__ == "__main__":
    raise SystemExit(main())
