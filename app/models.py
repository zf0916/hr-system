"""The schema, derived from SPEC.md. The model in code is the single source
(CLAUDE.md, Schema). Step 1 defines two capture layers and the rows the
receiver reads its own behaviour from — nothing below them.

Layer 1  raw_request   every HTTP request from the device, whole, append-only,
                       never validated, deduplicated or cleaned (SPEC §3, §12).
Layer 2  parsed_punch  one row per punch line. Disposable: rebuilt by replaying
                       layer 1 (SPEC §3).

Daily attendance, employees and the sheet are steps 6, 2 and 7. They are not
here on purpose.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    DDL,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Text,
    event,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, mapped_column


class Base(DeclarativeBase):
    pass


# Server-observed instants carry a timezone. Device-reported instants never do:
# they are stored exactly as the device sent them (SPEC §14).
SERVER_TS = TIMESTAMP(timezone=True)
DEVICE_TS = TIMESTAMP(timezone=False)


class RawRequest(Base):
    """Layer 1. Stored whole, before anything looks at it.

    Nothing in this table is validated, decoded, deduplicated or dropped. The
    body is bytes — name fields are GBK on many firmware builds and decoding
    belongs to the parser (SPEC §12). SN, table and Stamp are copied out of the
    query string verbatim so the layer can be replayed without re-parsing URLs;
    no lookup, no padding, no normalisation is done on any of them.
    """

    __tablename__ = "raw_request"

    id = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    received_at = mapped_column(SERVER_TS, nullable=False, server_default=func.now())
    remote_addr = mapped_column(Text)
    method = mapped_column(Text, nullable=False)
    path = mapped_column(Text, nullable=False)
    query_string = mapped_column(Text, nullable=False, default="")
    headers = mapped_column(JSONB, nullable=False)
    content_type = mapped_column(Text)
    body = mapped_column(LargeBinary, nullable=False)
    body_bytes = mapped_column(Integer, nullable=False)

    # Verbatim query parameters. A device PIN is never resolved to an employee
    # here and a serial is never rejected here (SPEC §2, §12, §13).
    serial_number = mapped_column(Text)
    table_param = mapped_column(Text)
    stamp_param = mapped_column(Text)

    # What the receiver answered, so a disputed batch can be reconstructed.
    response_body = mapped_column(Text)


Index("ix_raw_request_received_at", RawRequest.received_at)
Index("ix_raw_request_serial_number", RawRequest.serial_number)
Index("ix_raw_request_table_param", RawRequest.table_param)


# "Append-only" is a property of the table, not of the code that happens to
# write to it today. Dropping and recreating the database still works; a stray
# UPDATE or DELETE does not.
APPEND_ONLY = DDL(
    """
CREATE OR REPLACE FUNCTION raw_request_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'raw_request is append-only (SPEC.md 12): %% rejected', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER raw_request_no_update BEFORE UPDATE ON raw_request
    FOR EACH ROW EXECUTE FUNCTION raw_request_append_only();
CREATE TRIGGER raw_request_no_delete BEFORE DELETE ON raw_request
    FOR EACH ROW EXECUTE FUNCTION raw_request_append_only();
"""
)
event.listen(RawRequest.__table__, "after_create", APPEND_ONLY)


class ParsedPunch(Base):
    """Layer 2. One row per line of an ATTLOG body, including the lines that
    did not parse — a line that fails is a row with parse_ok false, never a
    dropped line and never a changed response (SPEC §12).

    Duplicates live here. Devices re-push after a timeout and nothing at this
    level deduplicates; that happens in daily attendance, which is step 6.
    """

    __tablename__ = "parsed_punch"

    id = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    raw_request_id = mapped_column(
        BigInteger, ForeignKey("raw_request.id"), nullable=False
    )
    parser_version = mapped_column(Text, nullable=False)
    parsed_at = mapped_column(SERVER_TS, nullable=False, server_default=func.now())

    line_no = mapped_column(Integer, nullable=False)
    raw_line = mapped_column(Text, nullable=False)
    decoded_with = mapped_column(Text, nullable=False)
    field_count = mapped_column(Integer, nullable=False)

    # The device PIN as a string, exactly as sent. Not an employee number and
    # not looked up here (SPEC §2, §13).
    pin = mapped_column(Text)

    # Both forms of the device time: the original string, and the same value as
    # a timestamp with no timezone applied to it on the way in (SPEC §14).
    punch_time_text = mapped_column(Text)
    punch_time = mapped_column(DEVICE_TS)

    # Meanings unverified — stored, never interpreted (SPEC §12).
    status_code = mapped_column(Text)
    verify_code = mapped_column(Text)
    workcode = mapped_column(Text)
    reserved_1 = mapped_column(Text)
    reserved_2 = mapped_column(Text)

    parse_ok = mapped_column(Boolean, nullable=False)
    parse_error = mapped_column(Text)


Index("ix_parsed_punch_raw_request_id", ParsedPunch.raw_request_id)
Index("ix_parsed_punch_pin", ParsedPunch.pin)
Index("ix_parsed_punch_punch_time", ParsedPunch.punch_time)
Index("ix_parsed_punch_parse_ok", ParsedPunch.parse_ok)


class Device(Base):
    """The serial-number allowlist. An unknown serial is logged and still gets
    200 OK — access control is network position, not a check in the handler
    (SPEC §12)."""

    __tablename__ = "device"

    serial_number = mapped_column(Text, primary_key=True)
    label = mapped_column(Text, nullable=False)
    note = mapped_column(Text)
    added_at = mapped_column(SERVER_TS, nullable=False, server_default=func.now())


class DeviceOption(Base):
    """The Key=Value lines returned to the device on the handshake (SPEC §12).

    §12 fixes that there are Key=Value lines; it does not fix which. The set is
    assumed and unverified — SPEC §9 A26 — so it is rows that an UPDATE
    corrects, never constants in a handler. A row with serial_number NULL
    applies to every device; a row naming a serial overrides it for that one.
    """

    __tablename__ = "device_option"

    id = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    serial_number = mapped_column(Text)
    key = mapped_column(Text, nullable=False)
    value = mapped_column(Text, nullable=False)
    sort_order = mapped_column(Integer, nullable=False, default=0)
    note = mapped_column(Text)


class ParserSetting(Base):
    """Values the parser reads. Assumed, so they are rows (SPEC §9 A27)."""

    __tablename__ = "parser_setting"

    key = mapped_column(Text, primary_key=True)
    value = mapped_column(Text, nullable=False)
    note = mapped_column(Text)
