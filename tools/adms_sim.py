#!/usr/bin/env python3
"""Device simulator — pushes at the receiver the way the firmware would.

It is not a test of whether SPEC.md §12 is right. **Most of §12 is now observed
against the real device** (raw_request 96-115, the second capture), and the
shapes below are copied from those bytes: the handshake query and its option
lines from request 101, the punch line from request 109, the OPERLOG cursor
parameter from requests 97 and 106. What is still unobserved is marked where it
is used. Only real traffic settles the rest (BUILD.md).

This checks that the receiver behaves the way §12 says the firmware needs it
to, and it fails on anything the firmware would reject:

    a redirect, a 401, a 404, a 405, a 500, an HTML error page, a JSON body,
    a content type that is not text/plain, a batch answered with the wrong
    count, or a route that answers anything but 200.

Deliberately stdlib-only and deliberately raw: http.client sends exactly the
bytes given to it and never follows a redirect, which is what makes a redirect
visible here instead of silently followed.

    uv run python tools/adms_sim.py                  # responses, and what landed
    uv run python tools/adms_sim.py --protocol-only  # responses only

Exits non-zero on the first thing the firmware would reject.
"""

from __future__ import annotations

import argparse
import http.client
import os
import sys
from dataclasses import dataclass, field
from urllib.parse import urlencode

SN = "SIM0000000001"
UNKNOWN_SN = "NOTALLOWLISTED9"

# The ten options the real device was sent and accepted (raw_request 101).
ACCEPTED_OPTIONS = {
    "Stamp", "OpStamp", "ErrorDelay", "Delay", "TransTimes", "TransInterval",
    "TransFlag", "TimeZone", "Realtime", "Encrypt",
}

# SPEC §12, observed in raw_request 109: pin, time, status, verify, then six
# fields that were 0 in every line captured, then a trailing tab. Status 255 and
# verify 15 (face) / 1 (fingerprint) are what the device actually sends.
#
# The leading-zero PINs here are not what this device can hold — it refuses a
# leading zero in a user ID (SPEC §10) — but the receiver must store whatever
# arrives verbatim, and that rule is what these lines exercise.
PUNCHES = [
    ("0090", "2026-08-18 07:58:11", "255", "1", "0", "0", "0", "0", "0", "0"),
    ("0657", "2026-08-18 07:59:02", "255", "15", "0", "0", "0", "0", "0", "0"),
    ("1627", "2026-08-18 08:03:47", "255", "1", "0", "0", "0", "0", "0", "0"),
    ("0090", "2026-08-18 17:31:20", "255", "1", "0", "0", "0", "0", "0", "0"),
]


def attlog_body(rows) -> bytes:
    """One line per punch, each ending in the trailing tab the device sends."""
    return ("\r\n".join("\t".join(r) + "\t" for r in rows) + "\r\n").encode("ascii")


BATCH = attlog_body(PUNCHES)

# What the device actually sent, from raw_request 97 and 106: an OPLOG line,
# tab-separated, ASCII, and no trailing newline.
OPERLOG_OBSERVED = b"OPLOG 4\t1\t2026-08-20 11:26:07\t1\t0\t0\t0"

# A name field in GBK, which is what many firmware builds send. Nothing captured
# so far has a non-ASCII byte in it (SPEC §9 A27), so this case is the only
# thing exercising the rule that the receiver never decodes at capture.
OPERLOG_BODY = (
    "OPLOG 4\t0\t2026-08-18 08:05:00\t0\t0\t0\r\n"
    "USER PIN=0090\tName=" + "陈志峰" + "\tPri=0\tPasswd=\tCard=\tGrp=1\tTZ=0000000100000000\r\n"
).encode("gbk")

# Enough of a JPEG to be binary: a header, a 0x00 run, and a byte sequence that
# is not valid UTF-8.
PHOTO_BODY = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    + bytes(range(256)) * 4
    + b"\xff\xd9"
)


@dataclass
class Response:
    status: int
    reason: str
    headers: dict
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def header(self, name: str) -> str | None:
        for key, value in self.headers.items():
            if key.lower() == name.lower():
                return value
        return None


@dataclass
class Sim:
    host: str
    port: int
    failures: list = field(default_factory=list)
    checks: int = 0
    requests: int = 0

    # ---- transport ---------------------------------------------------------

    def send(self, method: str, path: str, params: dict | None = None,
             body: bytes = b"", content_type: str | None = "text/plain") -> Response:
        url = path
        if params is not None:
            url = f"{path}?{urlencode(params)}"
        headers = {"User-Agent": "iClock Proxy/1.09", "Connection": "close"}
        if content_type is not None:
            headers["Content-Type"] = content_type
        if body or method in ("POST", "PUT", "PATCH"):
            headers["Content-Length"] = str(len(body))
        conn = http.client.HTTPConnection(self.host, self.port, timeout=15)
        try:
            conn.request(method, url, body=body, headers=headers)
            raw = conn.getresponse()
            response = Response(raw.status, raw.reason, dict(raw.getheaders()), raw.read())
        finally:
            conn.close()
        self.requests += 1
        return response

    # ---- checks ------------------------------------------------------------

    def check(self, ok: bool, what: str, detail: str = "") -> bool:
        self.checks += 1
        if ok:
            print(f"  ok    {what}")
        else:
            print(f"  FAIL  {what}" + (f" — {detail}" if detail else ""))
            self.failures.append(f"{what}: {detail}" if detail else what)
        return ok

    def firmware_accepts(self, label: str, response: Response) -> bool:
        """What the firmware demands of every /iclock/ response, whatever the
        route: 200, plain text, no redirect, no HTML, no JSON."""
        ok = True
        ok &= self.check(
            response.status == 200,
            f"{label}: 200",
            f"got {response.status} {response.reason}",
        )
        ok &= self.check(
            response.header("location") is None,
            f"{label}: no redirect",
            f"Location: {response.header('location')}",
        )
        ctype = response.header("content-type") or ""
        ok &= self.check(
            ctype.split(";")[0].strip() == "text/plain",
            f"{label}: plain text",
            f"Content-Type: {ctype!r}",
        )
        head = response.body.lstrip()[:1]
        ok &= self.check(
            head not in (b"<", b"{", b"["),
            f"{label}: not HTML or JSON",
            f"body starts {response.body[:60]!r}",
        )
        return ok

    def expect_body(self, label: str, response: Response, expected: str) -> bool:
        return self.check(
            response.text.strip() == expected,
            f"{label}: body {expected!r}",
            f"got {response.text.strip()!r}",
        )


def run_cycle(sim: Sim) -> None:
    """One full cycle, in the order a device does it after power-on."""

    print("\n-- handshake (GET /iclock/cdata, options=all)")
    # The query the device sends, parameter for parameter (raw_request 101).
    r = sim.send("GET", "/iclock/cdata",
                 {"SN": SN, "options": "all", "language": "69", "pushver": "2.4.1",
                  "DeviceType": "att", "PushOptionsFlag": "1"},
                 content_type=None)
    sim.firmware_accepts("handshake", r)
    lines = r.text.replace("\r\n", "\n").strip().split("\n")
    sim.check(lines[0] == f"GET OPTION FROM: {SN}", "handshake: first line",
              f"got {lines[0]!r}")
    sim.check(len(lines) > 1 and all("=" in line for line in lines[1:]),
              "handshake: Key=Value lines follow", f"got {lines[1:]!r}")
    # The set the real device accepted without complaint (raw_request 101). A
    # missing option is a row missing from device_option, which is how the
    # receiver would go quiet about something the device was told before.
    keys = {line.split("=", 1)[0] for line in lines[1:]}
    sim.check(ACCEPTED_OPTIONS <= keys,
              "handshake: every option the device accepted is still sent",
              f"missing {sorted(ACCEPTED_OPTIONS - keys)}")
    sim.check("Realtime=1" in lines[1:],
              "handshake: Realtime=1, which is why punches arrive in seconds")

    print("\n-- poll for commands (GET /iclock/getrequest)")
    r = sim.send("GET", "/iclock/getrequest", {"SN": SN}, content_type=None)
    sim.firmware_accepts("getrequest", r)
    body = r.text.strip()
    sim.check(body == "OK" or body.startswith("C:"), "getrequest: OK or C:{id}:{CMD}",
              f"got {body!r}")

    print("\n-- punch batch (POST /iclock/cdata table=ATTLOG)")
    r = sim.send("POST", "/iclock/cdata",
                 {"SN": SN, "table": "ATTLOG", "Stamp": "9999"}, BATCH)
    sim.firmware_accepts("attlog", r)
    sim.expect_body("attlog", r, f"OK: {len(PUNCHES)}")

    print("\n-- duplicate re-push, same body and same Stamp")
    r = sim.send("POST", "/iclock/cdata",
                 {"SN": SN, "table": "ATTLOG", "Stamp": "9999"}, BATCH)
    sim.firmware_accepts("attlog duplicate", r)
    sim.expect_body("attlog duplicate", r, f"OK: {len(PUNCHES)}")

    print("\n-- OPERLOG as the device sends it (OpStamp, not Stamp)")
    r = sim.send("POST", "/iclock/cdata",
                 {"SN": SN, "table": "OPERLOG", "OpStamp": "9999"}, OPERLOG_OBSERVED)
    sim.firmware_accepts("operlog", r)
    sim.expect_body("operlog", r, "OK")

    print("\n-- GBK OPERLOG body (still unobserved: SPEC §9 A27)")
    r = sim.send("POST", "/iclock/cdata",
                 {"SN": SN, "table": "OPERLOG", "OpStamp": "9999"}, OPERLOG_BODY)
    sim.firmware_accepts("operlog gbk", r)
    sim.expect_body("operlog gbk", r, "OK")

    print("\n-- binary photo (table=ATTPHOTO, then /iclock/fdata)")
    r = sim.send("POST", "/iclock/cdata",
                 {"SN": SN, "table": "ATTPHOTO", "Stamp": "9999",
                  "PIN": "0090", "photo": "0090-2026-08-18-080347.jpg"},
                 PHOTO_BODY, content_type="image/jpeg")
    sim.firmware_accepts("attphoto", r)
    sim.expect_body("attphoto", r, "OK")

    r = sim.send("POST", "/iclock/fdata", {"SN": SN, "PIN": "0090"},
                 PHOTO_BODY, content_type="application/octet-stream")
    sim.firmware_accepts("fdata", r)
    sim.expect_body("fdata", r, "OK")

    print("\n-- command result (POST /iclock/devicecmd)")
    r = sim.send("POST", "/iclock/devicecmd", {"SN": SN},
                 b"ID=1&Return=0&CMD=DATA UPDATE USERINFO\r\n")
    sim.firmware_accepts("devicecmd", r)
    sim.expect_body("devicecmd", r, "OK")

    print("\n-- malformed input (every one still has to be answered)")
    malformed = [
        ("too few fields", attlog_body([("0090", "2026-08-18 08:00:00")]), 1),
        ("unparseable time", b"0090\tyesterday morning\t0\t1\t0\t0\t0\r\n", 1),
        ("empty pin", b"\t2026-08-18 08:00:00\t0\t1\t0\t0\t0\r\n", 1),
        ("binary in an ATTLOG body", PHOTO_BODY[:64], None),
        ("no separators at all", b"garbage garbage garbage\r\n", 1),
        ("empty body", b"", 0),
    ]
    for label, body, expected_lines in malformed:
        r = sim.send("POST", "/iclock/cdata",
                     {"SN": SN, "table": "ATTLOG", "Stamp": "9999"}, body)
        sim.firmware_accepts(f"malformed/{label}", r)
        if expected_lines is not None:
            sim.expect_body(f"malformed/{label}", r, f"OK: {expected_lines}")
        else:
            sim.check(r.text.startswith("OK"), f"malformed/{label}: answered OK",
                      f"got {r.text[:40]!r}")

    print("\n-- malformed request framing")
    r = sim.send("POST", "/iclock/cdata", {"table": "ATTLOG"}, BATCH)
    sim.firmware_accepts("no SN at all", r)

    r = sim.send("POST", "/iclock/cdata", {"SN": SN, "Stamp": "9999"}, BATCH)
    sim.firmware_accepts("no table parameter", r)

    r = sim.send("POST", "/iclock/cdata", {"SN": SN, "table": "TABLETHATDOESNOTEXIST"},
                 b"whatever\r\n")
    sim.firmware_accepts("unknown table", r)
    sim.expect_body("unknown table", r, "OK")

    r = sim.send("POST", "/iclock/cdata", {"SN": SN, "table": "ATTLOG", "Stamp": "9999"},
                 BATCH, content_type="application/x-www-form-urlencoded")
    sim.firmware_accepts("wrong content type", r)
    sim.expect_body("wrong content type", r, f"OK: {len(PUNCHES)}")

    r = sim.send("POST", "/iclock/cdata", {"SN": SN, "table": "ATTLOG", "Stamp": "9999"},
                 BATCH, content_type=None)
    sim.firmware_accepts("no content type", r)

    print("\n-- trailing slash, unknown route, unexpected method")
    r = sim.send("POST", "/iclock/cdata/", {"SN": SN, "table": "ATTLOG", "Stamp": "9999"},
                 BATCH)
    sim.firmware_accepts("trailing slash", r)

    r = sim.send("POST", "/iclock/edata", {"SN": SN, "table": "SOMETHINGNEW"},
                 b"a\tb\tc\r\n")
    sim.firmware_accepts("unknown route", r)
    sim.expect_body("unknown route", r, "OK")

    r = sim.send("GET", "/iclock/ping", {"SN": SN}, content_type=None)
    sim.firmware_accepts("unknown route (GET)", r)

    r = sim.send("PUT", "/iclock/cdata", {"SN": SN, "table": "ATTLOG"}, BATCH)
    sim.firmware_accepts("unexpected method", r)

    print("\n-- unknown serial number (logged, never rejected)")
    r = sim.send("GET", "/iclock/cdata",
                 {"SN": UNKNOWN_SN, "options": "all", "pushver": "2.4.1"},
                 content_type=None)
    sim.firmware_accepts("unknown serial", r)
    sim.check(r.text.startswith(f"GET OPTION FROM: {UNKNOWN_SN}"),
              "unknown serial: still answered the handshake", f"got {r.text[:40]!r}")

    print("\n-- a shift's worth in one batch")
    big = [(f"{i:04d}", f"2026-08-18 07:{i % 60:02d}:00", "0", "1", "0", "0", "0")
           for i in range(1, 51)]
    r = sim.send("POST", "/iclock/cdata", {"SN": SN, "table": "ATTLOG", "Stamp": "10000"},
                 attlog_body(big))
    sim.firmware_accepts("50-punch batch", r)
    sim.expect_body("50-punch batch", r, "OK: 50")


# ---- what landed in the database, for the layers the firmware cannot see ----


def dsn() -> str:
    url = os.environ.get(
        "DATABASE_URL", "postgresql+psycopg://hr:hr@127.0.0.1:5432/hr_attendance"
    )
    return url.replace("postgresql+psycopg://", "postgresql://")


def check_db(sim: Sim, baseline: int) -> None:
    """Every count below is scoped to the rows this run produced. The raw layer
    is append-only and keeps everything an earlier run pushed, so a check that
    counted the whole table would pass or fail on history rather than on now."""
    import psycopg

    print("\n-- what landed (raw layer and parsed punches)")
    mine = "id > %s"
    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM raw_request WHERE {mine}", (baseline,))
        stored = cur.fetchone()[0]
        sim.check(stored == sim.requests, "every request stored, whole",
                  f"sent {sim.requests}, stored {stored}")

        cur.execute(
            f"SELECT count(*) FROM raw_request WHERE {mine} AND body = %s",
            (baseline, BATCH),
        )
        found = cur.fetchone()[0]
        sim.check(found >= 2, "duplicate re-push kept, not deduplicated",
                  f"found {found} copies of the batch")

        cur.execute(
            f"SELECT count(*) FROM raw_request WHERE {mine} AND body = %s",
            (baseline, OPERLOG_BODY),
        )
        found = cur.fetchone()[0]
        sim.check(found == 1, "GBK body stored byte-for-byte, not decoded",
                  f"found {found}")

        cur.execute(
            f"SELECT count(*) FROM raw_request WHERE {mine} AND body = %s "
            "AND query_string LIKE '%%OpStamp=%%'",
            (baseline, OPERLOG_OBSERVED),
        )
        found = cur.fetchone()[0]
        sim.check(found == 1,
                  "OPERLOG stored with the cursor parameter the device sends",
                  f"found {found}")

        cur.execute(
            f"SELECT count(*) FROM raw_request WHERE {mine} AND body = %s",
            (baseline, PHOTO_BODY),
        )
        found = cur.fetchone()[0]
        sim.check(found == 2, "binary photo stored byte-for-byte", f"found {found}")

        punches = "raw_request_id > %s"
        cur.execute(
            f"SELECT count(*) FROM parsed_punch WHERE {punches} "
            "AND pin = %s AND punch_time_text = %s",
            (baseline, PUNCHES[0][0], PUNCHES[0][1]),
        )
        found = cur.fetchone()[0]
        sim.check(found >= 2, "punch lines parsed, duplicates kept for downstream",
                  f"found {found}")

        cur.execute(
            f"SELECT count(*) FROM parsed_punch WHERE {punches} AND pin = '0090'",
            (baseline,),
        )
        sim.check(cur.fetchone()[0] > 0,
                  "a PIN is stored exactly as sent, zeros and all (SPEC §12, §13)")

        # The ten-field line the device really sends. The pin, the time, the
        # status and the verify method have to survive it whatever the parser
        # decides about the field count.
        cur.execute(
            f"SELECT count(*) FROM parsed_punch WHERE {punches} "
            "AND pin = %s AND punch_time_text = %s "
            "AND status_code = '255' AND verify_code = '15'",
            (baseline, PUNCHES[1][0], PUNCHES[1][1]),
        )
        found = cur.fetchone()[0]
        sim.check(found >= 2,
                  "the ten-field line yields pin, time, status 255 and verify 15",
                  f"found {found}")

        cur.execute(
            f"SELECT punch_time::text, punch_time_text FROM parsed_punch "
            f"WHERE {punches} AND punch_time_text = %s LIMIT 1",
            (baseline, PUNCHES[0][1]),
        )
        row = cur.fetchone()
        sim.check(row is not None and row[0] == row[1],
                  "device time stored as sent, never converted",
                  f"got {row}")

        cur.execute(
            f"SELECT count(*) FROM parsed_punch WHERE {punches} "
            "AND parse_ok = false AND parse_error IS NOT NULL",
            (baseline,),
        )
        failed = cur.fetchone()[0]
        sim.check(failed >= 4, "unparseable lines kept as rows, with the reason",
                  f"found {failed}")

        cur.execute(
            f"SELECT count(*) FROM parsed_punch WHERE {punches} "
            "AND decoded_with IS NULL",
            (baseline,),
        )
        sim.check(cur.fetchone()[0] == 0, "every parsed line records its codec")

        # A body the parser choked on is answered OK and stored, which is the
        # rule — and is also invisible from outside. This is where it shows.
        cur.execute(
            "SELECT count(*) FROM raw_request r WHERE r.id > %s "
            "AND lower(r.table_param) = 'attlog' AND r.body_bytes > 0 "
            "AND NOT EXISTS (SELECT 1 FROM parsed_punch p WHERE p.raw_request_id = r.id)",
            (baseline,),
        )
        silent = cur.fetchone()[0]
        sim.check(silent == 0, "no ATTLOG body was captured and then silently unparsed",
                  f"{silent} bodies produced no punch rows at all")

    print("\n-- the raw layer is append-only")
    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        try:
            cur.execute("DELETE FROM raw_request WHERE id = (SELECT min(id) FROM raw_request)")
            conn.rollback()
            sim.check(False, "DELETE on raw_request is refused", "it succeeded")
        except psycopg.errors.RaiseException:
            sim.check(True, "DELETE on raw_request is refused")
    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        try:
            cur.execute("UPDATE raw_request SET body = '' WHERE id = (SELECT min(id) FROM raw_request)")
            conn.rollback()
            sim.check(False, "UPDATE on raw_request is refused", "it succeeded")
        except psycopg.errors.RaiseException:
            sim.check(True, "UPDATE on raw_request is refused")


def read_baseline() -> int:
    """The highest raw_request id before this run, so the checks below look at
    this run's rows only."""
    import psycopg

    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT coalesce(max(id), 0) FROM raw_request")
        return cur.fetchone()[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=os.environ.get("SIM_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("SIM_PORT", "8000")))
    ap.add_argument(
        "--protocol-only",
        action="store_true",
        help="check the responses only, without looking at what was captured",
    )
    args = ap.parse_args()

    sim = Sim(args.host, args.port)
    print(f"pushing at http://{args.host}:{args.port}/iclock/ as SN={SN}")

    # Checking what landed is not optional by default. A receiver can answer
    # every request correctly and store none of them — that is exactly what a
    # missing catch-all route looks like from outside.
    baseline = 0
    if not args.protocol_only:
        try:
            baseline = read_baseline()
        except Exception as exc:
            print(f"cannot read the database to check what lands: {exc}\n"
                  "Set DATABASE_URL, or pass --protocol-only to check responses only.",
                  file=sys.stderr)
            return 2
    try:
        run_cycle(sim)
    except (ConnectionError, OSError) as exc:
        print(f"\nthe receiver is not answering: {exc}", file=sys.stderr)
        return 2

    if not args.protocol_only:
        check_db(sim, baseline)

    print(f"\n{sim.requests} requests, {sim.checks} checks")
    if sim.failures:
        print(f"{len(sim.failures)} FAILED — the firmware would reject this:")
        for failure in sim.failures:
            print(f"  - {failure}")
        return 1
    print("clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
