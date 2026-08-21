"""The ingestion alert commands.

**Where the warning goes, and why.** `hr alert check` is silent when everything
is fine and **exits 2 when it is not**, printing what is wrong. That makes the
transport a scheduled job rather than a screen: a cron entry or a systemd timer
running this every few minutes surfaces the output the way that host already
surfaces failures, with no new dependency, no daemon, and no channel decision
baked in. **Nobody has to be looking at anything.** The row in `ingestion_alert`
is the durable record — raised and cleared, so an outage has a start, an end and
a length — and `--status-file` writes the current state somewhere a monitoring
agent can watch if the site grows one.

    */5 * * * *  docker compose exec -T api hr alert check

**Deliberately not here:** no notification channel. Telegram and WhatsApp are
parked for the supervisors and belong to Milestone 5's approvals (BUILD.md);
this is HR-facing infrastructure and it stays boring. No auto-recovery either —
the device buffers and re-pushes on its own (SPEC §12), and the job here is to
make sure a person finds out, not to reach for the device.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

from app.alert import CONTACT, check, history, record, thresholds, unwatched_serials
from app.db import Session

STATUS_FILE_ENV = "ALERT_STATUS_FILE"


def _parse_now(text: str | None) -> dt.datetime | None:
    """A moment to check as if it were now. For the gate, and for asking what
    the alert would have said at 3am."""
    if not text:
        return None
    moment = dt.datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    return moment.replace(tzinfo=dt.timezone.utc)


def _write_status_file(path: Path, lines: list[str], alarming: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    path.write_text(
        f"{'ALERT' if alarming else 'ok'}  checked {stamp}\n" + "\n".join(lines) + "\n"
    )


def cmd_check(args) -> int:
    try:
        now = _parse_now(args.now)
    except ValueError:
        print("--now looks like '2026-08-20 03:00:00' (UTC)", file=sys.stderr)
        return 1

    with Session() as session:
        statuses = check(session, now=now)
        written = record(session, statuses)
        session.commit()

        lines: list[str] = []
        for status in statuses:
            if not status.watched:
                since = (status.state_since.strftime("%Y-%m-%d %H:%M")
                         if status.state_since else "?")
                lines.append(
                    f"{status.serial_number} ({status.label}): not watched — "
                    f"{status.why_unwatched}"
                    + (f", since {since}" if status.state_code != "live" else ""))
                continue
            head = (f"{status.serial_number} ({status.label}): "
                    f"last request {status.minutes_since_contact} min ago, "
                    f"last punch "
                    + (f"{status.minutes_since_punch} min ago"
                       if status.minutes_since_punch is not None else "never"))
            lines.append(head)
            lines.append(f"    punches expected now: "
                         f"{'yes' if status.punches_expected else 'no'} — "
                         f"{status.expectation}")
            for alarm in status.alarms:
                lines.append(f"    ALERT [{alarm.kind}] {alarm.detail}")

        stray = unwatched_serials(session)
        if stray:
            lines.append("serials pushing but not on the allowlist — nothing "
                         "watches these:")
            for serial, last, count in stray:
                lines.append(
                    f"    {serial}: {count} requests, last "
                    f"{last.isoformat(sep=' ', timespec='seconds')}. "
                    "`hr devices add` to watch it")

        alarming = [s for s in statuses if s.alarming]
        if args.status_file or os.environ.get(STATUS_FILE_ENV):
            _write_status_file(
                Path(args.status_file or os.environ[STATUS_FILE_ENV]),
                lines, bool(alarming))

        if alarming:
            # Loud, and non-zero: this is what a scheduled job turns into a
            # message on the host the way that host already does it. **Only
            # when something is actually wrong** — a header over a clean check
            # is how a person learns to skim past the word ALERT.
            print("INGESTION ALERT", file=sys.stderr)
            for line in lines:
                print(line, file=sys.stderr)
            for row in written:
                print(f"  recorded: {row.kind} {row.state}", file=sys.stderr)
            return 2

        if args.verbose:
            for line in lines:
                print(line)
            for row in written:
                print(f"  recorded: {row.kind} {row.state}")
            settings = thresholds(session)
            print("thresholds, all rows:")
            for key in sorted(settings):
                print(f"  {key:42} {settings[key]}")
        elif written:
            # A clear is worth one line even in quiet mode: it is the end of an
            # outage, and the only place it would otherwise show is a table.
            for row in written:
                print(f"{row.serial_number}: {row.kind} {row.state} — {row.detail}")
    return 0


def cmd_history(args) -> int:
    with Session() as session:
        rows = history(session, limit=args.limit)
        if not rows:
            print("no alert has ever been raised or cleared")
            return 0
        print(f"{'when':<28}{'serial':<18}{'kind':<9}{'state':<9}"
              f"{'silent':>7}  detail")
        for row in reversed(rows):
            silent = "" if row.minutes_silent is None else f"{row.minutes_silent}m"
            print(f"{row.changed_at.isoformat(sep=' ', timespec='seconds'):<28}"
                  f"{row.serial_number:<18}{row.kind:<9}{row.state:<9}"
                  f"{silent:>7}  {row.detail[:90]}")
    return 0


def add_parsers(sub) -> None:
    alert = sub.add_parser(
        "alert", help="the ingestion alert: warn when punches stop arriving"
    )
    commands = alert.add_subparsers(dest="alert_command", required=True)

    check_parser = commands.add_parser(
        "check",
        help="silent when all is well, exits 2 when it is not — run it from "
             "cron or a systemd timer",
    )
    check_parser.add_argument("--verbose", action="store_true",
                              help="print the state even when nothing is wrong")
    check_parser.add_argument("--now", help="check as if it were this UTC moment")
    check_parser.add_argument("--status-file",
                              help=f"write the current state here (or set "
                                   f"{STATUS_FILE_ENV})")
    check_parser.set_defaults(func=cmd_check)

    hist = commands.add_parser(
        "history", help="every time an alert was raised or cleared")
    hist.add_argument("--limit", type=int, default=40)
    hist.set_defaults(func=cmd_history)
