"""The rows the receiver reads its own behaviour from.

Every assumed value is a row, never a constant (CLAUDE.md). Correcting one of
these is an UPDATE, not a code change — which is the whole reason the device
protocol being unverified is survivable.
"""

import datetime as dt

from app.alert import FIXTURE_SERIAL_PATTERN
from app.models import (
    AlertSetting,
    AttendanceStatus,
    CorrectionReason,
    Device,
    DeviceCommandType,
    DeviceOption,
    DeviceState,
    GatePassCategory,
    LeaveCode,
    LeaveType,
    EmployeeNumberRule,
    HolidayScope,
    ParserSetting,
    Role,
    Section,
    ScreenUser,
    SheetSetting,
    SiteSetting,
)

# SPEC §9 A26. §12 fixes that the handshake answers `GET OPTION FROM: {SN}`
# and then Key=Value lines. It does not fix which lines. This set is assembled
# from ZKTeco push documentation and is unverified — the first real handshake
# settles it, and settling it is an UPDATE on these rows.
DEVICE_OPTIONS = [
    ("Stamp", "9999", "last ATTLOG stamp the device should resume from"),
    ("OpStamp", "9999", "same, for OPERLOG"),
    ("ErrorDelay", "30", "seconds before the device retries a failed push"),
    ("Delay", "10", "seconds between getrequest polls"),
    ("TransTimes", "00:00;14:05", "times of day for a full re-send"),
    ("TransInterval", "1", "minutes between transmissions"),
    ("TransFlag", "1111000000", "which tables the device pushes"),
    ("TimeZone", "8", "device timezone +8 (SPEC §10)"),
    ("Realtime", "1", "push each punch as it happens rather than in batches"),
    ("Encrypt", "0", "plain HTTP on an isolated segment (SPEC §12)"),
]

# SPEC §9 A27. Name fields are GBK on many firmware builds; the body is never
# decoded at capture, so the parser tries these in order. The last one cannot
# fail, so a line always survives as something a person can look at.
PARSER_SETTINGS = [
    (
        "attlog.decode_order",
        "utf-8,gbk,latin-1",
        "codecs tried in order when decoding a stored ATTLOG body",
    ),
]

# The allowlist. An unknown serial is logged and still answered 200 OK.
# The real device serial is added when the adapter arrives and it first pushes
# (BUILD.md, "When the adapter arrives").
DEVICES = [
    (
        "SIM0000000001",
        "device simulator",
        "tools/adms_sim.py — stands in for the device until it is powered",
    ),
]


# The five §2 names, as they are written on the sheet. The value is the code —
# inventing a code scheme would be inventing something HR has not said. §2 says
# there are others; the importer adds them when told to, and says which.
SECTIONS = ["PACK ASSY", "QC", "MAINT", "PROJECT DOOR", "WAREHOUSE"]

# The row colours from the sheet legend (SPEC §2).
ROLES = [
    "Management/Office",
    "Production Assistant",
    "HOD/Supervisor",
    "QA/QC",
    "Assistant Supervisor",
    "Charge Hand",
]

# SPEC §9 A28 and A29. Whether the number is always four digits is parked and
# unanswered, so the shape it is expected to take and the way its matching key
# is built are both rows. Employee groups are deliberately absent: which groups
# exist is parked too, and the first real list is what defines them.
# SPEC §2. The employee number is four digits, zero-padded, and that is
# confirmed rather than assumed — but **HR's paper writes it short**: the late
# coming record prints `090` and `1601` on the same page. A list writing a
# number short is not a typo and must not stop an import; it is stored as
# written and keyed to four. What stops an import is a number that cannot be
# keyed to four at all — more than four digits, or something that is not
# digits — and only until somebody accepts it deliberately.
EMPLOYEE_NUMBER_RULE = [
    ("expected_shape", "^[0-9]{1,4}$",
     "one to four digits: `090` and `1601` are both ordinary, and both key to "
     "four. Anything else stops the import and has to be accepted deliberately"),
    ("key_width", "4", "the matching key is padded to this width (SPEC §2)"),
    ("key_pad", "0", "padded with this character, on the left (SPEC §2)"),
]


# SPEC §4: Malaysia federal plus Melaka state. `company` is for a day the
# factory closes that no government gazetted.
HOLIDAY_SCOPES = [
    ("federal", "Malaysia federal public holiday", "SPEC §4"),
    ("melaka", "Melaka state public holiday", "SPEC §4"),
    ("company", "company closure, not gazetted", "SPEC §4"),
]

# SPEC §9 A31 — provisional, and marked provisional in the database.
#
# The shift times are SPEC §4: day 08:00–17:30 is assumed (A1–A2), night
# 19:30–04:30 and both break windows are from the sheet note, grace 0 is A4,
# and Sunday as the rest day is from the sheet legend. What is new here is
# *which group runs which shift*, and that is a guess: the group codes came
# from the sample list, not from HR. Nothing is applied to a group that is not
# named here — an unknown group stops, rather than being given a shift.
#
# Night shift ends the next morning, and the row says so. Its break is unknown
# and is left empty rather than invented.
PROVISIONAL_SHIFTS = {
    "DAY-PROD": {
        "start_time": dt.time(8, 0),
        "end_time": dt.time(17, 30),
        "end_next_day": False,
        "break_start": dt.time(12, 30),
        "break_end": dt.time(13, 15),
        "note": "day shift, production break (SPEC §4)",
    },
    "NIGHT-PROD": {
        "start_time": dt.time(19, 30),
        "end_time": dt.time(4, 30),
        "end_next_day": True,
        "break_start": None,
        "break_end": None,
        "note": "night shift, ends the next morning; break unknown (SPEC §4)",
    },
    "OFFICE": {
        "start_time": dt.time(8, 0),
        "end_time": dt.time(17, 30),
        "end_next_day": False,
        "break_start": dt.time(12, 30),
        "break_end": dt.time(13, 30),
        "note": "office break (SPEC §4)",
    },
}

# Sunday, from the sheet legend. A column on each schedule row, so a group that
# rests on another day is a row, not a code change.
PROVISIONAL_REST_WEEKDAYS = [7]


# SPEC §9 A32. The device is set to +8 (SPEC §10) and the factory is in Melaka.
# A guard entry is stamped by the server, and this is what turns that instant
# into the local time a device punch would have carried.
SITE_SETTINGS = [
    ("timezone", "Asia/Kuala_Lumpur", "A32 — used to read a server stamp as a local punch time"),
]

# The reasons each correction path may give (SPEC §3). The guard picks from a
# list; HR gives any reason, in words.
CORRECTION_REASONS = [
    ("biometric_failed", "Biometric failed at the device", "guard", "SPEC §3"),
    ("not_enrolled", "Not enrolled yet", "guard", "SPEC §3"),
]


# SPEC §3. What the punches on a day amount to, as a fact — and nothing more.
# There is no `absent` row, and adding one would be the collapse §13 forbids:
# no punch is a fact, absence is an HR judgement and needs leave, which is
# step 5. A day with one punch is its own status because it is its own fact
# (SPEC §9 A35).
ATTENDANCE_STATUSES = [
    ("punches_recorded", "Two or more punches on the record",
     "a first in and a last out both exist"),
    ("one_punch", "One punch on the record",
     "a first in and no last out — the device does not label direction"),
    ("no_punch", "No punch on the record",
     "a fact, never an absence: absence needs leave (SPEC §3, §13)"),
]


# SPEC §9 A38–A42. Everything the sheet would otherwise hard-code. The layout
# itself comes from HR's existing sheet, which has been analysed; these are the
# values in it that nobody has confirmed.
SHEET_SETTINGS = [
    ("sheet.title", "DAILY WORKERS ATTENDANCE",
     "the sheet's own name (SPEC §7)"),
    ("sheet.period_rule", "calendar_month",
     "A40 — which period one sheet covers. Unconfirmed: the 10th/15th/20th "
     "cut-offs may be deadlines rather than boundaries (SPEC §5)"),
    ("sheet.rows_per_page", "30",
     "A39 — headcount and page count are unread. The real sheet's row count "
     "settles it"),
    ("sheet.note_top_left", "",
     "A41 — the note in the sheet's top-left has never been read. Empty on "
     "purpose: the renderer marks it unread rather than guessing"),
    ("sheet.mark_on_schedule", "✓",
     "a punch inside the schedule is a tick (SPEC §7)"),
    ("sheet.mark_manual", "*",
     "a manual punch is marked wherever it appears (SPEC §3, §13)"),
    ("sheet.time_format", "%H:%M",
     "how an out-of-schedule punch time is written in a cell"),
]


# SPEC §9 A43-A45. The alert's thresholds, and what it is allowed to alarm
# about. All rows: the right numbers are guesses until the factory has run on
# them, and a wrong one is an UPDATE.
ALERT_SETTINGS = [
    ("alert.fixture_serial_pattern", FIXTURE_SERIAL_PATTERN,
     "A50 — which serials the gates and fixtures invent. They are kept off the "
     "unwatched list, which exists to catch one real device nobody is "
     "watching; five fixture names on it teach a reader to skip it. Counted "
     "rather than dropped: `hr alert fixtures`"),
    ("alert.contact_silence_minutes", "15",
     "A43 — the device polls every 10 seconds (the Delay option row), so 15 "
     "minutes is about 90 missed polls. Contact silence means the device is "
     "off, the network is down, or the Cloud Server setting moved (SPEC §10)"),
    ("alert.punch_silence_minutes", "180",
     "A44 — how long a running shift may produce no punch at all before that "
     "is a warning"),
    ("alert.punch_expected_after_minutes", "60",
     "A44 — how long after a shift starts punches are expected. Before this, "
     "an empty shift is normal rather than suspicious"),
    ("alert.check_punches_when_closed", "no",
     "A44 — a rest day or a holiday the factory closes for produces no "
     "punches by design. Contact is still checked on those days"),
    ("alert.watch_only_after_first_contact", "yes",
     "A45 — a serial on the allowlist that has never been heard from is not "
     "an outage. Otherwise adding a device raises an alert until it is "
     "installed"),
]


# SPEC §11. What this system is willing to send a device, and nothing else.
#
# **There is no command here that clears, deletes or resets anything.** The
# device buffers punches while the receiver is unreachable, that has never been
# proven on this hardware, and an unbuffered clear takes punches with it. Adding
# such a row is a decision somebody makes deliberately, in front of evidence —
# not a line of code somebody edits (SPEC §13).
DEVICE_COMMAND_TYPES = [
    ("REBOOT", "REBOOT", "Restart the device",
     "the safest real command there is: it touches no records"),
    ("CHECK", "CHECK", "Ask the device to re-read its own configuration",
     "used to prove the queue works end to end without changing anything"),
]


# SPEC §3. Whether a device is expected to be talking. The `alerted` column is
# what the ingestion alert reads, so standing a device down is an UPDATE and a
# new kind of not-talking is a row.
DEVICE_STATES = [
    ("live", "Live — mounted, powered, expected to be talking", True,
     "the only state the alert watches"),
    ("down", "Knowingly down — out for repair, or not yet mounted", False,
     "silence is expected, so silence is not news"),
    ("retired", "Retired — replaced, or a test serial that has served its "
                "purpose", False,
     "kept on the list because the raw layer holds its requests forever"),
]


# SPEC §6, the sheet legend. Rows, because §13 forbids hard-coding a leave
# code. The legend prints `T / C` on one line; a cell holds one letter, so they
# are two rows.
LEAVE_CODES = [
    ("AL", "Annual leave", None),
    ("MC", "Medical leave", None),
    ("EL", "Emergency leave",
     "written on the sheet; there is no box for it on the application form"),
    ("UL", "Unpaid leave", None),
    ("PH", "Public holiday", None),
    ("AB", "Absent — cut 3 times", "the calculation itself is unconfirmed"),
    ("SS", "Suspended", None),
    ("T", "Temporary", "the legend prints `T / C` on one line"),
    ("C", "Contract", "the legend prints `T / C` on one line"),
]

# SPEC §6, the ticks on the leave application form, in the form's own order.
# The third column is the sheet code the entry screen suggests (A48) — a
# convenience, not a mapping. **Four of the seven have none**, because the
# legend has no letter for them, and the screen offers nothing rather than
# inventing one.
LEAVE_TYPES = [
    ("ANNUAL", "Annual", 1, "AL", False, None),
    ("COMPASSIONATE", "Compassionate", 2, None,
     False, "no legend code exists for this (SPEC §6)"),
    ("HOSPITALIZATION", "Hospitalization", 3, None,
     False, "no legend code exists for this (SPEC §6)"),
    ("SOCSO", "Ind. Accident (SOCSO)", 4, None,
     False, "no legend code exists for this (SPEC §6)"),
    ("SICK", "Sick", 5, "MC", False, "a sick certificate is attached"),
    ("MATERNITY", "Maternity", 6, None,
     False, "no legend code exists for this (SPEC §6)"),
    ("UNPAID", "Unpaid Leave", 7, "UL", True,
     "the form carries a Reason of its own for this one"),
]

# SPEC §5, the four ticks on the gate pass. Medical Treatment is a category and
# nothing else: there is no treatment slip.
GATE_PASS_CATEGORIES = [
    ("OFFICIAL", "Official", 1, None),
    ("PERSONAL", "Personal", 2, "what the specimen time-off record reads"),
    ("MEDICAL_TREATMENT", "Medical Treatment", 3,
     "a reason for leaving the premises, not a document (SPEC §5)"),
    ("OTHERS", "Others", 4, None),
]


SCREEN_USERS = [
    # A51 — the guard roster has never been read. These stand in for real
    # names so the screen works, and they say so on the screen itself; they are
    # replaced by an UPDATE when somebody reads the roster, not corrected.
    ("guard-1", "Guard 1", "guard", "on duty at the guard house", 1, True,
     "A51 — placeholder. The guard roster is unread (BUILD.md, Parked)"),
    ("guard-2", "Guard 2", "guard", "on duty at the guard house", 2, True,
     "A51 — placeholder. The guard roster is unread (BUILD.md, Parked)"),
    # **HR's two, and these are real people**, named by the factory rather than
    # stood in for. They are not provisional and the screen does not say they
    # are: a placeholder warning over a real name teaches a reader to ignore the
    # warning where it is true. The names are as given — no surname and no
    # title has been read off any roster, and neither is needed to attribute a
    # form somebody typed.
    ("hr-aisyah", "Aisyah", "hr", "Human Resource Dept", 1, False,
     "named by the factory; not a placeholder"),
    ("hr-aslida", "Aslida", "hr", "Human Resource Dept", 2, False,
     "named by the factory; not a placeholder"),
]


def _rows() -> list:
    """Every seeded table, in the order the foreign keys need.

    One entry per table, so that adding a table later can seed just that table
    without dropping the database — which now holds forms somebody typed off
    paper and nothing can rebuild (CLAUDE.md).
    """
    return [
        # The states first: a device row carries a foreign key to one, so
        # seeding a device before them fails on the constraint rather than
        # silently.
        (DeviceState, lambda: [
            DeviceState(code=code, label=label, alerted=alerted, note=note)
            for code, label, alerted, note in DEVICE_STATES]),
        (Device, lambda: [
            Device(serial_number=serial, label=label, note=note)
            for serial, label, note in DEVICES]),
        (DeviceOption, lambda: [
            DeviceOption(serial_number=None, key=key, value=value,
                         sort_order=order, note=note)
            for order, (key, value, note) in enumerate(DEVICE_OPTIONS)]),
        (ParserSetting, lambda: [
            ParserSetting(key=key, value=value, note=note)
            for key, value, note in PARSER_SETTINGS]),
        (Section, lambda: [
            Section(code=name, label=name, note="SPEC §2") for name in SECTIONS]),
        (Role, lambda: [
            Role(code=name, label=name, note="SPEC §2, sheet legend")
            for name in ROLES]),
        (EmployeeNumberRule, lambda: [
            EmployeeNumberRule(key=key, value=value, note=note)
            for key, value, note in EMPLOYEE_NUMBER_RULE]),
        (HolidayScope, lambda: [
            HolidayScope(code=code, label=label, note=note)
            for code, label, note in HOLIDAY_SCOPES]),
        (SiteSetting, lambda: [
            SiteSetting(key=key, value=value, note=note)
            for key, value, note in SITE_SETTINGS]),
        (CorrectionReason, lambda: [
            CorrectionReason(code=code, label=label, path=path, note=note)
            for code, label, path, note in CORRECTION_REASONS]),
        (ScreenUser, lambda: [
            ScreenUser(code=code, name=name, screen=screen, label=label,
                       sort_order=order, active=True, provisional=provisional,
                       note=note)
            for code, name, screen, label, order, provisional, note
            in SCREEN_USERS]),
        (LeaveCode, lambda: [
            LeaveCode(code=code, label=label, note=note)
            for code, label, note in LEAVE_CODES]),
        (LeaveType, lambda: [
            LeaveType(code=code, label=label, sort_order=order,
                      suggested_sheet_code=suggested,
                      reason_required=reason_required, note=note)
            for code, label, order, suggested, reason_required, note
            in LEAVE_TYPES]),
        (GatePassCategory, lambda: [
            GatePassCategory(code=code, label=label, sort_order=order, note=note)
            for code, label, order, note in GATE_PASS_CATEGORIES]),
        (AttendanceStatus, lambda: [
            AttendanceStatus(code=code, label=label, note=note)
            for code, label, note in ATTENDANCE_STATUSES]),
        (SheetSetting, lambda: [
            SheetSetting(key=key, value=value, note=note)
            for key, value, note in SHEET_SETTINGS]),
        (AlertSetting, lambda: [
            AlertSetting(key=key, value=value, note=note)
            for key, value, note in ALERT_SETTINGS]),
        (DeviceCommandType, lambda: [
            DeviceCommandType(code=code, command_text=command_text,
                              label=label, note=note)
            for code, command_text, label, note in DEVICE_COMMAND_TYPES]),
    ]


# **The tables that hold nothing anybody typed.** Both are declared disposable
# by SPEC §3 and both have a command that rebuilds them from the layer above.
# When the model gives one of them a new column, dropping and recreating it
# costs a rebuild and nothing else — so `hr seed --add-missing` may do that,
# and may not do it to anything else.
#
# **Written out rather than inferred.** A rule that worked out for itself which
# tables were safe to drop would be one mistake away from dropping a table
# somebody typed into.
REBUILDABLE = {
    "daily_attendance": "hr attendance build --from <date> --to <date>",
    "parsed_punch": "hr replay",
}


def resync_rebuildable(engine) -> list[tuple[str, str]]:
    """Recreate any rebuildable table whose columns no longer match the model.

    Returns (table, the command that refills it) for each one recreated.
    `create_all` adds tables and never alters one, so a new column on a table
    that already exists would otherwise never reach the database — silently,
    and only noticed when something wrote to it.
    """
    from sqlalchemy import inspect as sa_inspect

    from app.models import Base

    inspector = sa_inspect(engine)
    recreated: list[tuple[str, str]] = []
    for name, command in REBUILDABLE.items():
        table = Base.metadata.tables.get(name)
        if table is None or not inspector.has_table(name):
            continue
        present = {column["name"] for column in inspector.get_columns(name)}
        wanted = {column.name for column in table.columns}
        if present == wanted:
            continue
        with engine.begin() as conn:
            table.drop(conn, checkfirst=True)
            table.create(conn)
        recreated.append((name, command))
    return recreated


def _key(model, row) -> tuple:
    """A seeded row's primary key, read off the object rather than assumed."""
    from sqlalchemy import inspect as sa_inspect

    return tuple(getattr(row, column.name)
                 for column in sa_inspect(model).primary_key)


def seed(session, only_missing: bool = False) -> tuple[dict, list[str]]:
    """Write the rows. Returns what it added per table, and what it would not
    decide about.

    `only_missing` adds the rows the seed has and the database does not, **by
    primary key**, and leaves everything else alone. It never updates and never
    deletes: a row HR has corrected is HR's, and a seed that overwrote it would
    quietly undo the correction. That is what makes adding a table — or a row
    to a table that already has some — safe now that the database holds leave
    records, gate passes and guard entries that nothing can rebuild.

    **It compares keys, not contents.** A row whose key is present is skipped
    however far its columns have drifted from what is written here, because the
    drift is the correction. And a seeded row somebody deliberately deleted
    comes back, for the same reason: this cannot tell a deletion from a table
    that never had it.

    **A table whose seeded rows carry no key of their own is left alone and
    named.** `device_option` is the one: its key is a serial the database
    assigns, so every row built here looks new and a second run would insert
    all ten again. It did, the first time this was tried. Adding a row to such
    a table is a deliberate INSERT, not a re-seed, and this says so rather than
    doubling the table quietly.
    """
    from sqlalchemy import func, select

    added: dict[str, list[str]] = {}
    undecidable: list[str] = []
    for model, build in _rows():
        rows = build()
        if only_missing and session.scalar(
                select(func.count()).select_from(model)):
            if any(part is None for row in rows for part in _key(model, row)):
                undecidable.append(model.__tablename__)
                continue
            present = {_key(model, row) for row in session.scalars(select(model))}
            rows = [row for row in rows if _key(model, row) not in present]
        if not rows:
            continue
        for row in rows:
            session.add(row)
        session.flush()
        added[model.__tablename__] = [
            "|".join(str(part) for part in _key(model, row)) for row in rows]
    session.commit()
    return added, undecidable
