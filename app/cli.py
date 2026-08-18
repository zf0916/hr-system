"""One command creates the database and seeds it; one command replays.

There are no migrations until the parallel run starts. Until then the schema
changes by dropping and recreating (BUILD.md, "Data and migrations"). From the
first day of the parallel run this command stops being safe: raw device capture
cannot be recreated, and `seed` would destroy it.
"""

import argparse
import sys

from sqlalchemy import func, select, text

from app.config import DATABASE_URL, PARSER_VERSION
from app.db import Session, engine
from app.models import Base, Device, DeviceOption, ParsedPunch, ParserSetting, RawRequest
from app.parser import replay as replay_parser
from app.seed import seed as seed_rows


def cmd_seed(args) -> int:
    with Session() as session:
        captured = 0
        try:
            captured = session.scalar(select(func.count()).select_from(RawRequest)) or 0
        except Exception:
            session.rollback()
    if captured and not args.force:
        print(
            f"refusing to drop: {captured} raw requests are already captured.\n"
            "Real device capture cannot be recreated. Re-run with --force only if "
            "this is still test data (BUILD.md, Data and migrations).",
            file=sys.stderr,
        )
        return 1

    with engine.begin() as conn:
        # DROP CASCADE, because raw_request carries its own append-only triggers.
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    Base.metadata.create_all(engine)
    with Session() as session:
        seed_rows(session)
    print(f"schema created and seeded at {_dsn()}")
    return 0


def cmd_replay(args) -> int:
    """A parser change means bumping the version and replaying the raw layer —
    never re-collecting from the device."""
    with Session() as session:
        requests, punches, unparsable = replay_parser(session, since_id=args.since_id)
        session.commit()
    print(
        f"parser {PARSER_VERSION}: replayed {requests} raw requests "
        f"into {punches} punch rows"
    )
    if unparsable:
        print(
            f"{len(unparsable)} raw requests could not be parsed at all and were "
            f"left for a later parser version: {unparsable[:20]}"
        )
        return 1
    return 0


def cmd_status(args) -> int:
    with Session() as session:
        counts = {
            "raw_request": session.scalar(select(func.count()).select_from(RawRequest)),
            "parsed_punch": session.scalar(select(func.count()).select_from(ParsedPunch)),
            "parsed_punch (failed)": session.scalar(
                select(func.count()).select_from(ParsedPunch).where(
                    ParsedPunch.parse_ok.is_(False)
                )
            ),
            "device": session.scalar(select(func.count()).select_from(Device)),
            "device_option": session.scalar(
                select(func.count()).select_from(DeviceOption)
            ),
            "parser_setting": session.scalar(
                select(func.count()).select_from(ParserSetting)
            ),
        }
    print(_dsn())
    for name, value in counts.items():
        print(f"  {name:24} {value}")
    return 0


def _dsn() -> str:
    return DATABASE_URL.rsplit("@", 1)[-1]


def main() -> int:
    parser = argparse.ArgumentParser(prog="hr", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_seed = sub.add_parser("seed", help="drop, recreate and seed the database")
    p_seed.add_argument(
        "--force",
        action="store_true",
        help="drop even though requests have already been captured",
    )
    p_seed.set_defaults(func=cmd_seed)

    p_replay = sub.add_parser("replay", help="rebuild parsed punches from the raw layer")
    p_replay.add_argument("--since-id", type=int, default=0)
    p_replay.set_defaults(func=cmd_replay)

    sub.add_parser("status", help="row counts").set_defaults(func=cmd_status)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
