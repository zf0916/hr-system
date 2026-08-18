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
    CheckConstraint,
    DDL,
    Date,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Text,
    Time,
    event,
    func,
    literal_column,
)
from sqlalchemy.dialects.postgresql import ARRAY, ExcludeConstraint, JSONB, TIMESTAMP
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


# ---------------------------------------------------------------------------
# Step 2: employees.
#
# Identity is one row that never changes. Everything that can change over time
# is a dated row beside it, because re-rendering a past period has to use the
# section, role, group and headcount that were in force then, not today's
# (SPEC §2).
#
# Nothing here is resolved at capture. A device PIN is a string the device
# sent; the mapping from PIN to employee lives in its own dated table so that a
# wrong assumption about the PIN format (A19, A21) is corrected by remapping
# rows (SPEC §9).
# ---------------------------------------------------------------------------


class EmployeeImport(Base):
    """One run of the importer. Kept so that a load can be traced back to the
    file and the mapping it came from — the real list will be loaded more than
    once before it is right."""

    __tablename__ = "employee_import"

    id = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    imported_at = mapped_column(SERVER_TS, nullable=False, server_default=func.now())
    source_filename = mapped_column(Text, nullable=False)
    source_sha256 = mapped_column(Text, nullable=False)
    mapping_filename = mapped_column(Text, nullable=False)
    mapping_text = mapped_column(Text, nullable=False)
    row_count = mapped_column(Integer, nullable=False)
    note = mapped_column(Text)


class Employee(Base):
    """Identity, and nothing that changes.

    `employee_number` is stored exactly as the source gave it — never padded,
    never stripped, never re-formatted (SPEC §2, §13). Matching is done on
    employee_number_key, which is where padding is allowed to happen.
    """

    __tablename__ = "employee"

    id = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    employee_number = mapped_column(Text, nullable=False, unique=True)
    imported_from_id = mapped_column(BigInteger, ForeignKey("employee_import.id"))
    created_at = mapped_column(SERVER_TS, nullable=False, server_default=func.now())
    note = mapped_column(Text)


class EmployeeNumberKey(Base):
    """The separate matching key SPEC §2 requires.

    The padded form lives here, so a wrong assumption about the number format
    is corrected by rebuilding these rows — `hr employees rekey` — rather than
    by touching a single stored employee number. More than one key may point at
    one employee: `90` and `0090` are the same person.
    """

    __tablename__ = "employee_number_key"

    id = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    employee_id = mapped_column(BigInteger, ForeignKey("employee.id"), nullable=False)
    key = mapped_column(Text, nullable=False, unique=True)
    built_by = mapped_column(Text, nullable=False)
    note = mapped_column(Text)


Index("ix_employee_number_key_employee_id", EmployeeNumberKey.employee_id)


class EmployeeAssignment(Base):
    """What an employee's name, section, role and group were, and from when.

    A change is a new row, not an edit. Re-rendering last March reads the row
    that covered last March (SPEC §2).
    """

    __tablename__ = "employee_assignment"

    id = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    employee_id = mapped_column(BigInteger, ForeignKey("employee.id"), nullable=False)
    effective_from = mapped_column(Date, nullable=False)
    effective_to = mapped_column(Date)  # NULL: still in force
    name = mapped_column(Text, nullable=False)
    section_code = mapped_column(Text, ForeignKey("section.code"), nullable=False)
    role_code = mapped_column(Text, ForeignKey("role.code"), nullable=False)
    group_code = mapped_column(Text, ForeignKey("employee_group.code"), nullable=False)
    imported_from_id = mapped_column(BigInteger, ForeignKey("employee_import.id"))
    note = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="employee_assignment_dates_ordered",
        ),
        # One employee cannot be in two sections on the same day. Enforced by
        # the database, not by whichever code path happens to write next.
        ExcludeConstraint(
            (literal_column("employee_id"), "="),
            (
                literal_column("daterange(effective_from, effective_to, '[]')"),
                "&&",
            ),
            name="employee_assignment_no_overlap",
            using="gist",
        ),
    )


class EmploymentPeriod(Base):
    """Active and left as dates, never a boolean (SPEC §2). Separate rows so
    that someone who leaves and comes back is two periods, not an edit."""

    __tablename__ = "employment_period"

    id = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    employee_id = mapped_column(BigInteger, ForeignKey("employee.id"), nullable=False)
    active_from = mapped_column(Date, nullable=False)
    left_on = mapped_column(Date)  # NULL: still employed
    imported_from_id = mapped_column(BigInteger, ForeignKey("employee_import.id"))
    note = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "left_on IS NULL OR left_on >= active_from",
            name="employment_period_dates_ordered",
        ),
        ExcludeConstraint(
            (literal_column("employee_id"), "="),
            (literal_column("daterange(active_from, left_on, '[]')"), "&&"),
            name="employment_period_no_overlap",
            using="gist",
        ),
    )


class DeviceUserMap(Base):
    """PIN to employee, dated, and deliberately its own table.

    The PIN is stored as the device would send it, as a string. Nothing resolves
    it at capture (SPEC §2, §13); this is read downstream, when punches are
    turned into daily attendance. A19 (the PIN is the employee number) and A21
    (leading zeros survive) are both isolated here, so being wrong about either
    is a remap of these rows.
    """

    __tablename__ = "device_user_map"

    id = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    serial_number = mapped_column(Text)  # NULL: any device
    pin = mapped_column(Text, nullable=False)
    employee_id = mapped_column(BigInteger, ForeignKey("employee.id"), nullable=False)
    effective_from = mapped_column(Date, nullable=False)
    effective_to = mapped_column(Date)
    source = mapped_column(Text, nullable=False)
    imported_from_id = mapped_column(BigInteger, ForeignKey("employee_import.id"))
    note = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="device_user_map_dates_ordered",
        ),
        # One PIN cannot belong to two employees on the same day. That overlap
        # is the buddy-punching hole this system exists to close.
        ExcludeConstraint(
            (literal_column("coalesce(serial_number, '')"), "="),
            (literal_column("pin"), "="),
            (literal_column("daterange(effective_from, effective_to, '[]')"), "&&"),
            name="device_user_map_no_overlap",
            using="gist",
        ),
    )


Index("ix_device_user_map_employee_id", DeviceUserMap.employee_id)


class Section(Base):
    """A column on the attendance sheet. §2 names five and says there are
    others, so this is rows and the importer can be told to add more."""

    __tablename__ = "section"

    code = mapped_column(Text, primary_key=True)
    label = mapped_column(Text, nullable=False)
    note = mapped_column(Text)


class Role(Base):
    """A row colour on the sheet (SPEC §2)."""

    __tablename__ = "role"

    code = mapped_column(Text, primary_key=True)
    label = mapped_column(Text, nullable=False)
    note = mapped_column(Text)


class EmployeeGroup(Base):
    """Decides schedule and break length (SPEC §2). Which groups exist is
    parked and unanswered, so this table starts empty — the first real list
    defines it, in front of someone who can see what got created."""

    __tablename__ = "employee_group"

    code = mapped_column(Text, primary_key=True)
    label = mapped_column(Text, nullable=False)
    note = mapped_column(Text)


class EmployeeNumberRule(Base):
    """How an employee number is expected to look, and how its matching key is
    built. Both are assumed (SPEC §9 A28, A29), so both are rows: being wrong
    is an UPDATE and a rekey, not a code change."""

    __tablename__ = "employee_number_rule"

    key = mapped_column(Text, primary_key=True)
    value = mapped_column(Text, nullable=False)
    note = mapped_column(Text)


# ---------------------------------------------------------------------------
# Step 3: schedule and calendar.
#
# Stored per group and effective-dated (SPEC §4), so that re-rendering a past
# period uses what was in force then. Rest day is a column on the schedule row
# rather than a setting somewhere else, because a group is what decides shift
# and break — and, until HR says otherwise, may decide its rest day too.
#
# Public holidays and rest days drive whole columns on the sheet and are never
# entered per employee (SPEC §4).
# ---------------------------------------------------------------------------


class GroupSchedule(Base):
    """One group's working day, from a date until it is superseded.

    A shift that ends after midnight says so on the row — `end_next_day` — and
    the attendance day is worked out from that window. Nothing downstream has
    to infer a crossed midnight by comparing two times.
    """

    __tablename__ = "group_schedule"

    id = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    group_code = mapped_column(
        Text, ForeignKey("employee_group.code"), nullable=False
    )
    effective_from = mapped_column(Date, nullable=False)
    effective_to = mapped_column(Date)  # NULL: still in force

    start_time = mapped_column(Time, nullable=False)
    end_time = mapped_column(Time, nullable=False)
    end_next_day = mapped_column(Boolean, nullable=False, default=False)

    break_start = mapped_column(Time)
    break_end = mapped_column(Time)
    break_start_next_day = mapped_column(Boolean, nullable=False, default=False)
    break_end_next_day = mapped_column(Boolean, nullable=False, default=False)

    # A4 — assumed 0, and per group rather than global, because a decision to
    # change it may not land on everyone at once.
    grace_minutes = mapped_column(Integer, nullable=False, default=0)

    # ISO weekdays: 1 Monday … 7 Sunday. A column on the row, not a global
    # setting (SPEC §4: rest days shade columns, driven by the calendar).
    rest_weekdays = mapped_column(ARRAY(Integer), nullable=False)

    # A30 — how far outside the scheduled window a punch still belongs to this
    # attendance day. Rows, because the right margin is a guess until real
    # punches arrive.
    window_before_minutes = mapped_column(Integer, nullable=False, default=240)
    window_after_minutes = mapped_column(Integer, nullable=False, default=240)

    provisional = mapped_column(Boolean, nullable=False, default=True)
    source = mapped_column(Text)
    note = mapped_column(Text)
    created_at = mapped_column(SERVER_TS, nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="group_schedule_dates_ordered",
        ),
        # A shift that ends at or before it starts has to say it crosses
        # midnight. Night shift 19:30–04:30 is legal; 19:30–04:30 on the same
        # day is not, and is the mistake that would quietly halve a shift.
        CheckConstraint(
            "end_next_day OR end_time > start_time",
            name="group_schedule_end_after_start",
        ),
        CheckConstraint("grace_minutes >= 0", name="group_schedule_grace_positive"),
        CheckConstraint(
            "window_before_minutes >= 0 AND window_after_minutes >= 0",
            name="group_schedule_window_positive",
        ),
        CheckConstraint(
            "rest_weekdays <@ ARRAY[1,2,3,4,5,6,7]",
            name="group_schedule_rest_weekdays_valid",
        ),
        CheckConstraint(
            "(break_start IS NULL) = (break_end IS NULL)",
            name="group_schedule_break_both_or_neither",
        ),
        # One group cannot have two schedules in force on the same day.
        ExcludeConstraint(
            (literal_column("group_code"), "="),
            (literal_column("daterange(effective_from, effective_to, '[]')"), "&&"),
            name="group_schedule_no_overlap",
            using="gist",
        ),
    )


class HolidayScope(Base):
    """Federal, Melaka state, or the company's own closure (SPEC §4)."""

    __tablename__ = "holiday_scope"

    code = mapped_column(Text, primary_key=True)
    label = mapped_column(Text, nullable=False)
    note = mapped_column(Text)


class HolidayUpload(Base):
    """One load of a year's holidays, kept so a calendar can be traced to the
    file it came from."""

    __tablename__ = "holiday_upload"

    id = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uploaded_at = mapped_column(SERVER_TS, nullable=False, server_default=func.now())
    year = mapped_column(Integer, nullable=False)
    source_filename = mapped_column(Text, nullable=False)
    source_sha256 = mapped_column(Text, nullable=False)
    mapping_filename = mapped_column(Text, nullable=False)
    mapping_text = mapped_column(Text, nullable=False)
    row_count = mapped_column(Integer, nullable=False)
    provisional = mapped_column(Boolean, nullable=False, default=True)
    note = mapped_column(Text)


class Holiday(Base):
    """A holiday as the uploaded list has it.

    `closes` is the flag that matters on the floor: a gazetted public holiday
    the factory works is a real case, and it is a different fact from the day
    being gazetted at all.

    One row per date. Two holidays falling on one date are one row with one
    name — otherwise the calendar has to decide which of them wins.
    """

    __tablename__ = "holiday"

    id = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    holiday_date = mapped_column(Date, nullable=False, unique=True)
    name = mapped_column(Text, nullable=False)
    scope_code = mapped_column(Text, ForeignKey("holiday_scope.code"), nullable=False)
    closes = mapped_column(Boolean, nullable=False)
    provisional = mapped_column(Boolean, nullable=False, default=True)
    upload_id = mapped_column(BigInteger, ForeignKey("holiday_upload.id"))
    note = mapped_column(Text)


class HolidayAdjustment(Base):
    """A per-date change, kept apart from the uploaded list.

    The same shape as every other correction in this system: a row beside the
    data, never an edit to it, carrying who made it and why (SPEC §3). Because
    it is a separate row it survives a re-upload of the year, and the re-upload
    reports which adjustments still change something.
    """

    __tablename__ = "holiday_adjustment"

    id = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    made_at = mapped_column(SERVER_TS, nullable=False, server_default=func.now())
    holiday_date = mapped_column(Date, nullable=False)
    action = mapped_column(Text, nullable=False)  # 'set' or 'remove'
    name = mapped_column(Text)
    scope_code = mapped_column(Text, ForeignKey("holiday_scope.code"))
    closes = mapped_column(Boolean)
    reason = mapped_column(Text, nullable=False)
    made_by = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "action IN ('set', 'remove')", name="holiday_adjustment_action_known"
        ),
    )


Index("ix_holiday_adjustment_date", HolidayAdjustment.holiday_date)
