"""CLI entrypoint — composition root.

This is where adapters get wired to ports and use-cases get invoked. Per
classfieds README, only this layer (and any future http/) knows about
concrete adapters.

Run: `python -m app.entrypoints.cli.main <command>`
"""
from app.entrypoints.cli.main import app  # noqa: F401
