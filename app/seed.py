"""The rows the receiver reads its own behaviour from.

Every assumed value is a row, never a constant (CLAUDE.md). Correcting one of
these is an UPDATE, not a code change — which is the whole reason the device
protocol being unverified is survivable.
"""

from app.models import (
    Device,
    DeviceOption,
    EmployeeNumberRule,
    ParserSetting,
    Role,
    Section,
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
EMPLOYEE_NUMBER_RULE = [
    ("expected_shape", "^[0-9]{4}$", "A29 — anything else stops the import and has to be accepted deliberately"),
    ("key_width", "4", "A28 — the matching key is padded to this width"),
    ("key_pad", "0", "A28 — padded with this character, on the left"),
]


def seed(session) -> None:
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
    session.commit()
