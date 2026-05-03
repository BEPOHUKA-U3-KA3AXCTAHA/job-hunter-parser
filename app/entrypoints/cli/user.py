"""CLI for the users table — single-user mode for now.

Usage:
    python -m app.entrypoints.cli.user show
    python -m app.entrypoints.cli.user set-info "<multi-line text>"
    python -m app.entrypoints.cli.user edit-info        # opens $EDITOR
    python -m app.entrypoints.cli.user create EMAIL     # adds an empty user row
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path

import typer
from rich.console import Console

from app.modules.users import User, default_uow

app = typer.Typer(help="Manage application users (single-user mode for now).")
console = Console()


@app.command("show")
def show():
    """Print the current default user + their info text."""
    async def _run():
        async with default_uow() as uow:
            user = await uow.users.get_default()
        if not user:
            console.print("[yellow]No users in DB. Run `jhp-user create EMAIL` to seed one.[/]")
            return
        console.print(f"[cyan]id:[/]      {user.id}")
        console.print(f"[cyan]email:[/]   {user.email}")
        console.print(f"[cyan]password:[/] {'<set>' if user.password_hash else '(none — login not implemented)'}")
        console.print(f"[cyan]created:[/] {user.created_at:%Y-%m-%d %H:%M}")
        console.print(f"[cyan]updated:[/] {user.updated_at:%Y-%m-%d %H:%M}")
        console.print("\n[bold]info:[/]")
        console.print(user.info or "[dim](empty — `jhp-user edit-info` to fill)[/]")
    asyncio.run(_run())


@app.command("create")
def create(email: str):
    """Add a new user row with the given email (info empty)."""
    async def _run():
        async with default_uow() as uow:
            existing = await uow.users.get_by_email(email)
            if existing:
                console.print(f"[yellow]User with email {email} already exists.[/]")
                return
            await uow.users.upsert(User(email=email, info=""))
            await uow.commit()
        console.print(f"[green]✓ created user[/] {email}")
    asyncio.run(_run())


@app.command("set-info")
def set_info(text: str):
    """Replace the default user's info field with the given text."""
    async def _run():
        async with default_uow() as uow:
            user = await uow.users.get_default()
            if not user:
                console.print("[red]No users in DB. Run `jhp-user create EMAIL` first.[/]")
                raise typer.Exit(1)
            await uow.users.update_info(user.email, text)
            await uow.commit()
        console.print(f"[green]✓ updated info for[/] {user.email} ({len(text)} chars)")
    asyncio.run(_run())


@app.command("edit-info")
def edit_info():
    """Open the default user's info field in $EDITOR (or vi)."""
    async def _run():
        async with default_uow() as uow:
            user = await uow.users.get_default()
        if not user:
            console.print("[red]No users in DB. Run `jhp-user create EMAIL` first.[/]")
            raise typer.Exit(1)
        editor = os.environ.get("EDITOR", "vi")
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w+", delete=False) as f:
            f.write(user.info or "")
            tmp = Path(f.name)
        subprocess.run([editor, str(tmp)], check=False)
        new_text = tmp.read_text()
        tmp.unlink()
        if new_text == (user.info or ""):
            console.print("[yellow]No changes.[/]")
            return
        async with default_uow() as uow:
            await uow.users.update_info(user.email, new_text)
            await uow.commit()
        console.print(f"[green]✓ updated info[/] ({len(new_text)} chars)")
    asyncio.run(_run())


if __name__ == "__main__":
    app()
