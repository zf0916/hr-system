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
    Computed,
    DDL,
    Date,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    Text,
    Time,
    event,
    func,
    literal_column,
    text,
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

    # How many tab-separated pieces the line split into, before any judgement
    # about whether that is the right number.
    field_count = mapped_column(Integer, nullable=False)

    # Every field of the line, positionally, verbatim, unnamed. The device
    # sends ten (SPEC §12) and only four of them have an observed meaning; the
    # rest are here so that nothing is lost and nothing is guessed. A name
    # invites logic, so the other six do not get one.
    fields = mapped_column(ARRAY(Text), nullable=False)

    # The four that are observed. Each is a copy of a position in `fields`,
    # named because §12 says what it is.
    #
    # The device PIN as a string, exactly as sent. Not an employee number and
    # not looked up here (SPEC §2, §13).
    pin = mapped_column(Text)

    # Both forms of the device time: the original string, and the same value as
    # a timestamp with no timezone applied to it on the way in (SPEC §14).
    punch_time_text = mapped_column(Text)
    punch_time = mapped_column(DEVICE_TS)

    # Observed: status is 255 on every punch and verify is 15 for face, 1 for
    # fingerprint (SPEC §12). Stored as strings; only verify is ever read, by
    # the password-punch count.
    status_code = mapped_column(Text)
    verify_code = mapped_column(Text)

    parse_ok = mapped_column(Boolean, nullable=False)
    parse_error = mapped_column(Text)


Index("ix_parsed_punch_raw_request_id", ParsedPunch.raw_request_id)
Index("ix_parsed_punch_pin", ParsedPunch.pin)
Index("ix_parsed_punch_punch_time", ParsedPunch.punch_time)
Index("ix_parsed_punch_parse_ok", ParsedPunch.parse_ok)


class DeviceState(Base):
    """Whether a device is expected to be talking.

    Rows, and the `alerted` flag on the row is what the ingestion alert reads —
    so standing a device down is an UPDATE, and adding a new kind of
    not-talking is a row rather than a branch.

    **A device is not simply on or off the list.** It can be live, knowingly
    down — out for repair, not yet mounted — or finished, like a test serial.
    A permanent alert nobody can silence except by deleting the serial is what
    teaches people to ignore the alert (SPEC §3, §9 A43-A45).
    """

    __tablename__ = "device_state"

    code = mapped_column(Text, primary_key=True)
    label = mapped_column(Text, nullable=False)
    alerted = mapped_column(Boolean, nullable=False)
    note = mapped_column(Text)


class Device(Base):
    """The serial-number allowlist. An unknown serial is logged and still gets
    200 OK — access control is network position, not a check in the handler
    (SPEC §12).

    A serial that stops being watched keeps its row: the raw layer holds its
    requests forever, and the list has to say why it went quiet. Deleting it
    would answer neither question (SPEC §13).
    """

    __tablename__ = "device"

    serial_number = mapped_column(Text, primary_key=True)
    label = mapped_column(Text, nullable=False)
    note = mapped_column(Text)
    added_at = mapped_column(SERVER_TS, nullable=False, server_default=func.now())

    state_code = mapped_column(
        Text, ForeignKey("device_state.code"), nullable=False, default="live"
    )
    state_since = mapped_column(SERVER_TS, nullable=False, server_default=func.now())
    # Why it is in that state, in words, and who put it there. The same reflex
    # as every other adjustment in this system (SPEC §3).
    state_reason = mapped_column(Text)
    state_by = mapped_column(Text)


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


# ---------------------------------------------------------------------------
# Step 4: corrections.
#
# A missed, failed or wrong punch is corrected by adding a row beside the punch
# data, never by editing it (SPEC §3, §13). Every row carries who made it, when,
# and why.
#
# The two paths differ in one structural way, and it is the whole point. A
# guard's entry has no field for a time: "the log alone does not prevent it;
# removing the time field does" (SPEC §3). HR's retroactive entry does have one,
# because a device that was down yesterday can only be corrected by naming
# yesterday.
# ---------------------------------------------------------------------------


class SiteSetting(Base):
    """Where the factory is, in the terms the system needs. Assumed, so rows."""

    __tablename__ = "site_setting"

    key = mapped_column(Text, primary_key=True)
    value = mapped_column(Text, nullable=False)
    note = mapped_column(Text)


class CorrectionReason(Base):
    """The reasons each path may give (SPEC §3).

    The guard picks from a list — biometric failed, not enrolled. HR may give
    any reason, and gives it in words.
    """

    __tablename__ = "correction_reason"

    code = mapped_column(Text, primary_key=True)
    label = mapped_column(Text, nullable=False)
    path = mapped_column(Text, nullable=False)  # 'guard' or 'hr_retroactive'
    note = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "path IN ('guard', 'hr_retroactive')", name="correction_reason_path_known"
        ),
    )


class ManualPunch(Base):
    """A punch a person recorded, not the device.

    There is no value of `path` that means "device". A row in this table is a
    manual punch by construction, and the read that puts it next to device
    punches says so on every row — an unmarked manual punch is indistinguishable
    from a biometric one and recreates the hole the device exists to close
    (SPEC §3, §13).

    Times, and why there are two columns:

      recorded_at    server-stamped, always. When the entry was made.
      asserted_time  the local time the entry claims a punch happened.

    A guard entry has `asserted_time` NULL and the database refuses anything
    else. The guard records that the employee is standing in front of him now;
    what time that is, is the server's to say. An HR retroactive entry must
    carry one — correcting a day the device was down means naming that day.
    """

    __tablename__ = "manual_punch"

    id = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    employee_id = mapped_column(
        BigInteger, ForeignKey("employee.id"), nullable=False
    )
    path = mapped_column(Text, nullable=False)

    # clock_timestamp(), not now(): now() is the transaction's start time, and
    # the stamp on a guard entry is meant to be the moment the entry was made.
    recorded_at = mapped_column(
        SERVER_TS, nullable=False, server_default=text("clock_timestamp()")
    )
    asserted_time = mapped_column(DEVICE_TS)

    # Derived from the schedule in force, at entry — the same rule device
    # punches follow, so a night-shift correction lands on the right day.
    # Rebuildable: `hr corrections rebuild-days`.
    attendance_day = mapped_column(Date, nullable=False)
    schedule_id = mapped_column(BigInteger, ForeignKey("group_schedule.id"))

    reason_code = mapped_column(Text, ForeignKey("correction_reason.code"))
    reason = mapped_column(Text)
    made_by = mapped_column(Text, nullable=False)
    note = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "path IN ('guard', 'hr_retroactive')", name="manual_punch_path_known"
        ),
        # The rule that makes guard entry something other than buddy punching
        # with a log: no time field, enforced here rather than in whichever code
        # path writes next.
        CheckConstraint(
            "(path = 'guard' AND asserted_time IS NULL) OR "
            "(path = 'hr_retroactive' AND asserted_time IS NOT NULL)",
            name="manual_punch_guard_cannot_state_a_time",
        ),
        # A guard picks a reason from the list; HR says why in words. Neither
        # may record nothing.
        CheckConstraint(
            "(path = 'guard' AND reason_code IS NOT NULL) OR "
            "(path = 'hr_retroactive' AND reason IS NOT NULL "
            "AND length(btrim(reason)) > 0)",
            name="manual_punch_reason_recorded",
        ),
        # Attributable to one named person (SPEC §3).
        CheckConstraint(
            "length(btrim(made_by)) > 0", name="manual_punch_made_by_recorded"
        ),
    )


Index("ix_manual_punch_employee_day", ManualPunch.employee_id, ManualPunch.attendance_day)
Index("ix_manual_punch_attendance_day", ManualPunch.attendance_day)


# ---------------------------------------------------------------------------
# Step 6: daily attendance.
#
# Layer 3 of SPEC §3: one row per employee per day, built over parsed punches,
# corrections and the schedule. Every period total is a query over these rows,
# so the rows carry facts and nothing aggregated (SPEC §3).
#
# Three things this layer deliberately does not do:
#
#   * It does not judge. "No punch" is a status because it is a fact; "absent"
#     is not, because absence needs leave, which is step 5 (SPEC §3, §13).
#   * It does not deduct. Late minutes are a figure; the threshold and the
#     decision to deduct belong to §5 and to management.
#   * It does not hide a manual punch. A manual punch counts toward the day's
#     figures and the row says which figure came from one (SPEC §3, §13).
# ---------------------------------------------------------------------------


class AttendanceStatus(Base):
    """What the punches on a day amount to, as a fact.

    Rows, not constants, and deliberately short: two or more punches, exactly
    one, or none. **There is no `absent`.** No punch and an absence are never
    collapsed (SPEC §3, §13), and an absence cannot be decided without leave,
    which does not exist yet.
    """

    __tablename__ = "attendance_status"

    code = mapped_column(Text, primary_key=True)
    label = mapped_column(Text, nullable=False)
    note = mapped_column(Text)


class DailyAttendance(Base):
    """One employee, one attendance day.

    The attendance day is the shift's, not the clock's, and it is decided by
    `schedule.attendance_day_for` rather than re-derived here — a night-shift
    punch at 04:35 belongs to the previous day (SPEC §4, §13).

    Every figure on the row carries what produced it: the group and schedule in
    force on that day, that schedule's scheduled start and grace, and whether
    that schedule row is still provisional. A figure from a provisional
    schedule is readable as provisional without going and looking.

    `first_in` and `last_out` are SPEC §3's words for the day's earliest and
    latest punch. **The device does not label a punch in or out** — status was
    255 on every punch captured and punch state options are off (SPEC §12) — so
    the pair only means anything when there are two or more punches. With one
    punch there is a first in and no last out, and the constraint below is what
    keeps anything from filling that in later (A35).
    """

    __tablename__ = "daily_attendance"

    id = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    employee_id = mapped_column(BigInteger, ForeignKey("employee.id"), nullable=False)
    attendance_day = mapped_column(Date, nullable=False)

    # What was in force on that day, never what is in force now.
    group_code = mapped_column(Text, ForeignKey("employee_group.code"))
    schedule_id = mapped_column(BigInteger, ForeignKey("group_schedule.id"))
    schedule_provisional = mapped_column(Boolean, nullable=False, default=False)
    scheduled_start = mapped_column(DEVICE_TS)
    scheduled_end = mapped_column(DEVICE_TS)
    grace_minutes = mapped_column(Integer)

    # The calendar's two separate facts (SPEC §4).
    is_rest_day = mapped_column(Boolean, nullable=False, default=False)
    holiday_name = mapped_column(Text)
    holiday_closes = mapped_column(Boolean)

    # The day's earliest and latest punch, and where each came from. A manual
    # punch is never silently one of these: the source says so.
    first_in = mapped_column(DEVICE_TS)
    first_in_source = mapped_column(Text)
    first_in_manual = mapped_column(Boolean, nullable=False, default=False)
    last_out = mapped_column(DEVICE_TS)
    last_out_source = mapped_column(Text)
    last_out_manual = mapped_column(Boolean, nullable=False, default=False)

    # Distinct punches, after re-pushes are dropped. A device re-pushes a batch
    # after a timeout and the raw layer keeps every copy; this is the layer that
    # deduplicates, which is what §12 means by "deduplicate downstream" (A37).
    punch_count = mapped_column(Integer, nullable=False, default=0)
    device_punch_count = mapped_column(Integer, nullable=False, default=0)
    manual_punch_count = mapped_column(Integer, nullable=False, default=0)

    # How many device pushes were dropped as copies of a punch already counted.
    # A rising number here is the device retrying, not an employee punching.
    duplicate_pushes = mapped_column(Integer, nullable=False, default=0)

    # A figure, not a deduction. NULL where there is nothing to measure
    # against — no punch, or a day with no scheduled start (A36).
    late_minutes = mapped_column(Integer)

    status_code = mapped_column(
        Text, ForeignKey("attendance_status.code"), nullable=False
    )

    built_at = mapped_column(SERVER_TS, nullable=False, server_default=func.now())
    note = mapped_column(Text)

    __table_args__ = (
        # One row per employee per day, enforced here rather than by whichever
        # code path writes next.
        Index("uq_daily_attendance_employee_day", "employee_id", "attendance_day",
              unique=True),
        CheckConstraint(
            "punch_count = device_punch_count + manual_punch_count",
            name="daily_attendance_counts_add_up",
        ),
        CheckConstraint(
            "(first_in IS NULL) = (punch_count = 0)",
            name="daily_attendance_first_in_iff_punches",
        ),
        # A35, in the database: one punch is a first in and nothing else. A row
        # claiming a last out on a single punch is the day being made to look
        # like a full one.
        CheckConstraint(
            "last_out IS NULL OR punch_count >= 2",
            name="daily_attendance_last_out_needs_two_punches",
        ),
        CheckConstraint(
            "last_out IS NULL OR first_in IS NULL OR last_out >= first_in",
            name="daily_attendance_last_out_after_first_in",
        ),
        CheckConstraint(
            "late_minutes IS NULL OR late_minutes >= 0",
            name="daily_attendance_late_minutes_positive",
        ),
        CheckConstraint(
            "duplicate_pushes >= 0", name="daily_attendance_duplicates_positive"
        ),
        # A figure needs the thing it was measured against on the same row.
        CheckConstraint(
            "late_minutes IS NULL OR (scheduled_start IS NOT NULL "
            "AND grace_minutes IS NOT NULL AND first_in IS NOT NULL)",
            name="daily_attendance_late_minutes_have_a_baseline",
        ),
        CheckConstraint(
            "(first_in IS NULL) = (first_in_source IS NULL)",
            name="daily_attendance_first_in_says_its_source",
        ),
        CheckConstraint(
            "(last_out IS NULL) = (last_out_source IS NULL)",
            name="daily_attendance_last_out_says_its_source",
        ),
    )


Index("ix_daily_attendance_day", DailyAttendance.attendance_day)
Index("ix_daily_attendance_status", DailyAttendance.status_code)


# ---------------------------------------------------------------------------
# Step 7: the sheet.
#
# Nothing about the sheet is stored. It is generated from the daily rows on
# demand, and the Excel file is an export of the same render (SPEC §7) — so the
# only rows this step needs are the ones that would otherwise be constants in
# a layout: how many rows fit a page, what period a sheet covers, the marks a
# cell uses, and the note in the top-left that nobody has read yet.
# ---------------------------------------------------------------------------


class SheetSetting(Base):
    """Values the sheet renders with. Assumed, so rows (SPEC §9 A38–A42).

    `sheet.note_top_left` is deliberately empty: the note exists on HR's paper
    and has never been read. The renderer marks the cell as unread rather than
    guessing at it, and filling this row in is what settles it.
    """

    __tablename__ = "sheet_setting"

    key = mapped_column(Text, primary_key=True)
    value = mapped_column(Text, nullable=False)
    note = mapped_column(Text)


# ---------------------------------------------------------------------------
# Step 9: the ingestion alert.
#
# Two silences, and they are different failures (SPEC §3, §12):
#
#   contact  the device says nothing at all. It polls every ten seconds even
#            when nobody punches, so silence here means the device is off, the
#            network is down, or the Cloud Server setting was repointed —
#            which §10 says stops capture silently while the device keeps
#            recording locally.
#   punches  the device is talking but no punch has arrived while a shift is
#            running on a day the factory is open. A quiet night and a Sunday
#            are not this.
#
# Collapsing them would produce an alert that cries wolf every weekend and
# stays quiet when the receiver is unplugged on a public holiday.
# ---------------------------------------------------------------------------


class AlertSetting(Base):
    """Thresholds. Rows, because the right numbers are guesses until the
    factory has run on them (SPEC §9 A43-A45)."""

    __tablename__ = "alert_setting"

    key = mapped_column(Text, primary_key=True)
    value = mapped_column(Text, nullable=False)
    note = mapped_column(Text)


class IngestionAlert(Base):
    """One row per state change, never one per check.

    A check that wrote a row every time it ran would bury the transition that
    matters in thousands of identical rows. Raised and cleared are both
    recorded, so an outage has a start, an end and a length that can be read
    off afterwards — which is what makes the alert answerable to the question
    "how long were we blind?"
    """

    __tablename__ = "ingestion_alert"

    id = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    changed_at = mapped_column(SERVER_TS, nullable=False, server_default=func.now())
    serial_number = mapped_column(Text, nullable=False)
    kind = mapped_column(Text, nullable=False)      # 'contact' or 'punch'
    state = mapped_column(Text, nullable=False)     # 'raised' or 'cleared'
    minutes_silent = mapped_column(Integer)
    threshold_minutes = mapped_column(Integer)
    detail = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("kind IN ('contact', 'punch')", name="ingestion_alert_kind"),
        CheckConstraint(
            "state IN ('raised', 'cleared')", name="ingestion_alert_state"
        ),
    )


Index("ix_ingestion_alert_serial_kind", IngestionAlert.serial_number,
      IngestionAlert.kind, IngestionAlert.id)


# ---------------------------------------------------------------------------
# Step 8: the device command queue.
#
# The device asks for work on every poll and reports each result back (SPEC
# §11). Two things about this step are deliberately small:
#
#   * **Two commands exist: REBOOT and CHECK.** Nothing that clears, deletes or
#     resets anything on the device. The device buffers punches while the
#     receiver is unreachable and that has never been proven on this hardware
#     (BUILD.md) — an unbuffered clear would take punches with it, and there is
#     no way to get them back.
#   * **The formats are documented, not observed** (SPEC §9 A46, A47). No
#     command has ever been sent to this device.
# ---------------------------------------------------------------------------


class DeviceCommandType(Base):
    """The commands this system is willing to send.

    Rows, because §11 says the command strings themselves are unverified — and
    a row is also where the refusal to add a destructive one is visible. There
    is no `CLEAR DATA` row and adding one is a decision, not a typo.
    """

    __tablename__ = "device_command_type"

    code = mapped_column(Text, primary_key=True)
    command_text = mapped_column(Text, nullable=False)
    label = mapped_column(Text, nullable=False)
    note = mapped_column(Text)


class DeviceCommand(Base):
    """One row per command issued, and one per result that matched nothing.

    The lifecycle is three stamps: queued, handed out, result received. A row
    that has been handed out and never answered is the interesting one — it
    means the device took the command and either did not finish it or does not
    report the way the documentation says.

    `reported_id` is the id **as the device sent it back**, stored verbatim
    beside our own. They are expected to be the same string and nothing depends
    on that being true: matching is done on the text the device returned, and a
    mismatch shows up as a row rather than as a lost result.

    An `unsolicited` row is a result for a command this system never issued. It
    is stored, flagged, and changes no response — the same reflex as an unknown
    serial (SPEC §12).
    """

    __tablename__ = "device_command"

    id = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    serial_number = mapped_column(Text, nullable=False)
    command_code = mapped_column(Text, ForeignKey("device_command_type.code"))
    command_text = mapped_column(Text)

    queued_at = mapped_column(SERVER_TS)
    queued_by = mapped_column(Text)
    handed_out_at = mapped_column(SERVER_TS)
    handed_out_raw_request_id = mapped_column(BigInteger, ForeignKey("raw_request.id"))

    result_at = mapped_column(SERVER_TS)
    result_raw_request_id = mapped_column(BigInteger, ForeignKey("raw_request.id"))
    reported_id = mapped_column(Text)
    return_code = mapped_column(Text)
    result_body = mapped_column(Text)

    unsolicited = mapped_column(Boolean, nullable=False, default=False)
    note = mapped_column(Text)

    __table_args__ = (
        # A queued command names what it is; an unsolicited result does not
        # have to, because the device chose what to tell us.
        CheckConstraint(
            "unsolicited OR (command_code IS NOT NULL AND queued_at IS NOT NULL)",
            name="device_command_queued_rows_have_a_command",
        ),
        CheckConstraint(
            "NOT unsolicited OR result_at IS NOT NULL",
            name="device_command_unsolicited_rows_are_results",
        ),
        # Handed out before answered, and never answered without being handed
        # out — unless nobody issued it.
        CheckConstraint(
            "result_at IS NULL OR handed_out_at IS NOT NULL OR unsolicited",
            name="device_command_answered_only_after_handout",
        ),
    )


Index("ix_device_command_serial", DeviceCommand.serial_number, DeviceCommand.id)
Index("ix_device_command_pending", DeviceCommand.serial_number,
      DeviceCommand.handed_out_at)


# ---------------------------------------------------------------------------
# Step 5: HR entry — leave and gate pass.
#
# **This is HR typing a form that has already been signed on paper** (SPEC §5,
# §6). No approval is routed, no entitlement is checked and no balance is kept:
# those are Milestone 5, and a signature block on the paper is not a workflow.
#
# Two rules from the forms are structural here rather than advisory:
#
#   * **The number of days is what the form says.** It is required, and nothing
#     in this system computes it from the range — a half day, and a non-working
#     day inside a range, both mean the count and the span are different numbers
#     (SPEC §6).
#   * **The hours on a gate pass are never typed.** The form carries an out time
#     and an in time and no hours at all, so hours is a generated column: there
#     is no field to type into (SPEC §5).
# ---------------------------------------------------------------------------


class LeaveCode(Base):
    """A code from the sheet legend — what HR writes in a cell (SPEC §6).

    Rows, because §13 forbids hard-coding a leave code. The legend prints
    `T / C` on one line; they are two codes and a cell can only hold one, so
    they are two rows.
    """

    __tablename__ = "leave_code"

    code = mapped_column(Text, primary_key=True)
    label = mapped_column(Text, nullable=False)
    note = mapped_column(Text)


class LeaveType(Base):
    """A tick on the leave application form — what an employee applies for.

    `suggested_sheet_code` is the convenience A48 describes and nothing more:
    it is what the entry screen offers before HR touches it. **Four of the
    seven types have none**, because the legend has no letter for them, and the
    screen offers nothing rather than inventing one.
    """

    __tablename__ = "leave_type"

    code = mapped_column(Text, primary_key=True)
    label = mapped_column(Text, nullable=False)
    sort_order = mapped_column(Integer, nullable=False, default=0)
    suggested_sheet_code = mapped_column(Text, ForeignKey("leave_code.code"))
    reason_required = mapped_column(Boolean, nullable=False, default=False)
    note = mapped_column(Text)


class LeaveRecord(Base):
    """One line of leave, as the form has it.

    **Both vocabularies are stored and neither is derived from the other**
    (SPEC §6): `leave_type_code` is what was applied for, `sheet_code` is what
    HR writes on the sheet, and either may be empty — a form type with no code,
    or a code HR wrote with no form behind it. Filling one in from the other
    would invent a mapping the paper does not contain.

    `sql_account_code` is carried from the start and stays empty until Accounts
    answers what the payroll codes mean (SPEC §8).
    """

    __tablename__ = "leave_record"

    id = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    employee_id = mapped_column(BigInteger, ForeignKey("employee.id"), nullable=False)

    leave_type_code = mapped_column(Text, ForeignKey("leave_type.code"))
    sheet_code = mapped_column(Text, ForeignKey("leave_code.code"))

    period_from = mapped_column(Date, nullable=False)
    period_to = mapped_column(Date, nullable=False)

    # As given on the form. Never computed from the range, and required so that
    # there is nothing for a later default to quietly fill in.
    days = mapped_column(Numeric(5, 2), nullable=False)

    # When leave was asked for, which is a different fact from when it was
    # taken. The form has its own date field for it (SPEC §6).
    date_of_application = mapped_column(Date)

    reason = mapped_column(Text)
    sql_account_code = mapped_column(Text)

    entered_by = mapped_column(Text, nullable=False)
    entered_at = mapped_column(SERVER_TS, nullable=False, server_default=func.now())
    note = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "leave_type_code IS NOT NULL OR sheet_code IS NOT NULL",
            name="leave_record_says_what_it_is",
        ),
        CheckConstraint("period_to >= period_from", name="leave_record_dates_ordered"),
        CheckConstraint("days > 0", name="leave_record_days_positive"),
        # A15: a half day is stored as a fraction, and 0.5 is the only fraction
        # anybody has described.
        CheckConstraint("mod(days * 2, 1) = 0", name="leave_record_days_are_halves"),
        CheckConstraint(
            "length(btrim(entered_by)) > 0", name="leave_record_entered_by_recorded"
        ),
    )


Index("ix_leave_record_employee_period", LeaveRecord.employee_id,
      LeaveRecord.period_from, LeaveRecord.period_to)


class GatePassCategory(Base):
    """The four ticks on the gate pass (SPEC §5). Rows, and exactly four."""

    __tablename__ = "gate_pass_category"

    code = mapped_column(Text, primary_key=True)
    label = mapped_column(Text, nullable=False)
    sort_order = mapped_column(Integer, nullable=False, default=0)
    note = mapped_column(Text)


class GatePass(Base):
    """One gate pass, as the form has it.

    **There is no department field**, because the form has none — the
    employee's section is looked up (SPEC §5, §2).

    **`hours` is generated by the database from the two times.** The form
    carries no hours at all, so there is no field to type into: the same reflex
    as the guard entry that has no field for a time (SPEC §3). A pass has one
    date and two times, so the in time is later than the out time on that same
    date.
    """

    __tablename__ = "gate_pass"

    id = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    employee_id = mapped_column(BigInteger, ForeignKey("employee.id"), nullable=False)
    pass_date = mapped_column(Date, nullable=False)
    category_code = mapped_column(
        Text, ForeignKey("gate_pass_category.code"), nullable=False
    )
    reason = mapped_column(Text)
    destination = mapped_column(Text)

    # Filled in by the guard on the paper form, typed by HR on entry — not the
    # guard entry path in §3, which is server-stamped (SPEC §5).
    out_time = mapped_column(Time, nullable=False)
    in_time = mapped_column(Time, nullable=False)

    hours = mapped_column(
        Numeric(5, 2),
        Computed("round(extract(epoch from (in_time - out_time)) / 3600.0, 2)",
                 persisted=True),
    )

    entered_by = mapped_column(Text, nullable=False)
    entered_at = mapped_column(SERVER_TS, nullable=False, server_default=func.now())
    note = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("in_time > out_time", name="gate_pass_in_after_out"),
        CheckConstraint(
            "length(btrim(entered_by)) > 0", name="gate_pass_entered_by_recorded"
        ),
    )


Index("ix_gate_pass_employee_date", GatePass.employee_id, GatePass.pass_date)
