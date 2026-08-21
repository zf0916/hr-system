# CLAUDE.md

## What this is

An attendance and leave system for a factory, replacing punch cards and hand-filled sheets. It captures attendance and leave and hands the data to Accounts. **It never calculates pay.**

**Read `SPEC.md` before implementing anything.** It is the whole specification, including §12, the device protocol contract — that section is fixed by the firmware, not a design to improve. **Read `BUILD.md`** for the current step and what is deliberately parked.

There is no separate decisions log, plan document or assumptions file. If a rule matters, it is in SPEC.md. If it is not in SPEC.md, it is not decided.

Separate codebase and database from Production Tracking. No integration exists or is being built.

## Stack

Python/FastAPI managed with **uv** · React + Tailwind · PostgreSQL · Docker Compose · on-premises.

`uv add <pkg>`, never `pip install`. Run with `uv run`. Both the project file and the lock file are committed. **Adding a dependency means saying why in the same task.**

## Schema

**SPEC.md contains no schema, on purpose.** Derive it from what the spec describes and keep the model in code as the single source.

**There are no migrations yet.** Until employees are punching for real, change the schema by dropping and recreating the database. Do not write migration files. **This changes on the first day of the parallel run** — from then, raw device capture cannot be recreated, and migrations are the only way to change anything.

**Real device capture now exists in the raw layer and cannot be recreated. Never drop that table** — schema changes are still free, but `raw_request` is preserved across them by dump, recreate, restore, replay, the procedure BUILD.md records. That is not a migration and does not become one.

## Rules

- **New scope stops.** If a request looks like a feature or a structural change the spec does not describe, say so and ask before building. Skip for bug fixes, tooling and scoped continuations.
- **The four blocked areas stop too:** leave entitlement rules, the Accounts export format, the government-application field set, and reports. If a task needs one, say so rather than inventing it.
- **Every assumed value is a row, never a constant.** Schedules, grace periods, thresholds, period boundaries, leave codes, holidays. **A new assumption made while implementing gets added to SPEC.md §9 in the same task.**
- **Run the device simulator after any change to a device route.** It exercises duplicate re-push, a GBK body, a binary photo and malformed input. It must exit clean.
- **A gate is not passed until a deliberate mistake makes it fail.**
- **Say nothing about commit state without running `git status` first.** A report describes the repository, not what this session happened to do. Work committed by somebody else is committed, and "still uncommitted" written from memory is a claim about the past — this repo gets committed between tasks. If it has not been checked, leave it out.
- Update SPEC.md in the same task when a change makes something in it untrue. A stale spec actively misleads.

## Conventions

- Server-observed times carry a timezone. Device-reported times are stored as sent, with the original string, **never converted on the way in**.
- The raw capture layer is append-only and is never validated, deduplicated or cleaned. Everything below it is rebuildable by replay.
- A parser change means bumping the parser version and replaying — **never re-collecting from the device.**

## Environment

Windows with WSL2 (Ubuntu). Claude Code runs in the WSL2 shell inside VS Code. Docker Desktop on the WSL2 backend. The repo lives in the Linux filesystem at ~/projects, never under /mnt/c.