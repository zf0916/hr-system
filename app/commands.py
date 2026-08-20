"""Step 8: the device command queue.

The device asks for work on every poll and reports each result back (SPEC §11).
This module is the queue behind those two routes and nothing else — it does not
push users, set passwords or delete anybody, because **the only commands that
exist are REBOOT and CHECK** (SPEC §13). The device buffers punches while the
receiver is unreachable, that has never been proven on this hardware, and a
command that clears records would take punches with it.

**Both formats here are documented, not observed** (SPEC §9 A46, A47):

    reply to a poll   C:{id}:{CMD}
    result posted     ID={id}&Return={code}&CMD={command}

No command has ever been sent to this device. When the first real REBOOT comes
back, both get corrected the way the punch line was — the id we sent, the id the
device returned, the separator, the return code and the body are all kept
verbatim so that the correction is a reading rather than a re-run.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from sqlalchemy import select

from app.models import DeviceCommand, DeviceCommandType, RawRequest

# A46: what the device is told. The id is ours; the device echoes it back.
COMMAND_LINE = "C:{id}:{command}"

# A47: what the device posts to /iclock/devicecmd. Ampersand-separated
# key=value, in any order and any case, on one or more lines.
RESULT_FIELD = re.compile(r"(?P<key>[A-Za-z_]+)=(?P<value>[^&\r\n]*)")


@dataclass
class Result:
    """One result line, as read. Nothing here is trusted: every field is
    optional, because the only evidence for the format is a document."""

    reported_id: str | None
    return_code: str | None
    command: str | None
    line: str


def command_types(session) -> list[DeviceCommandType]:
    return list(session.scalars(
        select(DeviceCommandType).order_by(DeviceCommandType.code)))


def queue(session, serial_number: str, code: str, *, queued_by: str,
          note: str | None = None) -> DeviceCommand:
    """Put one command on a device's queue.

    The command text comes from its row, not from the caller — which is what
    makes "no destructive commands" a fact about the database rather than a
    habit of whoever is typing.
    """
    command_type = session.get(DeviceCommandType, code.strip().upper())
    if command_type is None:
        allowed = [row.code for row in command_types(session)]
        raise ValueError(
            f"{code!r} is not a command this system sends. Allowed: {allowed}. "
            "There is deliberately no command that clears or deletes anything "
            "on the device (SPEC §13)"
        )
    row = DeviceCommand(
        serial_number=serial_number,
        command_code=command_type.code,
        command_text=command_type.command_text,
        queued_at=dt.datetime.now(dt.timezone.utc),
        queued_by=queued_by,
        note=note,
    )
    session.add(row)
    session.flush()
    return row


def next_for(session, serial_number: str) -> DeviceCommand | None:
    """The oldest queued, un-handed-out command for this serial, or None.

    Scoped to the serial: a command queued for one device is never handed to
    another, however similar their configuration.
    """
    return session.scalars(
        select(DeviceCommand)
        .where(
            DeviceCommand.serial_number == serial_number,
            DeviceCommand.handed_out_at.is_(None),
            DeviceCommand.unsolicited.is_(False),
            DeviceCommand.queued_at.is_not(None),
        )
        .order_by(DeviceCommand.queued_at, DeviceCommand.id)
        .limit(1)
    ).first()


def hand_out(session, command: DeviceCommand) -> str:
    """Mark the command as given to the device and build the reply line.

    Marking and answering happen in the same transaction as the raw request
    that asked, so a command cannot be handed out without the request that took
    it being on the record.
    """
    command.handed_out_at = dt.datetime.now(dt.timezone.utc)
    session.flush()
    return COMMAND_LINE.format(id=command.id, command=command.command_text)


def parse_result(body: bytes, codec: str = "utf-8") -> list[Result]:
    """Read what the device posted. Never raises.

    A body that says nothing recognisable still produces one Result with its
    line kept, because a result nobody can read is still evidence about the
    firmware — and this runs downstream of capture, where the bytes are already
    safe (SPEC §12).
    """
    text = body.decode(codec, errors="replace")
    results: list[Result] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if not line.strip():
            continue
        fields = {m.group("key").lower(): m.group("value")
                  for m in RESULT_FIELD.finditer(line)}
        results.append(Result(
            reported_id=fields.get("id"),
            return_code=fields.get("return"),
            command=fields.get("cmd"),
            line=line.strip(),
        ))
    return results


def record_results(session, raw: RawRequest) -> list[DeviceCommand]:
    """Store every result in this request against the command it belongs to.

    **A result for a command we never sent is stored anyway, flagged, and
    changes nothing about the response** — the same reflex as an unknown serial
    (SPEC §12). It cannot be an error: the device is reporting something that
    happened, and refusing to write it down would not make it un-happen.
    """
    written: list[DeviceCommand] = []
    for result in parse_result(raw.body):
        command = None
        if result.reported_id and result.reported_id.strip().isdigit():
            candidate = session.get(DeviceCommand, int(result.reported_id))
            if (candidate is not None
                    and candidate.serial_number == raw.serial_number
                    and not candidate.unsolicited):
                command = candidate

        if command is None:
            command = DeviceCommand(
                serial_number=raw.serial_number or "",
                unsolicited=True,
                note="a result for a command this system never issued",
            )
            session.add(command)

        command.result_at = raw.received_at or dt.datetime.now(dt.timezone.utc)
        command.result_raw_request_id = raw.id
        command.reported_id = result.reported_id
        command.return_code = result.return_code
        command.result_body = result.line
        if command.unsolicited and result.command:
            command.command_text = result.command
        session.flush()
        written.append(command)
    return written


def for_serial(session, serial_number: str, limit: int = 40) -> list[DeviceCommand]:
    return list(session.scalars(
        select(DeviceCommand)
        .where(DeviceCommand.serial_number == serial_number)
        .order_by(DeviceCommand.id.desc())
        .limit(limit)
    ))


def state_of(command: DeviceCommand) -> str:
    if command.unsolicited:
        return "unsolicited result"
    if command.result_at is not None:
        return "answered"
    if command.handed_out_at is not None:
        return "handed out, no result yet"
    return "queued"
