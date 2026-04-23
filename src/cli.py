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
        scraper = _get_scraper(source)

        table = Table(title=f"Companies from {source}")
        table.add_column("#", style="dim", width=4)
        table.add_column("Company", style="cyan", min_width=20)
        table.add_column("Location", min_width=15)
        table.add_column("Tech Stack", min_width=30)
        table.add_column("URL", style="dim")

        rows: list[dict] = []
        count = 0
        async for company in scraper.fetch_companies(tech_stack_filter=tech, limit=limit):
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
        scraper = _get_scraper(source)

        table = Table(title=f"Job Postings from {source}")
        table.add_column("#", style="dim", width=4)
        table.add_column("Title", style="cyan", min_width=30)
        table.add_column("Seniority", min_width=8)
        table.add_column("Salary", style="green", min_width=12)
        table.add_column("Location", min_width=15)
        table.add_column("URL", style="dim")

        rows: list[dict] = []
        count = 0
        async for posting in scraper.fetch_job_postings(tech_stack_filter=tech, limit=limit):
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
        all_rows: list[dict] = []

        for source_name in SOURCES:
            console.print(f"\n[bold yellow]Scraping {source_name}...[/]")
            try:
                scraper = _get_scraper(source_name)
                count = 0
                async for posting in scraper.fetch_job_postings(tech_stack_filter=tech, limit=limit):
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
    limit: int = typer.Option(20, help="Max companies per source"),
    tech: list[str] | None = typer.Option(None, help="Tech filter, e.g. --tech python --tech rust"),
    salary_min: int | None = typer.Option(None, help="Min salary USD/year, e.g. 60000"),
    channel: str = typer.Option("linkedin", help="Message channel: linkedin | email | twitter"),
    output: str = typer.Option("leads_full.csv", "-o", help="Output CSV"),
    apollo_key: str | None = typer.Option(None, envvar="APOLLO_API_KEY", help="Apollo.io API key (paid plan)"),
    apify_key: str | None = typer.Option(None, envvar="APIFY_API_KEY", help="Apify API key"),
    anthropic_key: str | None = typer.Option(None, envvar="ANTHROPIC_API_KEY", help="Anthropic API key"),
) -> None:
    """FULL PIPELINE: scrape all sources -> find contacts -> generate messages -> CSV.

    Contact sources tried in order: TheOrg (free) -> Apollo (paid) -> Apify (paid).
    Whichever finds data first is used per company.
    """
    from src.outreach.models import OutreachChannel
    from src.pipeline import run_pipeline
    from src.shared import CandidateProfile, SearchCriteria

    async def _run():
        criteria = SearchCriteria(
            tech_stack=tech or ["python", "rust"],
            salary_min_usd=salary_min,
            limit_per_source=limit,
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
        if apollo_key:
            from src.people.adapters.apollo import ApolloAdapter
            apollo = ApolloAdapter(apollo_key)
            dm_searches.append(apollo)
            enrichments.append(apollo)
            console.print("[green]Apollo adapter enabled (paid)[/]")

        # 3. Apify - $5-49 LinkedIn scraping
        if apify_key:
            from src.people.adapters.apify import ApifyAdapter
            apify = ApifyAdapter(apify_key)
            dm_searches.append(apify)
            enrichments.append(apify)
            console.print("[green]Apify adapter enabled (paid, ~$5/1k)[/]")

        # LLM (optional)
        llm = None
        if anthropic_key:
            from src.outreach.llm import ClaudeLLMAdapter
            llm = ClaudeLLMAdapter(anthropic_key)
            console.print("[green]Claude message generation enabled[/]")
        else:
            console.print("[yellow]No ANTHROPIC_API_KEY - messages will be empty[/]")

        ch = OutreachChannel(channel)

        await run_pipeline(
            sources=sources,
            dm_searches=dm_searches,
            enrichments=enrichments,
            llm=llm,
            criteria=criteria,
            profile=profile,
            channel=ch,
            output_csv=output,
        )

    asyncio.run(_run())


@app.command()
def stats() -> None:
    """Show DB statistics."""
    from sqlalchemy import func, select
    from src.leads.db import CompanyRow, DecisionMakerRow, LeadRow, get_session_maker, init_db

    async def _run():
        await init_db()
        Session = get_session_maker()
        async with Session() as session:
            total_companies = (await session.execute(select(func.count(CompanyRow.id)))).scalar() or 0
            total_dms = (await session.execute(select(func.count(DecisionMakerRow.id)))).scalar() or 0
            total_leads = (await session.execute(select(func.count(LeadRow.id)))).scalar() or 0

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
    from src.leads.db import CompanyRow, get_session_maker, init_db

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
) -> None:
    """List all decision makers in DB."""
    from sqlalchemy import select
    from src.leads.db import CompanyRow, DecisionMakerRow, get_session_maker, init_db

    async def _run():
        await init_db()
        Session = get_session_maker()
        async with Session() as session:
            stmt = select(DecisionMakerRow, CompanyRow).join(CompanyRow).order_by(DecisionMakerRow.last_seen_at.desc()).limit(limit)
            if role:
                stmt = stmt.where(DecisionMakerRow.role == role)
            if company:
                stmt = stmt.where(CompanyRow.name.ilike(f"%{company}%"))
            result = await session.execute(stmt)
            rows = result.all()

        table = Table(title=f"Contacts ({len(rows)})")
        table.add_column("#", style="dim", width=4)
        table.add_column("Name", style="cyan")
        table.add_column("Role")
        table.add_column("Company")
        table.add_column("Email", style="green")
        table.add_column("LinkedIn", style="dim")

        for i, (dm, comp) in enumerate(rows, 1):
            table.add_row(
                str(i), dm.full_name,
                (dm.title_raw or dm.role)[:30],
                comp.name[:30],
                (dm.email or "-")[:35],
                (dm.linkedin_url or "-")[:40],
            )
        console.print(table)

    asyncio.run(_run())


@app.command("reset-db")
def reset_db() -> None:
    """Drop all tables and recreate (WIPES DATA)."""
    import os
    from src.leads.db import _DB_PATH
    if os.path.exists(_DB_PATH):
        os.remove(_DB_PATH)
        console.print(f"[red]Deleted {_DB_PATH}[/]")
    from src.leads.db import init_db
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
