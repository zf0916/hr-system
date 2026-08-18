"""Process configuration. Values that HR or the device owner can be wrong about
live in database rows, not here (SPEC.md §9). What is here is where the process
runs, and nothing about how the device behaves."""

import os

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://hr:hr@127.0.0.1:5432/hr_attendance"
)

# Bumping this and replaying is how a parser change reaches old captures.
# Never re-collected from the device (CLAUDE.md, Conventions).
PARSER_VERSION = "1"
