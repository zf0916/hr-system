#!/usr/bin/env python3
"""Push a plausible month of punches at the receiver, so the sheet can be read.

**This is not a second ingest path and not a new code path.** It builds the same
ATTLOG bodies the device sends — the observed ten-field line with its trailing
tab (SPEC §12) — and posts them to the same `/iclock/cdata` route over HTTP.
Everything downstream happens exactly as it does for the real device: the raw
layer stores each request whole, the parser reads it, `hr attendance build`
derives the daily rows and the sheet renders them. Nothing here writes a punch,
a daily row or a cell directly.

What it generates, across every employee who has a PIN:

  * two punches on most working days, first in near the group's start and last
    out near its end;
  * late arrivals scattered through the month, with a few employees late often
    enough to cross the 30-minute accumulated threshold (SPEC §5);
  * a handful of early departures;
  * night-shift employees punching out after midnight, so the attendance-day
    rule (SPEC §4) shows on the face of the sheet;
  * some days with one punch only, some with none;
  * **nothing on a Sunday** — the rest day on every seeded schedule;
  * **and nothing on a day the calendar says the factory closes.** A shaded
    National Day column with a full shift's punch times in it is not a
    plausible month — it is a fixture that never asked the calendar. A
    *gazetted* holiday the factory works is a different case and still gets
    punches, because only the `closes` flag closes the factory (SPEC §4);
  * **and nothing from somebody a leave record says was not there.** An `AL`
    cell over a day the same employee punched in and out of is the same defect
    one level down: a fixture describing something that did not happen. Leave
    has to be recorded before this runs for that to work, which is the order
    `tools/demo_stand.py` uses.

**This writes no leave and no gate pass.** It reads leave to know who was
absent; the forms themselves are typed on the screens or by `hr leave add`,
because a fixture that invented one would be inventing a signed form.

It is deterministic: the same seed writes the same month. It adds to whatever is
already captured and removes nothing — the raw layer is append-only.

    uv run python tools/make_month_fixture.py --port 8081
    uv run python tools/make_month_fixture.py --port 8081 --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import http.client
import random
import sys
from dataclasses import dataclass, field
from urllib.parse import urlencode

from sqlalchemy import select

from app.db import Session
from app.hr_entry import leave_by_day
from app.models import DeviceUserMap, Employee, EmployeeAssignment
from app.schedule import effective_holiday, is_rest_day, schedule_for

SERIAL = "SIM0000000001"

# A34: the device's own option push reports ~MaxAttLogCount=20. Read as a
# per-push batch limit, this is how many lines it would send at once, so the
# bodies here are chunked the same way.
BATCH_LINES = 20

# Verify codes the device actually sends (SPEC §12): face and fingerprint.
VERIFY = ("15", "1")


@dataclass
class Person:
    employee_number: str
    pin: str
    group_code: str
    employee_id: int = 0
    often_late: bool = False
    leaves_early: bool = False
    punches: list = field(default_factory=list)


def people(session, on_date: dt.date, rng: random.Random) -> list[Person]:
    """Everybody with a PIN in force, and the group they are in."""
    rows = session.execute(
        select(Employee.employee_number, DeviceUserMap.pin,
               EmployeeAssignment.group_code, Employee.id)
        .join(DeviceUserMap, DeviceUserMap.employee_id == Employee.id)
        .join(EmployeeAssignment,
              EmployeeAssignment.employee_id == Employee.id)
        .where(
            DeviceUserMap.effective_from <= on_date,
            (DeviceUserMap.effective_to.is_(None))
            | (DeviceUserMap.effective_to >= on_date),
            EmployeeAssignment.effective_from <= on_date,
            (EmployeeAssignment.effective_to.is_(None))
            | (EmployeeAssignment.effective_to >= on_date),
        )
        .order_by(Employee.employee_number)
    ).all()

    found = [Person(number, pin, group, employee_id)
             for number, pin, group, employee_id in rows]
    # A few people who are late often enough to accumulate past the 30-minute
    # threshold, and a few who leave early. Chosen by the seed, not by name.
    for person in rng.sample(found, min(6, len(found))):
        person.often_late = True
    for person in rng.sample(found, min(5, len(found))):
        person.leaves_early = True
    return found


def punch_times(person: Person, schedule, day: dt.date,
                rng: random.Random) -> list[dt.datetime]:
    """The punches one person makes on one working day, or none at all."""
    start = dt.datetime.combine(day, schedule.start_time)
    end_day = day + dt.timedelta(days=1) if schedule.end_next_day else day
    end = dt.datetime.combine(end_day, schedule.end_time)

    roll = rng.random()
    if roll < 0.03:
        return []                      # nobody punched: a fact, not an absence
    one_punch_only = roll < 0.08       # a failed punch at one end of the day

    if person.often_late and rng.random() < 0.45:
        # Late enough to count, small enough to be ordinary.
        first_in = start + dt.timedelta(minutes=rng.randint(4, 18),
                                        seconds=rng.randint(0, 59))
    elif rng.random() < 0.08:
        # Everybody is late occasionally.
        first_in = start + dt.timedelta(minutes=rng.randint(1, 9),
                                        seconds=rng.randint(0, 59))
    else:
        first_in = start - dt.timedelta(minutes=rng.randint(1, 14),
                                        seconds=rng.randint(0, 59))

    if one_punch_only:
        return [first_in]

    if person.leaves_early and rng.random() < 0.3:
        last_out = end - dt.timedelta(minutes=rng.randint(20, 95))
    else:
        last_out = end + dt.timedelta(minutes=rng.randint(0, 22),
                                      seconds=rng.randint(0, 59))
    return [first_in, last_out]


def attlog_line(pin: str, at: dt.datetime, verify: str) -> str:
    """The observed shape: ten tab-separated fields and a trailing tab."""
    fields = [pin, at.strftime("%Y-%m-%d %H:%M:%S"), "255", verify] + ["0"] * 6
    return "\t".join(fields) + "\t"


def post(host: str, port: int, lines: list[str]) -> tuple[int, str]:
    body = ("\r\n".join(lines) + "\r\n").encode("ascii")
    params = urlencode({"SN": SERIAL, "table": "ATTLOG", "Stamp": "9999"})
    conn = http.client.HTTPConnection(host, port, timeout=30)
    try:
        conn.request(
            "POST", f"/iclock/cdata?{params}", body=body,
            headers={"User-Agent": "iClock Proxy/1.09", "Connection": "close",
                     "Content-Type": "text/plain",
                     "Content-Length": str(len(body))},
        )
        response = conn.getresponse()
        return response.status, response.read().decode("ascii", "replace").strip()
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--from", dest="start", default="2026-08-01")
    parser.add_argument("--to", dest="end", default="2026-08-31")
    parser.add_argument("--seed", type=int, default=20260801)
    parser.add_argument("--dry-run", action="store_true",
                        help="build the month and report it, post nothing")
    args = parser.parse_args()

    start = dt.datetime.strptime(args.start, "%Y-%m-%d").date()
    end = dt.datetime.strptime(args.end, "%Y-%m-%d").date()
    rng = random.Random(args.seed)

    with Session() as session:
        staff = people(session, start, rng)
        if not staff:
            print("no employee has a PIN in force — load a list first",
                  file=sys.stderr)
            return 1

        # Who a leave record says was not there. Read once, keyed the way the
        # sheet keys it — a punch on a day somebody was on leave is a fixture
        # describing something that did not happen.
        on_leave = set(leave_by_day(session, start, end))

        lines: list[str] = []
        working_days = 0
        rest_days = 0
        closed_days: list[dt.date] = []
        leave_days = 0
        stats = {"two": 0, "one": 0, "none": 0, "late": 0, "early": 0,
                 "after_midnight": 0}

        day = start
        while day <= end:
            # **The calendar closes the factory, not the schedule.** A rest day
            # is a column on the schedule row; a public holiday is a row in the
            # calendar, and only its `closes` flag shuts the doors. Both are
            # skipped here for the same reason: nobody was there to punch.
            holiday = effective_holiday(session, day)
            if holiday is not None and holiday.closes:
                closed_days.append(day)
                rest_days += 1
                day += dt.timedelta(days=1)
                continue

            day_had_work = False
            for person in staff:
                schedule = schedule_for(session, person.group_code, day)
                if schedule is None:
                    continue
                if is_rest_day(schedule, day):
                    continue
                day_had_work = True
                if (person.employee_id, day) in on_leave:
                    leave_days += 1
                    continue
                times = punch_times(person, schedule, day, rng)
                if not times:
                    stats["none"] += 1
                    continue
                stats["two" if len(times) == 2 else "one"] += 1

                scheduled_start = dt.datetime.combine(day, schedule.start_time)
                if times[0] > scheduled_start:
                    stats["late"] += 1
                if len(times) == 2:
                    end_day = (day + dt.timedelta(days=1)
                               if schedule.end_next_day else day)
                    if times[1] < dt.datetime.combine(end_day, schedule.end_time):
                        stats["early"] += 1
                    if times[1].date() != day:
                        stats["after_midnight"] += 1

                for at in times:
                    lines.append(attlog_line(person.pin, at,
                                             rng.choice(VERIFY)))
            if day_had_work:
                working_days += 1
            else:
                rest_days += 1
            day += dt.timedelta(days=1)

    print(f"{len(staff)} employees with a PIN, {start} → {end}")
    print(f"  {working_days} working days, {rest_days} days with nothing on "
          "them")
    if closed_days:
        print(f"  {len(closed_days)} of those "
              f"{'is a day' if len(closed_days) == 1 else 'are days'} the "
              "calendar closes the factory for: "
              + ", ".join(str(d) for d in closed_days))
    else:
        print("  no day in this range is a closed holiday on the calendar — "
              "load one before generating, or the month will have people "
              "punching on a day the sheet shades")
    print(f"  {stats['two']} days with two punches, {stats['one']} with one, "
          f"{stats['none']} with none")
    if leave_days:
        print(f"  {leave_days} employee-day(s) skipped because a leave record "
              "already covers them")
    print(f"  {stats['late']} late arrivals, {stats['early']} early departures, "
          f"{stats['after_midnight']} punches after midnight")
    print(f"  {len(lines)} punch lines in "
          f"{(len(lines) + BATCH_LINES - 1) // BATCH_LINES} pushes of "
          f"{BATCH_LINES} (SPEC §9 A34)")

    if args.dry_run:
        print("\ndry run: nothing was posted")
        print("first three lines, as the device would send them:")
        for line in lines[:3]:
            print(f"  {line!r}")
        return 0

    sent = 0
    for index in range(0, len(lines), BATCH_LINES):
        batch = lines[index:index + BATCH_LINES]
        status, body = post(args.host, args.port, batch)
        if status != 200 or not body.startswith("OK"):
            print(f"the receiver answered {status} {body!r} — stopping",
                  file=sys.stderr)
            return 2
        sent += len(batch)

    print(f"\nposted {sent} punch lines to http://{args.host}:{args.port}"
          "/iclock/cdata — the same route and the same shape the device uses")
    print("next: hr attendance build --from ... --to ..., then hr sheet export")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
