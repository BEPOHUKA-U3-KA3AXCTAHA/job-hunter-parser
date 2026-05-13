"""Typer CLI entrypoint. Delete or extend.

Each command is a thin shim: parse args → call a service via UoW
factory → print result. No business logic here.

    python -m app.entrypoints.cli.main create-item --name foo
"""

from __future__ import annotations

import asyncio

import typer
from loguru import logger

from app.modules.example import create_item, default_uow

cli = typer.Typer(help="Your project CLI — replace with real commands.")


@cli.command("create-item")
def create_item_cmd(name: str = typer.Option(..., "--name", help="Item name")) -> None:
    """Create one item — demo of how a CLI command wires to a service."""

    async def _run() -> None:
        item = await create_item(default_uow(), name=name)
        typer.echo(f"created: {item}")

    try:
        asyncio.run(_run())
    except Exception as e:
        logger.exception("create-item failed: {}", e)
        raise typer.Exit(1) from e


if __name__ == "__main__":
    cli()
