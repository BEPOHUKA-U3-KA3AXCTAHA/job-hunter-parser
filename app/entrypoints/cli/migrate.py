"""Alembic CLI wrapper — points at app/infra/db/alembic.ini.

Usage:
    python -m app.entrypoints.cli.migrate upgrade [head]
    python -m app.entrypoints.cli.migrate revision -m "describe change"   # autogenerate is on
    python -m app.entrypoints.cli.migrate current
    python -m app.entrypoints.cli.migrate history
    python -m app.entrypoints.cli.migrate downgrade -1
    python -m app.entrypoints.cli.migrate stamp head
    python -m app.entrypoints.cli.migrate ...                              # any other alembic subcommand

`revision` is wrapped to set --autogenerate by default. Pass `--no-autogenerate`
to disable. Everything else is forwarded verbatim to alembic.
"""
from __future__ import annotations

import sys
from pathlib import Path

from alembic.config import CommandLine

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "infra" / "db" / "alembic.ini"


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])

    # Default `revision` to autogenerate (matches classfieds workflow). Skip if
    # caller explicitly opted out or already requested it.
    if argv and argv[0] == "revision":
        if "--autogenerate" not in argv and "--no-autogenerate" not in argv:
            argv.insert(1, "--autogenerate")
        argv = [a for a in argv if a != "--no-autogenerate"]

    full_argv = ["-c", str(ALEMBIC_INI), *argv]
    return CommandLine().main(argv=full_argv) or 0


if __name__ == "__main__":
    raise SystemExit(main())
