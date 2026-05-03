"""CLI for managing the form_answers Q&A cache.

Usage:
    python -m app.entrypoints.cli.qa list
    python -m app.entrypoints.cli.qa review                         # low-confidence LLM rows
    python -m app.entrypoints.cli.qa add "LinkedIn*" "https://..."  # hand-curated
    python -m app.entrypoints.cli.qa delete <question fragment>

CLI is a composition root — it constructs a UoW (the only place outside
adapter code where session lifecycle is managed) and threads it through
to the repo methods.
"""
from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select

from app.infra.db.tables.form_answers import FormAnswerRow
from app.modules.applies import default_uow

app = typer.Typer(help="Manage cached answers for ATS form questions.")
console = Console()


@app.command("list")
def list_cmd(
    limit: int = typer.Option(100, help="Max rows to show"),
    source: str = typer.Option("", help="Filter by source: 'user' | 'llm'"),
):
    """Show all cached Q&A pairs sorted by last_used_at desc."""
    async def _run():
        async with default_uow() as uow:
            rows = await uow.qa_cache.list_all(limit=limit, source=source or None)
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
        # Fetch list inside one read UoW
        async with default_uow() as read_uow:
            rows = await read_uow.qa_cache.list_low_confidence(threshold=threshold)
        if not rows:
            console.print(f"[green]No LLM-source rows below conf {threshold}. Cache is clean.[/]")
            return
        console.print(f"[yellow]{len(rows)} low-confidence answers — press Enter to accept, type new value to override, or 'd' to delete:[/]\n")
        for r in rows:
            console.print(f"\n[bold]Q:[/] {r.question_raw}")
            if r.options:
                console.print(f"[dim]   options: {r.options}[/]")
            console.print(f"[dim]   from: {r.last_company or '?'} / {r.last_job_title or '?'}  used {r.used_count}× | conf {r.confidence:.2f}[/]")
            console.print(f"[cyan]A (LLM):[/] {r.answer}")
            new = console.input("[yellow]>>> [/]").strip()
            async with default_uow() as uow:
                if new == "d":
                    row = (await uow.qa_cache._s.execute(  # noqa: SLF001 — composition-root delete
                        select(FormAnswerRow).where(FormAnswerRow.id == r.id)
                    )).scalar_one()
                    await uow.qa_cache._s.delete(row)
                    await uow.commit()
                    console.print("[red]deleted[/]")
                elif new:
                    await uow.qa_cache.upsert_user_answer(r.question_raw, new, r.options)
                    await uow.commit()
                    console.print("[green]saved[/]")
                else:
                    # Promote LLM → user without changing answer (treat as confirmed)
                    await uow.qa_cache.upsert_user_answer(r.question_raw, r.answer, r.options)
                    await uow.commit()
                    console.print("[green]confirmed[/]")

    asyncio.run(_run())


@app.command("add")
def add(label: str, answer: str):
    """Add or override a Q&A entry as user-curated (highest priority)."""
    async def _run():
        async with default_uow() as uow:
            await uow.qa_cache.upsert_user_answer(label, answer)
            await uow.commit()
        console.print(f"[green]✓ saved[/] {label!r} → {answer!r}")
    asyncio.run(_run())


@app.command("delete")
def delete(fragment: str):
    """Delete entries whose question_raw contains <fragment> (substring match)."""
    async def _run():
        async with default_uow() as uow:
            session = uow.qa_cache._s  # noqa: SLF001 — composition-root delete
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
                await uow.commit()
                console.print(f"[green]✓ deleted {len(rows)}[/]")

    asyncio.run(_run())


if __name__ == "__main__":
    app()
