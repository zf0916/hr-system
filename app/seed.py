"""The rows the receiver reads its own behaviour from.

Every assumed value is a row, never a constant (CLAUDE.md). Correcting one of
these is an UPDATE, not a code change — which is the whole reason the device
protocol being unverified is survivable.
"""

from app.models import Device, DeviceOption, ParserSetting

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
    session.commit()
