#!/usr/bin/env python3
"""A throwaway database, and an interface running against it.

**A write that goes through HTTP cannot be rolled back by the thing that made
it.** The receiver, the routes and the browser are separate processes; a
`session.rollback()` in a gate has no reach into a commit the route already
made. So a gate that has to press *Save* — and piece 4's does, because a page
that only looks right proves nothing about what it writes — presses it
somewhere that can be thrown away afterwards.

That is the rule CLAUDE.md states for a deliberate mistake on a write path, and
it applies to the deliberate *correct* path too, for the same reason: this
system's leave records, gate passes and guard entries are what people typed and
nothing rebuilds them. A gate that left one behind would be inventing a form.

What this does:

  * creates a database beside the working one, in the same Postgres container;
  * runs the same image as `api` against it, on the same compose network, so a
    browser inside that network reaches it by container name;
  * seeds it and loads the demonstration employee list, the provisional
    schedules and the 2026 holidays — everything the sheet needs to draw;
  * and **drops the database and kills the container when the block ends**,
    whatever happened inside it.

It never touches `hr_attendance`. The name it makes is fixed and obvious, and
the drop at the end is of that name only.

    from tools.throwaway import Throwaway
    with Throwaway() as app:
        print(app.base)          # http://hr-throwaway:8100, on the compose net
        print(app.published)     # ('127.0.0.1', 8099), from this machine
"""

from __future__ import annotations

import http.client
import subprocess
import time

IMAGE = "hr-system-api"
NETWORK = "hr-system_default"
CONTAINER = "hr-throwaway"
DATABASE = "hr_throwaway"
PORT = 8099


def _compose(*arguments: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *arguments],
                          capture_output=True, text=True, check=check)


def _psql(sql: str) -> None:
    result = _compose("exec", "-T", "db", "psql", "-U", "hr", "-d", "postgres",
                      "-c", sql, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[-400:])


class Throwaway:
    """The interface, on a database that is deleted when this block ends."""

    def __init__(self, port: int = PORT) -> None:
        self.port = port
        self.base = f"http://{CONTAINER}:8100"
        self.published = ("127.0.0.1", port)
        self._started = False

    def __enter__(self) -> "Throwaway":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()

    def run(self, *arguments: str) -> str:
        """One `hr` command inside the throwaway container."""
        result = subprocess.run(
            ["docker", "exec", CONTAINER, *arguments],
            capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(
                f"{' '.join(arguments)} failed:\n{result.stdout[-800:]}"
                f"\n{result.stderr[-800:]}")
        return result.stdout

    def start(self) -> None:
        # Anything left over from a run that died. Killed and dropped by name.
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
        _psql(f'DROP DATABASE IF EXISTS "{DATABASE}"')
        _psql(f'CREATE DATABASE "{DATABASE}" OWNER hr')
        self._started = True

        root = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                              capture_output=True, text=True,
                              check=True).stdout.strip()
        subprocess.run(
            ["docker", "run", "-d", "--name", CONTAINER,
             "--network", NETWORK,
             "-e", f"DATABASE_URL=postgresql+psycopg://hr:hr@db:5432/{DATABASE}",
             "-p", f"127.0.0.1:{self.port}:8100",
             "-v", f"{root}/fixtures:/srv/fixtures:ro",
             IMAGE],
            capture_output=True, text=True, check=True)

        for _ in range(120):
            try:
                connection = http.client.HTTPConnection(*self.published, timeout=2)
                connection.request("GET", "/api/health")
                if connection.getresponse().status == 200:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError("the throwaway interface never answered")

        # Everything the sheet needs to draw a month. No punches: leave is what
        # is being looked at, and a cell with leave in it does not need one.
        self.run("hr", "seed")
        self.run("hr", "employees", "import",
                 "/srv/fixtures/employees_punch_demo.xlsx",
                 "--mapping", "/srv/fixtures/employees_punch_demo.mapping.toml",
                 "--allow-new", "group", "--accept-leading-zero-pins")
        self.run("hr", "schedule", "seed-provisional")
        self.run("hr", "calendar", "import",
                 "/srv/fixtures/holidays_provisional_2026.xlsx",
                 "--mapping", "/srv/fixtures/holidays.mapping.toml",
                 "--year", "2026")

    def stop(self) -> None:
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
        if self._started:
            _psql(f'DROP DATABASE IF EXISTS "{DATABASE}"')
        self._started = False


if __name__ == "__main__":
    with Throwaway() as app:
        print(f"{app.base}   ·   http://{app.published[0]}:{app.published[1]}/")
        print(app.run("hr", "status"))
