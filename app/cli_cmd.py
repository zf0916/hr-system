"""The device command queue commands.

`hr cmd send` puts one command on a device's queue; the device collects it on
its next poll, seconds later. `hr cmd list` shows what was queued, what was
handed out, what came back, and what came back that nobody asked for.

**Two commands exist, REBOOT and CHECK**, and neither touches a record on the
device. Anything that clears or deletes is a decision to be made in front of
evidence that the device buffers — which nothing has yet proven (SPEC §13,
BUILD.md).
"""

from __future__ import annotations

import sys

from app.commands import command_types, for_serial, queue, state_of
from app.db import Session
from app.models import Device


def cmd_send(args) -> int:
    with Session() as session:
        if session.get(Device, args.serial) is None:
            print(f"{args.serial} is not on the allowlist. `hr devices add` "
                  "first — a command is queued for a device this system knows "
                  "about", file=sys.stderr)
            return 1
        try:
            command = queue(session, args.serial, args.command,
                            queued_by=args.by, note=args.note)
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        session.commit()
        print(f"queued {command.command_text} for {command.serial_number} "
              f"as command {command.id}")
        print("  the device collects it on its next poll — it asks every few "
              "seconds (SPEC §12)")
        print("  the reply line and the result format are documented, not "
              "observed (SPEC §9 A46, A47)")
    return 0


def cmd_list(args) -> int:
    with Session() as session:
        rows = for_serial(session, args.serial, limit=args.limit)
        if not rows:
            print(f"nothing queued or reported for {args.serial}")
            return 0
        width = max([10] + [len(r.command_text or "?") for r in rows]) + 2
        print(f"{'id':>5}  {'command':<{width}}{'state':<26}{'queued':<21}"
              f"{'handed out':<21}{'result':<21}return")
        for row in reversed(rows):
            def stamp(value):
                return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""
            print(f"{row.id:>5}  {(row.command_text or '?'):<{width}}"
                  f"{state_of(row):<26}{stamp(row.queued_at):<21}"
                  f"{stamp(row.handed_out_at):<21}{stamp(row.result_at):<21}"
                  f"{row.return_code or ''}")
            if row.unsolicited:
                print(f"         reported id {row.reported_id!r}, body "
                      f"{row.result_body!r} — nobody issued this")
    return 0


def cmd_types(args) -> int:
    with Session() as session:
        for row in command_types(session):
            print(f"{row.code:<10}{row.command_text:<12}{row.label}")
            if row.note:
                print(f"          {row.note}")
    print("there is deliberately no command that clears or deletes anything "
          "(SPEC §13)")
    return 0


def add_parsers(sub) -> None:
    cmd = sub.add_parser("cmd", help="the device command queue")
    commands = cmd.add_subparsers(dest="cmd_command", required=True)

    send = commands.add_parser("send", help="queue one command for a device")
    send.add_argument("serial")
    send.add_argument("command", help="REBOOT or CHECK")
    send.add_argument("--by", default="hr", help="who queued it")
    send.add_argument("--note")
    send.set_defaults(func=cmd_send)

    listing = commands.add_parser("list", help="what a device was sent, and said")
    listing.add_argument("serial")
    listing.add_argument("--limit", type=int, default=40)
    listing.set_defaults(func=cmd_list)

    commands.add_parser(
        "types", help="the commands this system will send"
    ).set_defaults(func=cmd_types)
