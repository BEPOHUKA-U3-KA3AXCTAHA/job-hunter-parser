"""CLI for managing the form_answers Q&A cache.

Usage:
    python -m app.entrypoints.cli.qa list
    python -m app.entrypoints.cli.qa review                         # low-confidence LLM rows
    python -m app.entrypoints.cli.qa add "LinkedIn*" "https://..."  # hand-curated
    python -m app.entrypoints.cli.qa delete <question fragment>
"""
from __future__ import annotations

import asyncio
import sys

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select

from app.infra.db import get_session_maker
from app.infra.db.tables.form_answers import FormAnswerRow
from app.modules.applies.adapters.repository.qa_cache import (
    list_all,
    list_low_confidence,
    upsert_user_answer,
)

app = typer.Typer(help="Manage cached answers for ATS form questions.")
console = Console()


@app.command("list")
def list_cmd(
    limit: int = typer.Option(100, help="Max rows to show"),
    source: str = typer.Option("", help="Filter by source: 'user' | 'llm'"),
):
    """Show all cached Q&A pairs sorted by last_used_at desc."""
    async def _run():
        rows = await list_all(limit=limit, source=source or None)
        if not rows:
            console.print("[yellow]Cache is empty.[/]")
            return
        t = Table(show_lines=False)
        t.add_column("source", style="cyan", width=6)
        t.add_column("conf", justify="right", width=4)
        t.add_column("used", justify="right", width=4)
        t.add_column("question", overflow="fold")
        t.add_column("answer", overflow="fold", style="green")
        for r in rows:
            t.add_row(
                r.source, f"{r.confidence:.1f}", str(r.used_count or 0),
                r.question_raw[:90], (r.answer or "")[:90],
            )
        console.print(t)
        console.print(f"[dim]{len(rows)} rows[/]")

    asyncio.run(_run())


@app.command("review")
def review(
    threshold: float = typer.Option(0.7, help="Show LLM rows with confidence below this"),
):
    """Walk every uncertain LLM-source row and let user accept or correct."""
    async def _run():
        rows = await list_low_confidence(threshold=threshold)
        if not rows:
            console.print(f"[green]No LLM-source rows below conf {threshold}. Cache is clean.[/]")
            return
        console.print(f"[yellow]{len(rows)} low-confidence answers — press Enter to accept, type new value to override, or 'd' to delete:[/]\n")
        Session = get_session_maker()
        for r in rows:
            console.print(f"\n[bold]Q:[/] {r.question_raw}")
            if r.options:
                console.print(f"[dim]   options: {r.options}[/]")
            console.print(f"[dim]   from: {r.last_company or '?'} / {r.last_job_title or '?'}  used {r.used_count}× | conf {r.confidence:.2f}[/]")
            console.print(f"[cyan]A (LLM):[/] {r.answer}")
            new = console.input("[yellow]>>> [/]").strip()
            if new == "d":
                async with Session() as s:
                    row = (await s.execute(select(FormAnswerRow).where(FormAnswerRow.id == r.id))).scalar_one()
                    await s.delete(row)
                    await s.commit()
                console.print("[red]deleted[/]")
            elif new:
                await upsert_user_answer(r.question_raw, new, r.options)
                console.print("[green]saved[/]")
            else:
                # Promote LLM → user without changing answer (treat as confirmed)
                await upsert_user_answer(r.question_raw, r.answer, r.options)
                console.print("[green]confirmed[/]")

    asyncio.run(_run())


@app.command("add")
def add(label: str, answer: str):
    """Add or override a Q&A entry as user-curated (highest priority)."""
    async def _run():
        await upsert_user_answer(label, answer)
        console.print(f"[green]✓ saved[/] {label!r} → {answer!r}")
    asyncio.run(_run())


@app.command("delete")
def delete(fragment: str):
    """Delete entries whose question_raw contains <fragment> (substring match)."""
    async def _run():
        Session = get_session_maker()
        async with Session() as session:
            rows = list(
                (
                    await session.execute(
                        select(FormAnswerRow).where(FormAnswerRow.question_raw.contains(fragment))
                    )
                ).scalars()
            )
            if not rows:
                console.print(f"[yellow]No rows match {fragment!r}[/]")
                return
            for r in rows:
                console.print(f"  - {r.question_raw[:80]}  →  {r.answer[:60]}")
            console.print(f"[red]Delete {len(rows)} row(s)? [y/N][/]", end=" ")
            if input().strip().lower() == "y":
                for r in rows:
                    await session.delete(r)
                await session.commit()
                console.print(f"[green]✓ deleted {len(rows)}[/]")

    asyncio.run(_run())


if __name__ == "__main__":
    app()
