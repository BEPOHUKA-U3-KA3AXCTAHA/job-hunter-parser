from __future__ import annotations

import asyncio
import csv
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="jhp",
    help="Job Hunter Parser - multi-channel outreach automation",
    no_args_is_help=True,
)
console = Console()

SOURCES = ["remoteok", "web3", "linkedin", "rustjobs"]


def _get_scraper(source: str, category: str = "rust"):
    if source == "remoteok":
        from src.companies.scrapers.remoteok import RemoteOKScraper
        return RemoteOKScraper()
    elif source == "web3":
        from src.companies.scrapers.web3career import Web3CareerScraper
        return Web3CareerScraper(category=category)
    elif source == "linkedin":
        from src.companies.scrapers.linkedin import LinkedInScraper
        return LinkedInScraper()
    elif source == "rustjobs":
        from src.companies.scrapers.rustjobs import RustJobsScraper
        return RustJobsScraper()
    else:
        console.print(f"[red]Unknown source:[/] {source}. Available: {', '.join(SOURCES)}")
        raise typer.Exit(1)


def _format_salary(salary_min: int | None, salary_max: int | None) -> str:
    if salary_min and salary_max:
        return f"${salary_min // 1000}k-${salary_max // 1000}k"
    return "-"


@app.command()
def version() -> None:
    """Print version."""
    console.print("[bold cyan]job-hunter-parser[/] v0.1.0")


@app.command()
def scrape(
    source: str = typer.Argument(..., help="remoteok | web3 | linkedin | rustjobs"),
    limit: int = typer.Option(50, help="Max companies to fetch"),
    tech: list[str] | None = typer.Option(None, help="Filter by tech, e.g. --tech python --tech rust"),
    output: str | None = typer.Option(None, "-o", help="Save to CSV file, e.g. -o companies.csv"),
) -> None:
    """Scrape companies from a source."""

    async def _run():
        from src.shared import SearchCriteria
        scraper = _get_scraper(source)
        criteria = SearchCriteria(tech_stack=tech or ["python", "rust"], limit_per_source=limit)

        table = Table(title=f"Companies from {source}")
        table.add_column("#", style="dim", width=4)
        table.add_column("Company", style="cyan", min_width=20)
        table.add_column("Location", min_width=15)
        table.add_column("Tech Stack", min_width=30)
        table.add_column("URL", style="dim")

        rows: list[dict] = []
        count = 0
        async for company in scraper.fetch_companies(criteria):
            count += 1
            techs = ", ".join(sorted(company.tech_stack.technologies)[:6])
            table.add_row(
                str(count),
                company.name,
                company.location or "Remote",
                techs,
                company.source_url or "-",
            )
            rows.append({
                "name": company.name,
                "location": company.location or "Remote",
                "tech_stack": techs,
                "source": source,
                "url": company.source_url or "",
                "is_hiring": company.is_hiring,
            })

        console.print(table)
        console.print(f"\n[green]Total:[/] {count} companies")

        if output:
            _save_csv(output, rows)

    asyncio.run(_run())


@app.command()
def jobs(
    source: str = typer.Argument(..., help="remoteok | web3 | linkedin | rustjobs"),
    limit: int = typer.Option(20, help="Max job postings"),
    tech: list[str] | None = typer.Option(None, help="Filter by tech"),
    output: str | None = typer.Option(None, "-o", help="Save to CSV file, e.g. -o jobs.csv"),
) -> None:
    """List job postings from a source."""

    async def _run():
        from src.shared import SearchCriteria
        scraper = _get_scraper(source)
        criteria = SearchCriteria(tech_stack=tech or ["python", "rust"], limit_per_source=limit)

        table = Table(title=f"Job Postings from {source}")
        table.add_column("#", style="dim", width=4)
        table.add_column("Title", style="cyan", min_width=30)
        table.add_column("Seniority", min_width=8)
        table.add_column("Salary", style="green", min_width=12)
        table.add_column("Location", min_width=15)
        table.add_column("URL", style="dim")

        rows: list[dict] = []
        count = 0
        async for posting in scraper.fetch_job_postings(criteria):
            count += 1
            salary = _format_salary(posting.salary_min, posting.salary_max)

            table.add_row(
                str(count),
                posting.title[:50],
                str(posting.seniority),
                salary,
                posting.location or "Remote",
                posting.source_url or "-",
            )
            rows.append({
                "title": posting.title,
                "seniority": str(posting.seniority),
                "salary_min": posting.salary_min or "",
                "salary_max": posting.salary_max or "",
                "salary": salary,
                "location": posting.location or "Remote",
                "is_remote": posting.is_remote,
                "tech_stack": ", ".join(sorted(posting.tech_stack.technologies)[:8]),
                "source": source,
                "url": posting.source_url or "",
            })

        console.print(table)
        console.print(f"\n[green]Total:[/] {count} postings")

        if output:
            _save_csv(output, rows)

    asyncio.run(_run())


@app.command("scrape-all")
def scrape_all(
    limit: int = typer.Option(30, help="Max per source"),
    tech: list[str] | None = typer.Option(None, help="Filter by tech"),
    output: str = typer.Option("all_jobs.csv", "-o", help="Output CSV file"),
) -> None:
    """Scrape jobs from ALL sources at once and save to one CSV."""

    async def _run():
        from src.shared import SearchCriteria
        all_rows: list[dict] = []
        criteria = SearchCriteria(tech_stack=tech or ["python", "rust"], limit_per_source=limit)

        for source_name in SOURCES:
            console.print(f"\n[bold yellow]Scraping {source_name}...[/]")
            try:
                scraper = _get_scraper(source_name)
                count = 0
                async for posting in scraper.fetch_job_postings(criteria):
                    count += 1
                    salary = _format_salary(posting.salary_min, posting.salary_max)
                    all_rows.append({
                        "title": posting.title,
                        "seniority": str(posting.seniority),
                        "salary_min": posting.salary_min or "",
                        "salary_max": posting.salary_max or "",
                        "salary": salary,
                        "location": posting.location or "Remote",
                        "is_remote": posting.is_remote,
                        "tech_stack": ", ".join(sorted(posting.tech_stack.technologies)[:8]),
                        "source": source_name,
                        "url": posting.source_url or "",
                    })
                console.print(f"  [green]{count}[/] postings from {source_name}")
            except Exception as e:
                console.print(f"  [red]Error:[/] {e}")

        _save_csv(output, all_rows)
        console.print(f"\n[bold green]Total: {len(all_rows)} jobs saved to {output}[/]")

    asyncio.run(_run())


@app.command()
def hunt(
    limit: int | None = typer.Option(None, help="Max companies per source (default from config.toml)"),
    tech: list[str] | None = typer.Option(None, help="Tech filter, e.g. --tech python --tech rust"),
    salary_min: int | None = typer.Option(None, help="Min salary USD/year, e.g. 60000"),
    channel: str | None = typer.Option(None, help="Message channel (default from config.toml)"),
    output: str = typer.Option("messages_full.csv", "-o", help="Output CSV"),
    skip_fresh_days: int | None = typer.Option(None, help="Skip re-enrichment if contacts verified within N days (0 = always refresh, default from config.toml)"),
) -> None:
    """FULL PIPELINE: scrape all sources -> find contacts -> generate messages -> CSV.

    Contact sources tried in order: TheOrg (free) -> Apollo (paid) -> Apify (paid).
    Whichever finds data first is used per company.

    Secrets (API keys) come from .env. App config from config.toml.
    """
    from src.config import get_secrets, load_app_config
    from src.messages.models import MessageChannel
    from src.messages.repo import SqliteMessageRepository
    from src.pipeline import run_pipeline
    from src.shared import CandidateProfile, SearchCriteria

    secrets = get_secrets()
    app_config = load_app_config()

    eff_limit = limit if limit is not None else app_config.default_limit
    eff_channel = channel if channel else app_config.default_channel
    eff_skip_fresh = skip_fresh_days if skip_fresh_days is not None else app_config.skip_fresh_days
    eff_tech = tech if tech else app_config.default_tech

    async def _run():
        criteria = SearchCriteria(
            tech_stack=eff_tech,
            salary_min_usd=salary_min,
            limit_per_source=eff_limit,
        )
        profile = CandidateProfile()

        # Job board scrapers
        sources = []
        for name in SOURCES:
            try:
                sources.append(_get_scraper(name))
            except Exception:
                pass

        # Decision maker sources (tried in order)
        dm_searches = []
        enrichments = []

        # 1. TheOrg - always free, primary source
        from src.people.adapters.theorg import TheOrgScraper
        theorg = TheOrgScraper()
        dm_searches.append(theorg)
        enrichments.append(theorg)
        console.print("[green]TheOrg adapter enabled (free)[/]")

        # 2. Apollo - paid plan needed for API
        if secrets.apollo_api_key:
            from src.people.adapters.apollo import ApolloAdapter
            apollo = ApolloAdapter(secrets.apollo_api_key)
            dm_searches.append(apollo)
            enrichments.append(apollo)
            console.print("[green]Apollo adapter enabled (paid)[/]")

        # 3. Apify - $5-49 LinkedIn scraping
        if secrets.apify_api_key:
            from src.people.adapters.apify import ApifyAdapter
            apify = ApifyAdapter(secrets.apify_api_key)
            dm_searches.append(apify)
            enrichments.append(apify)
            console.print("[green]Apify adapter enabled (paid, ~$5/1k)[/]")

        # LLM (optional)
        llm = None
        if secrets.anthropic_api_key:
            from src.messages.llm import ClaudeLLMAdapter
            llm = ClaudeLLMAdapter(secrets.anthropic_api_key)
            console.print("[green]Claude message generation enabled[/]")
        else:
            console.print("[yellow]No ANTHROPIC_API_KEY - message bodies will be empty[/]")

        ch = MessageChannel(eff_channel)
        repo = SqliteMessageRepository()

        from src.messages.db import init_db
        await init_db()

        await run_pipeline(
            sources=sources,
            dm_searches=dm_searches,
            enrichments=enrichments,
            llm=llm,
            criteria=criteria,
            profile=profile,
            channel=ch,
            output_csv=output,
            skip_fresh_days=eff_skip_fresh,
            repo=repo,
        )

    asyncio.run(_run())


@app.command()
def stats() -> None:
    """Show DB statistics."""
    from sqlalchemy import func, select
    from src.messages.db import CompanyRow, DecisionMakerRow, MessageRow, get_session_maker, init_db

    async def _run():
        await init_db()
        Session = get_session_maker()
        async with Session() as session:
            total_companies = (await session.execute(select(func.count(CompanyRow.id)))).scalar() or 0
            total_dms = (await session.execute(select(func.count(DecisionMakerRow.id)))).scalar() or 0
            total_leads = (await session.execute(select(func.count(MessageRow.id)))).scalar() or 0

            # Companies with/without contacts
            with_contacts = (await session.execute(
                select(func.count(func.distinct(DecisionMakerRow.company_id)))
            )).scalar() or 0

            # By source
            by_source = await session.execute(
                select(CompanyRow.source, func.count(CompanyRow.id)).group_by(CompanyRow.source)
            )

            # By role
            by_role = await session.execute(
                select(DecisionMakerRow.role, func.count(DecisionMakerRow.id)).group_by(DecisionMakerRow.role)
            )

        table = Table(title="Database Stats")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("Total companies", str(total_companies))
        table.add_row("Total contacts", str(total_dms))
        table.add_row("Total leads", str(total_leads))
        table.add_row("Companies with contacts", f"{with_contacts} ({with_contacts * 100 // max(total_companies, 1)}%)")
        console.print(table)

        src_table = Table(title="By Source")
        src_table.add_column("Source", style="cyan")
        src_table.add_column("Count", style="green")
        for source, count in by_source:
            src_table.add_row(source or "unknown", str(count))
        console.print(src_table)

        role_table = Table(title="Contacts by Role")
        role_table.add_column("Role", style="cyan")
        role_table.add_column("Count", style="green")
        for role, count in by_role:
            role_table.add_row(role, str(count))
        console.print(role_table)

    asyncio.run(_run())


@app.command()
def companies(
    limit: int = typer.Option(50, help="Max rows"),
    source: str | None = typer.Option(None, help="Filter by source"),
) -> None:
    """List all companies in DB."""
    from sqlalchemy import select
    from src.messages.db import CompanyRow, get_session_maker, init_db

    async def _run():
        await init_db()
        Session = get_session_maker()
        async with Session() as session:
            stmt = select(CompanyRow).order_by(CompanyRow.last_seen_at.desc()).limit(limit)
            if source:
                stmt = stmt.where(CompanyRow.source == source)
            result = await session.execute(stmt)
            rows = result.scalars().all()

        table = Table(title=f"Companies ({len(rows)})")
        table.add_column("#", style="dim", width=4)
        table.add_column("Name", style="cyan")
        table.add_column("Source")
        table.add_column("Location")
        table.add_column("Tech")
        table.add_column("Last seen", style="dim")

        for i, r in enumerate(rows, 1):
            table.add_row(
                str(i), r.name, r.source or "-",
                (r.location or "Remote")[:25],
                (r.tech_stack or "")[:35],
                r.last_seen_at.strftime("%Y-%m-%d"),
            )
        console.print(table)

    asyncio.run(_run())


@app.command()
def contacts(
    limit: int = typer.Option(50, help="Max rows"),
    role: str | None = typer.Option(None, help="Filter by role (ceo|cto|founder|...)"),
    company: str | None = typer.Option(None, help="Filter by company name"),
    max_age_days: int = typer.Option(30, help="Only show contacts verified within N days (0=all)"),
) -> None:
    """List all decision makers in DB."""
    from datetime import datetime, timedelta
    from sqlalchemy import select
    from src.messages.db import CompanyRow, DecisionMakerRow, get_session_maker, init_db

    async def _run():
        await init_db()
        Session = get_session_maker()
        async with Session() as session:
            stmt = select(DecisionMakerRow, CompanyRow).join(CompanyRow).order_by(DecisionMakerRow.last_seen_at.desc()).limit(limit)
            if role:
                stmt = stmt.where(DecisionMakerRow.role == role)
            if company:
                stmt = stmt.where(CompanyRow.name.ilike(f"%{company}%"))
            if max_age_days > 0:
                cutoff = datetime.utcnow() - timedelta(days=max_age_days)
                stmt = stmt.where(DecisionMakerRow.last_seen_at >= cutoff)
            result = await session.execute(stmt)
            rows = result.all()

        table = Table(title=f"Contacts ({len(rows)})")
        table.add_column("#", style="dim", width=4)
        table.add_column("Name", style="cyan")
        table.add_column("Role")
        table.add_column("Company")
        table.add_column("Verified", style="green")
        table.add_column("LinkedIn", style="dim")

        now = datetime.utcnow()
        for i, (dm, comp) in enumerate(rows, 1):
            age_days = (now - dm.last_seen_at).days
            freshness = f"{age_days}d ago" if age_days > 0 else "today"
            freshness_style = "[green]" if age_days < 7 else "[yellow]" if age_days < 30 else "[red]"
            table.add_row(
                str(i), dm.full_name,
                (dm.title_raw or dm.role)[:30],
                comp.name[:30],
                f"{freshness_style}{freshness}[/]",
                (dm.linkedin_url or "-")[:40],
            )
        console.print(table)

    asyncio.run(_run())


@app.command()
def stale(
    max_age_days: int = typer.Option(30, help="Contacts older than N days are stale"),
    limit: int = typer.Option(50, help="Max rows"),
) -> None:
    """List contacts that haven't been re-verified in N days."""
    from datetime import datetime, timedelta
    from sqlalchemy import select
    from src.messages.db import CompanyRow, DecisionMakerRow, get_session_maker, init_db

    async def _run():
        await init_db()
        cutoff = datetime.utcnow() - timedelta(days=max_age_days)
        Session = get_session_maker()
        async with Session() as session:
            stmt = (
                select(DecisionMakerRow, CompanyRow)
                .join(CompanyRow)
                .where(DecisionMakerRow.last_seen_at < cutoff)
                .order_by(DecisionMakerRow.last_seen_at.asc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = result.all()

        console.print(f"[yellow]Contacts older than {max_age_days} days (need re-scraping):[/]")
        table = Table()
        table.add_column("Name", style="cyan")
        table.add_column("Role")
        table.add_column("Company")
        table.add_column("Last verified", style="red")
        now = datetime.utcnow()
        for dm, comp in rows:
            age = (now - dm.last_seen_at).days
            table.add_row(dm.full_name, dm.role, comp.name, f"{age} days ago")
        console.print(table)
        console.print(f"\nTotal stale: [red]{len(rows)}[/]")

    asyncio.run(_run())


@app.command()
def retry(
    status: str = typer.Option("no_reply", help="Retry leads with this status (no_reply | rejected | new)"),
    limit: int = typer.Option(50, help="Max leads to retry"),
) -> None:
    """Create new attempts for leads that didn't get a reply.

    Bumps attempt_no on each. Use after status=no_reply to re-target stale leads.
    """
    from sqlalchemy import select
    from src.messages.db import MessageRow, get_session_maker, init_db
    from src.messages.repo import SqliteMessageRepository

    async def _run():
        await init_db()
        repo = SqliteMessageRepository()
        Session = get_session_maker()
        created = 0
        async with Session() as session:
            # Find existing leads with given status, only the latest attempt per dm
            result = await session.execute(
                select(MessageRow).where(MessageRow.status == status).limit(limit)
            )
            leads = result.scalars().all()
            console.print(f"Found {len(leads)} leads with status='{status}'")

            for old_lead in leads:
                new_lead = await repo.create_retry(old_lead.decision_maker_id, score=old_lead.relevance_score)
                if new_lead:
                    created += 1
                    console.print(f"  → new attempt #{new_lead.attempt_no} for dm {old_lead.decision_maker_id}")

        console.print(f"\n[green]Created {created} retry leads[/]")

    asyncio.run(_run())


@app.command("reset-db")
def reset_db() -> None:
    """Drop all tables and recreate (WIPES DATA, only for SQLite)."""
    import os
    from src.messages.db import _get_database_url, init_db
    url = _get_database_url()
    if url.startswith("sqlite"):
        path = url.split("///")[-1]
        if os.path.exists(path):
            os.remove(path)
            console.print(f"[red]Deleted {path}[/]")
    else:
        console.print(f"[yellow]Non-SQLite DB ({url}). Drop tables manually.[/]")
    asyncio.run(init_db())
    console.print("[green]Fresh DB initialized[/]")


def _save_csv(filepath: str, rows: list[dict]) -> None:
    if not rows:
        console.print("[yellow]No data to save[/]")
        return

    path = Path(filepath)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    console.print(f"[green]Saved to {path.resolve()}[/]")


if __name__ == "__main__":
    app()
