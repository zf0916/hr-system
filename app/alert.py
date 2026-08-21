"""Step 9: warn when punches stop arriving.

**Two silences, deliberately not one number.** The device polls `getrequest`
every ten seconds whether or not anybody punches (SPEC §12, the `Delay` option
row), so:

  * **Contact silence** — nothing at all from the device — means the device is
    off, the network is down, or the Cloud Server setting was repointed, which
    SPEC §10 says stops capture silently while the device keeps recording. It
    is checkable at 3am on a Sunday, because the polls do not stop for the
    weekend.
  * **Punch silence** — the device is talking, but no punch has arrived while a
    shift is running on a day the factory is open. A quiet night and a Sunday
    are not this, and a single threshold over "time since the last punch" would
    alarm every weekend and stay silent when the receiver is unplugged on a
    public holiday.

Both thresholds are rows, and so is whether punches are checked at all on a day
the calendar closes (SPEC §9 A43-A45).

**This module reads the database and never talks to the device.** That is what
lets it answer while the receiver is down — the outage it is meant to catch is
exactly the moment the receiver cannot be asked anything. There is no
auto-recovery here and nothing re-requests a batch: the device buffers and
re-pushes by itself (SPEC §12), and this step's whole job is making sure a
person finds out.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.corrections import site_timezone
from app.models import (
    AlertSetting,
    Device,
    DeviceState,
    IngestionAlert,
    ParsedPunch,
    RawRequest,
)
from app.schedule import effective_holiday, is_rest_day, schedule_for, shift_window

CONTACT = "contact"
PUNCH = "punch"
RAISED = "raised"
CLEARED = "cleared"

DEFAULTS = {
    "alert.contact_silence_minutes": "15",
    "alert.punch_silence_minutes": "180",
    "alert.punch_expected_after_minutes": "60",
    "alert.check_punches_when_closed": "no",
    "alert.watch_only_after_first_contact": "yes",
}


@dataclass
class Alarm:
    kind: str
    minutes_silent: int | None
    threshold_minutes: int
    detail: str


@dataclass
class DeviceStatus:
    serial_number: str
    label: str
    last_request_at: dt.datetime | None
    last_punch_at: dt.datetime | None
    minutes_since_contact: int | None
    minutes_since_punch: int | None
    punches_expected: bool
    expectation: str
    alarms: list[Alarm] = field(default_factory=list)
    watched: bool = True
    state_code: str = "live"
    state_label: str = ""
    state_since: dt.datetime | None = None
    state_reason: str | None = None
    why_unwatched: str = ""

    @property
    def alarming(self) -> bool:
        return bool(self.alarms)


def thresholds(session) -> dict[str, str]:
    values = dict(DEFAULTS)
    values.update({row.key: row.value for row in session.scalars(select(AlertSetting))})
    return values


def _is_yes(value: str) -> bool:
    return value.strip().lower() in ("yes", "true", "1", "on")


def _minutes(then: dt.datetime | None, now: dt.datetime) -> int | None:
    if then is None:
        return None
    return int((now - then).total_seconds() // 60)


def punches_expected(session, local_now: dt.datetime,
                     settings: dict[str, str]) -> tuple[bool, str]:
    """Is a shift running right now on a day the factory is open?

    Asked of the schedule rows and the calendar, never of the punches — the
    whole point is to know whether an absence of punches is surprising. Night
    shifts are handled by asking yesterday's window as well as today's, so 02:00
    inside a 19:30-04:30 shift counts as running (SPEC §4).
    """
    check_when_closed = _is_yes(
        settings.get("alert.check_punches_when_closed", "no"))
    after = int(settings.get("alert.punch_expected_after_minutes", "60"))

    for offset in (0, -1):
        day = local_now.date() + dt.timedelta(days=offset)
        holiday = effective_holiday(session, day)
        for schedule in _schedules_on(session, day):
            if not check_when_closed:
                if is_rest_day(schedule, day):
                    continue
                if holiday is not None and holiday.closes:
                    continue
            start, end = shift_window(schedule, day)
            if start + dt.timedelta(minutes=after) <= local_now <= end:
                running = int((local_now - start).total_seconds() // 60)
                return True, (
                    f"{schedule.group_code}'s shift started {start:%H:%M} and has "
                    f"been running {running} minutes"
                )

    day = local_now.date()
    holiday = effective_holiday(session, day)
    if holiday is not None and holiday.closes:
        return False, f"{day} is {holiday.name} and the factory closes for it"
    schedules = _schedules_on(session, day)
    if not schedules:
        return False, f"no schedule is in force on {day}"
    if all(is_rest_day(schedule, day) for schedule in schedules):
        return False, f"{day} is a rest day for every group"
    return False, "no shift has been running long enough for a punch to be due"


def _schedules_on(session, day: dt.date):
    from app.models import EmployeeGroup

    schedules = []
    for group in session.scalars(select(EmployeeGroup.code).order_by(EmployeeGroup.code)):
        schedule = schedule_for(session, group, day)
        if schedule is not None:
            schedules.append(schedule)
    return schedules


def status_for(session, device: Device, now: dt.datetime,
               settings: dict[str, str]) -> DeviceStatus:
    last_request = session.scalar(
        select(func.max(RawRequest.received_at))
        .where(RawRequest.serial_number == device.serial_number)
    )
    last_punch = session.scalar(
        select(func.max(RawRequest.received_at))
        .join(ParsedPunch, ParsedPunch.raw_request_id == RawRequest.id)
        .where(
            RawRequest.serial_number == device.serial_number,
            ParsedPunch.parse_ok.is_(True),
        )
    )

    local_now = now.astimezone(ZoneInfo(site_timezone(session))).replace(tzinfo=None)
    expected, expectation = punches_expected(session, local_now, settings)

    state = session.get(DeviceState, device.state_code)
    status = DeviceStatus(
        serial_number=device.serial_number,
        label=device.label,
        last_request_at=last_request,
        last_punch_at=last_punch,
        minutes_since_contact=_minutes(last_request, now),
        minutes_since_punch=_minutes(last_punch, now),
        punches_expected=expected,
        expectation=expectation,
        state_code=device.state_code,
        state_label=state.label if state else device.state_code,
        state_since=device.state_since,
        state_reason=device.state_reason,
    )

    # **A device that is knowingly down is not an outage.** Silence from a
    # device somebody took off the wall is expected, and an alert nobody can
    # silence except by deleting the serial is what teaches people to ignore
    # the alert. The `alerted` flag is a row on device_state, so this is an
    # UPDATE rather than a branch (SPEC §3).
    if state is not None and not state.alerted:
        status.watched = False
        status.why_unwatched = (
            f"{state.label}"
            + (f" — {device.state_reason}" if device.state_reason else "")
        )
        return status

    # A45: a serial that has never been heard from is not an outage.
    never_heard = last_request is None
    only_after_first = _is_yes(
        settings.get("alert.watch_only_after_first_contact", "yes"))
    if never_heard and only_after_first:
        status.watched = False
        status.why_unwatched = (
            "never heard from — not watched until it is installed (A45)")
        return status

    contact_threshold = int(settings.get("alert.contact_silence_minutes", "15"))
    if never_heard:
        status.alarms.append(Alarm(
            CONTACT, None, contact_threshold,
            "nothing has ever arrived from this serial"))
    elif status.minutes_since_contact > contact_threshold:
        status.alarms.append(Alarm(
            CONTACT, status.minutes_since_contact, contact_threshold,
            f"nothing has arrived for {status.minutes_since_contact} minutes. "
            "The device polls every few seconds, so this is the device off, the "
            "network down, or the Cloud Server setting moved (SPEC §10)"))

    punch_threshold = int(settings.get("alert.punch_silence_minutes", "180"))
    if expected:
        if last_punch is None:
            status.alarms.append(Alarm(
                PUNCH, None, punch_threshold,
                f"no punch has ever arrived, and {expectation}"))
        elif status.minutes_since_punch > punch_threshold:
            status.alarms.append(Alarm(
                PUNCH, status.minutes_since_punch, punch_threshold,
                f"no punch for {status.minutes_since_punch} minutes, and "
                f"{expectation}"))
    return status


def check(session, now: dt.datetime | None = None) -> list[DeviceStatus]:
    """Every allowlisted device, as it stands right now."""
    now = now or dt.datetime.now(dt.timezone.utc)
    settings = thresholds(session)
    return [
        status_for(session, device, now, settings)
        for device in session.scalars(
            select(Device).order_by(Device.serial_number)
        )
    ]


def unwatched_serials(session) -> list[tuple[str, dt.datetime, int]]:
    """Serials that have pushed but are not on the allowlist.

    **This is the hole the alert would otherwise have.** The check watches the
    allowlist, so a real device that nobody added is a device nobody is
    watching — and it goes on capturing perfectly until the day it stops, with
    no alarm. §12 says an unknown serial is logged and still answered `200 OK`,
    which is right for the receiver and wrong to leave unsaid here.

    Reported, never alarmed on: a stray probe should not page anybody.
    """
    allowlisted = set(session.scalars(select(Device.serial_number)))
    rows = session.execute(
        select(
            RawRequest.serial_number,
            func.max(RawRequest.received_at),
            func.count(),
        )
        .where(RawRequest.serial_number.is_not(None))
        .group_by(RawRequest.serial_number)
        .order_by(func.max(RawRequest.received_at).desc())
    ).all()
    return [(serial, last, count) for serial, last, count in rows
            if serial not in allowlisted]


def latest_state(session, serial: str, kind: str) -> str:
    row = session.scalars(
        select(IngestionAlert)
        .where(IngestionAlert.serial_number == serial, IngestionAlert.kind == kind)
        .order_by(IngestionAlert.id.desc())
        .limit(1)
    ).first()
    return row.state if row else CLEARED


def record(session, statuses: list[DeviceStatus]) -> list[IngestionAlert]:
    """Write the transitions, and only the transitions.

    A row per check would bury the moment things changed under thousands of
    identical rows; a row per change gives an outage a start, an end and a
    length.
    """
    written: list[IngestionAlert] = []
    for status in statuses:
        if not status.watched:
            # A device that stops being watched clears whatever it had
            # standing. Leaving an alert raised forever on a device somebody
            # deliberately stood down is the noise this whole state exists to
            # remove.
            for kind in (CONTACT, PUNCH):
                if latest_state(session, status.serial_number, kind) == RAISED:
                    row = IngestionAlert(
                        serial_number=status.serial_number, kind=kind,
                        state=CLEARED, minutes_silent=None,
                        threshold_minutes=None,
                        detail=f"no longer watched: {status.why_unwatched}")
                    session.add(row)
                    written.append(row)
            continue
        alarming = {alarm.kind: alarm for alarm in status.alarms}
        for kind in (CONTACT, PUNCH):
            was = latest_state(session, status.serial_number, kind)
            alarm = alarming.get(kind)
            if alarm and was != RAISED:
                row = IngestionAlert(
                    serial_number=status.serial_number, kind=kind, state=RAISED,
                    minutes_silent=alarm.minutes_silent,
                    threshold_minutes=alarm.threshold_minutes,
                    detail=alarm.detail)
                session.add(row)
                written.append(row)
            elif not alarm and was == RAISED:
                if kind == CONTACT:
                    detail = (
                        f"contact resumed; last request "
                        f"{status.minutes_since_contact} minutes ago")
                else:
                    detail = (
                        "punches resumed"
                        if status.minutes_since_punch is not None
                        else "punches are no longer expected right now")
                row = IngestionAlert(
                    serial_number=status.serial_number, kind=kind, state=CLEARED,
                    minutes_silent=(
                        status.minutes_since_contact if kind == CONTACT
                        else status.minutes_since_punch),
                    threshold_minutes=None, detail=detail)
                session.add(row)
                written.append(row)
    session.flush()
    return written


def history(session, limit: int = 40) -> list[IngestionAlert]:
    return list(session.scalars(
        select(IngestionAlert).order_by(IngestionAlert.id.desc()).limit(limit)
    ))
