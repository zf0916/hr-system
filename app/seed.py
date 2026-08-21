"""The rows the receiver reads its own behaviour from.

Every assumed value is a row, never a constant (CLAUDE.md). Correcting one of
these is an UPDATE, not a code change — which is the whole reason the device
protocol being unverified is survivable.
"""

import datetime as dt

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


def seed(session) -> None:
    # The states first: a device row carries a foreign key to one, so seeding a
    # device before them fails on the constraint rather than silently.
    for code, label, alerted, note in DEVICE_STATES:
        session.add(DeviceState(code=code, label=label, alerted=alerted,
                                note=note))
    session.flush()

    for serial, label, note in DEVICES:
        session.add(Device(serial_number=serial, label=label, note=note))
    for order, (key, value, note) in enumerate(DEVICE_OPTIONS):
        session.add(
            DeviceOption(
                serial_number=None, key=key, value=value, sort_order=order, note=note
            )
        )
    for key, value, note in PARSER_SETTINGS:
        session.add(ParserSetting(key=key, value=value, note=note))
    for name in SECTIONS:
        session.add(Section(code=name, label=name, note="SPEC §2"))
    for name in ROLES:
        session.add(Role(code=name, label=name, note="SPEC §2, sheet legend"))
    for key, value, note in EMPLOYEE_NUMBER_RULE:
        session.add(EmployeeNumberRule(key=key, value=value, note=note))
    for code, label, note in HOLIDAY_SCOPES:
        session.add(HolidayScope(code=code, label=label, note=note))
    for key, value, note in SITE_SETTINGS:
        session.add(SiteSetting(key=key, value=value, note=note))
    for code, label, path, note in CORRECTION_REASONS:
        session.add(CorrectionReason(code=code, label=label, path=path, note=note))
    for code, label, note in LEAVE_CODES:
        session.add(LeaveCode(code=code, label=label, note=note))
    session.flush()
    for code, label, order, suggested, reason_required, note in LEAVE_TYPES:
        session.add(LeaveType(code=code, label=label, sort_order=order,
                              suggested_sheet_code=suggested,
                              reason_required=reason_required, note=note))
    for code, label, order, note in GATE_PASS_CATEGORIES:
        session.add(GatePassCategory(code=code, label=label, sort_order=order,
                                     note=note))
    for code, label, note in ATTENDANCE_STATUSES:
        session.add(AttendanceStatus(code=code, label=label, note=note))
    for key, value, note in SHEET_SETTINGS:
        session.add(SheetSetting(key=key, value=value, note=note))
    for key, value, note in ALERT_SETTINGS:
        session.add(AlertSetting(key=key, value=value, note=note))
    for code, command_text, label, note in DEVICE_COMMAND_TYPES:
        session.add(DeviceCommandType(code=code, command_text=command_text,
                                      label=label, note=note))
    session.commit()
