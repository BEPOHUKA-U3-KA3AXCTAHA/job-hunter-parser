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
        from app.modules.companies.adapters.scraper.remoteok import RemoteOKScraper
        return RemoteOKScraper()
    elif source == "web3":
        from app.modules.companies.adapters.scraper.web3career import Web3CareerScraper
        return Web3CareerScraper(category=category)
    elif source == "linkedin":
        from app.modules.companies.adapters.scraper.linkedin import LinkedInScraper
        return LinkedInScraper()
    elif source == "rustjobs":
        from app.modules.companies.adapters.scraper.rustjobs import RustJobsScraper
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
        from app.modules.companies import SearchCriteria
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
        from app.modules.companies import SearchCriteria
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
        from app.modules.companies import SearchCriteria
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
    max_applicants: int | None = typer.Option(None, help="Skip job posts with more than N applicants (LinkedIn only — passes when unknown)"),
    max_age_days: int | None = typer.Option(None, help="Skip job posts older than N days (LinkedIn / RemoteOK / web3.career; rustjobs has no date)"),
) -> None:
    """FULL PIPELINE: scrape all sources -> find contacts -> generate messages -> CSV.

    Contact sources tried in order: TheOrg (free) -> Apollo (paid) -> Apify (paid).
    Whichever finds data first is used per company.

    Secrets (API keys) come from .env. App config from config.toml.
    """
    from app.infra.config import get_secrets, load_app_config
    from app.modules.applies import MessageChannel, default_uow
    from app.entrypoints.cli.pipeline import run_pipeline
    from app.modules.companies import SearchCriteria
    from app.modules.users import CandidateProfile

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
            max_applicants=max_applicants,
            max_posted_age_days=max_age_days,
        )
        profile = CandidateProfile()

        # Job board scrapers. Web3.career rotates categories to widen the funnel.
        # RustJobs needs playwright; skip silently if not installed.
        sources = []
        from app.modules.companies.adapters.scraper.remoteok import RemoteOKScraper
        from app.modules.companies.adapters.scraper.linkedin import LinkedInScraper
        from app.modules.companies.adapters.scraper.web3career import Web3CareerScraper
        sources.append(RemoteOKScraper())
        sources.append(LinkedInScraper())
        for cat in ("rust", "python", "backend", "senior"):
            sources.append(Web3CareerScraper(category=cat))
        try:
            from app.modules.companies.adapters.scraper.rustjobs import RustJobsScraper
            sources.append(RustJobsScraper())
        except ImportError:
            console.print("[yellow]playwright not installed → skipping rustjobs[/]")

        # Decision maker sources (tried in order)
        dm_searches = []
        enrichments = []

        # 1. TheOrg - always free, primary source
        from app.modules.people.adapters.search.theorg import TheOrgScraper
        theorg = TheOrgScraper()
        dm_searches.append(theorg)
        enrichments.append(theorg)
        console.print("[green]TheOrg adapter enabled (free)[/]")

        # 1b. Email pattern guesser - always on, free, runs after TheOrg per dm
        from app.modules.people.adapters.search.email_guesser import EmailPatternGuesser
        enrichments.append(EmailPatternGuesser())
        console.print("[green]Email pattern guesser enabled (free)[/]")

        # 2. Apollo - paid plan needed for API
        if secrets.apollo_api_key:
            from app.modules.people.adapters.search.apollo import ApolloAdapter
            apollo = ApolloAdapter(secrets.apollo_api_key)
            dm_searches.append(apollo)
            enrichments.append(apollo)
            console.print("[green]Apollo adapter enabled (paid)[/]")

        # 3. Apify - $5-49 LinkedIn scraping
        if secrets.apify_api_key:
            from app.modules.people.adapters.search.apify import ApifyAdapter
            apify = ApifyAdapter(secrets.apify_api_key)
            dm_searches.append(apify)
            enrichments.append(apify)
            console.print("[green]Apify adapter enabled (paid, ~$5/1k)[/]")

        # LLM (optional). Priority: free providers first.
        # Gemini: 1500 req/day free.  Groq: 6000/day, very fast.  Anthropic: paid per token.
        llm = None
        if secrets.gemini_api_key:
            from app.modules.applies.adapters.llm.gemini import GeminiLLMAdapter
            llm = GeminiLLMAdapter(secrets.gemini_api_key)
            console.print("[green]Gemini Flash message generation enabled (free tier)[/]")
        elif secrets.groq_api_key:
            from app.modules.applies.adapters.llm.groq import GroqLLMAdapter
            llm = GroqLLMAdapter(secrets.groq_api_key)
            console.print("[green]Groq Llama-3.3-70B message generation enabled (free tier)[/]")
        elif secrets.anthropic_api_key:
            from app.modules.applies.adapters.llm.anthropic import ClaudeLLMAdapter
            llm = ClaudeLLMAdapter(secrets.anthropic_api_key)
            console.print("[green]Claude message generation enabled (paid)[/]")
        else:
            console.print("[yellow]No LLM key (GEMINI_API_KEY / GROQ_API_KEY / ANTHROPIC_API_KEY) — message bodies will be empty[/]")

        ch = MessageChannel(eff_channel)

        from app.infra.db import init_db
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
            uow_factory=default_uow,
        )

    asyncio.run(_run())


@app.command()
def stats() -> None:
    """Show DB statistics."""
    from app.infra.db import init_db
    from app.modules.admin import default_uow

    async def _run():
        await init_db()
        async with default_uow() as uow:
            status = await uow.admin.db_status()

        table = Table(title="Database Stats")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("Total companies", str(status.total_companies))
        table.add_row("Total contacts", str(status.total_dms))
        table.add_row("Total applies", str(status.total_applies))
        table.add_row("Sent in last 24h", str(status.sent_today))
        console.print(table)

    asyncio.run(_run())


@app.command()
def companies(
    limit: int = typer.Option(50, help="Max rows"),
    source: str | None = typer.Option(None, help="Filter by source"),
) -> None:
    """List all companies in DB."""
    from app.infra.db import init_db
    from app.modules.admin import default_uow

    async def _run():
        await init_db()
        async with default_uow() as uow:
            rows = await uow.admin.list_companies(limit=limit)
        if source:
            rows = [r for r in rows if r.source == source]

        table = Table(title=f"Companies ({len(rows)})")
        table.add_column("#", style="dim", width=4)
        table.add_column("Name", style="cyan")
        table.add_column("Source")
        table.add_column("Hiring")
        table.add_column("DM scan", style="dim")

        for i, r in enumerate(rows, 1):
            scan = r.last_dm_scan_at.strftime("%Y-%m-%d") if r.last_dm_scan_at else "-"
            table.add_row(
                str(i), r.name, r.source or "-",
                "yes" if r.is_hiring else "-",
                scan,
            )
        console.print(table)

    asyncio.run(_run())


@app.command()
def contacts(
    limit: int = typer.Option(50, help="Max rows"),
    role: str | None = typer.Option(None, help="Filter by role (ceo|cto|founder|...)"),
    company: str | None = typer.Option(None, help="Filter by company name"),
    max_age_days: int = typer.Option(30, help="Only show contacts whose company was DM-scanned within N days (0=all)"),
) -> None:
    """List all decision makers in DB.

    Freshness is per-company (companies.last_dm_scan_at), not per-dm.
    """
    from app.infra.db import init_db
    from app.modules.admin import default_uow

    async def _run():
        await init_db()
        async with default_uow() as uow:
            rows = await uow.admin.list_people(limit=limit)
        if role:
            rows = [r for r in rows if r.role == role]
        if company:
            rows = [r for r in rows if company.lower() in r.company_name.lower()]

        table = Table(title=f"Contacts ({len(rows)})")
        table.add_column("#", style="dim", width=4)
        table.add_column("Name", style="cyan")
        table.add_column("Role")
        table.add_column("Company")
        table.add_column("Channels", style="dim")
        for i, r in enumerate(rows, 1):
            channels = ", ".join(sorted((r.contacts or {}).keys())) or "-"
            table.add_row(str(i), r.full_name, r.role or "-", r.company_name[:30], channels[:40])
        console.print(table)

    asyncio.run(_run())


@app.command("jobs-list")
def jobs_list(
    limit: int = typer.Option(50, help="Max rows"),
    source: str | None = typer.Option(None, help="Filter by source"),
    max_applicants: int | None = typer.Option(None, help="Skip jobs with > N applicants (None = no filter)"),
    max_age_days: int | None = typer.Option(None, help="Only jobs posted within N days"),
    no_orphans: bool = typer.Option(False, help="Skip jobs without a linked company"),
    output: str | None = typer.Option(None, "-o", help="Optional CSV output"),
) -> None:
    """List job_postings from DB with competition filters.

    `applicants_count` is best-effort and only LinkedIn populates it. Posts where
    the source didn't expose the count are kept (treated as unknown).
    """
    from datetime import datetime, timedelta
    from app.infra.db import init_db
    from app.modules.admin import default_uow

    async def _run():
        await init_db()
        async with default_uow() as uow:
            rows = await uow.admin.list_jobs(limit=limit)
        if source:
            rows = [r for r in rows if r.source == source]
        if max_applicants is not None:
            rows = [r for r in rows if r.applicants_count is None or r.applicants_count <= max_applicants]
        if max_age_days is not None:
            cutoff = datetime.utcnow() - timedelta(days=max_age_days)
            rows = [r for r in rows if r.posted_at and r.posted_at >= cutoff]
        if no_orphans:
            rows = [r for r in rows if r.company_name]

        table = Table(title=f"Job Postings ({len(rows)})")
        table.add_column("#", style="dim", width=4)
        table.add_column("Title", style="cyan", min_width=30)
        table.add_column("Company")
        table.add_column("Source")
        table.add_column("Apps", style="green")
        table.add_column("Age")

        csv_rows: list[dict] = []
        now = datetime.utcnow()
        for i, r in enumerate(rows, 1):
            apps = str(r.applicants_count) if r.applicants_count is not None else "-"
            age = f"{(now - r.posted_at).days}d" if r.posted_at else "-"
            comp_name = (r.company_name or "[orphan]")[:25]
            table.add_row(str(i), r.title[:50], comp_name, r.source or "-", apps, age)
            csv_rows.append({
                "title": r.title,
                "company": r.company_name or "",
                "source": r.source or "",
                "url": r.source_url or "",
                "applicants": r.applicants_count if r.applicants_count is not None else "",
                "posted_at": r.posted_at.isoformat() if r.posted_at else "",
                "age_days": (now - r.posted_at).days if r.posted_at else "",
            })
        console.print(table)
        if output:
            _save_csv(output, csv_rows)

    asyncio.run(_run())


@app.command()
def stale(
    max_age_days: int = typer.Option(30, help="Companies whose DM scan is older than N days are stale"),
    limit: int = typer.Option(50, help="Max rows"),
) -> None:
    """List companies whose decision makers haven't been re-scanned in N days.

    Freshness is per-company. `hunt` will refresh these on the next run
    (when skip_fresh_days < age).
    """
    from datetime import datetime
    from app.infra.db import init_db
    from app.modules.admin import default_uow

    async def _run():
        await init_db()
        async with default_uow() as uow:
            rows = await uow.admin.stale_companies(max_age_days=max_age_days, limit=limit)

        console.print(f"[yellow]Companies with stale (>{max_age_days}d) or missing DM scan:[/]")
        table = Table()
        table.add_column("Company", style="cyan")
        table.add_column("Last DM scan", style="red")
        now = datetime.utcnow()
        for r in rows:
            cell = f"{(now - r.last_dm_scan_at).days} days ago" if r.last_dm_scan_at else "never"
            table.add_row(r.name, cell)
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
    from app.infra.db import init_db
    from app.modules.applies import default_uow

    async def _run():
        await init_db()
        created = 0
        async with default_uow() as uow:
            leads = await uow.apply.list_by_status(status, limit=limit)
            console.print(f"Found {len(leads)} leads with status='{status}'")
            for old in leads:
                new_lead = await uow.apply.create_retry(
                    old.decision_maker_id, score=old.relevance_score,
                )
                if new_lead:
                    created += 1
                    console.print(
                        f"  → new attempt #{new_lead.attempt_no} for dm "
                        f"{old.decision_maker_id}"
                    )
            await uow.commit()
        console.print(f"\n[green]Created {created} retry leads[/]")

    asyncio.run(_run())


@app.command()
def curate(
    max_age_days: int = typer.Option(30, help="Skip jobs older than N days"),
    min_score: int = typer.Option(30, help="Drop pairs with score below this"),
    top: int = typer.Option(50, help="Generate letters for top-N pairs (after filter+rank)"),
    dms_per_job: int = typer.Option(2, help="Pitch each job to up to K DMs (CTO + Founder, etc.)"),
    generate: bool = typer.Option(True, help="Call LLM to write letters and persist (otherwise just preview)"),
    dry_run: bool = typer.Option(False, help="Just print the ranked top-N, don't call LLM"),
    output: str = typer.Option("curated_messages.csv", "-o", help="CSV with the curated set"),
) -> None:
    """Filter saturated/stale jobs, rank by candidate fit, generate letters per (job, dm).

    Each surviving job gets ONE letter to its best DM (highest role priority +
    most contacts). Letters land in messages keyed by (job_posting_id, dm_id, attempt_no=1).
    """
    from app.infra.config import get_secrets
    from app.infra.db import init_db
    from app.modules.applies import Message, MessageChannel, MessageStatus
    from app.modules.applies import default_uow, filter_and_score
    from app.modules.users import CandidateProfile

    async def _run():
        await init_db()
        profile = CandidateProfile()

        async with default_uow() as uow:
            bundles = await uow.candidates.load_active_bundles()
        console.print(f"Loaded [cyan]{len(bundles)}[/] (job, company, dms) bundles from DB")

        pairs = filter_and_score(
            bundles, profile,
            max_age_days=max_age_days,
            min_score=min_score,
            dms_per_job=dms_per_job,
        )
        console.print(f"Curated [green]{len(pairs)}[/] pairs (top score={pairs[0].score if pairs else 0})")

        if not pairs:
            console.print("[yellow]No pairs survived the filter — relax --max-age-days or --min-score[/]")
            return

        pairs = pairs[:top]

        # Preview table
        table = Table(title=f"Top {len(pairs)} curated pairs")
        table.add_column("#", style="dim", width=4)
        table.add_column("Score", style="green", width=5)
        table.add_column("Job", style="cyan", min_width=30)
        table.add_column("Company")
        table.add_column("DM")
        table.add_column("Reasons", style="dim")
        for i, p in enumerate(pairs[:25], 1):
            table.add_row(
                str(i), str(p.score),
                p.job.title[:45],
                p.company.name[:20],
                f"{p.dm.full_name} ({p.dm.role.value})"[:30],
                ", ".join(p.reasons)[:50],
            )
        console.print(table)
        if len(pairs) > 25:
            console.print(f"  …and {len(pairs) - 25} more")

        # Always dump the ranked set to CSV — useful even without LLM
        meta_rows = [{
            "score": p.score,
            "company": p.company.name,
            "job_title": p.job.title,
            "job_url": p.job.source_url or "",
            "posted_at": p.job.posted_at.isoformat() if p.job.posted_at else "",
            "applicants": p.job.applicants_count if p.job.applicants_count is not None else "",
            "dm_name": p.dm.full_name,
            "dm_role": p.dm.title_raw or p.dm.role.value,
            "dm_linkedin": p.dm.contacts.get("linkedin", ""),
            "dm_email_guess": p.dm.contacts.get("email_guess", ""),
            "reasons": ", ".join(p.reasons),
        } for p in pairs]
        _save_csv(output, meta_rows)

        if dry_run or not generate:
            console.print("[yellow]--dry-run / --no-generate: no LLM calls, no DB writes[/]")
            return

        # LLM gen
        secrets = get_secrets()
        if not secrets.anthropic_api_key and not secrets.gemini_api_key and not secrets.groq_api_key:
            console.print(
                "[yellow]No LLM key in .env — letters not generated.\n"
                "  Free option: https://aistudio.google.com/apikey → echo 'GEMINI_API_KEY=...' >> .env\n"
                "  Curated metadata saved without bodies.[/]"
            )
            return

        if secrets.gemini_api_key:
            from app.modules.applies.adapters.llm.gemini import GeminiLLMAdapter
            llm = GeminiLLMAdapter(secrets.gemini_api_key)
        elif secrets.groq_api_key:
            from app.modules.applies.adapters.llm.groq import GroqLLMAdapter
            llm = GroqLLMAdapter(secrets.groq_api_key)
        else:
            from app.modules.applies.adapters.llm.anthropic import ClaudeLLMAdapter
            llm = ClaudeLLMAdapter(secrets.anthropic_api_key)
        console.print(f"[green]LLM: {llm.__class__.__name__} ({llm.model_name})[/]")

        rows_for_csv: list[dict] = []
        saved = 0
        for i, p in enumerate(pairs, 1):
            ch = MessageChannel.LINKEDIN if "linkedin" in p.dm.contacts else MessageChannel.EMAIL
            msg = Message(
                decision_maker=p.dm,
                company=p.company,
                job_posting=p.job,
                relevance_score=p.score,
                channel=ch,
                status=MessageStatus.GENERATED,
            )
            try:
                body = await llm.generate_body(msg, profile.summary)
            except Exception as e:
                console.print(f"  [red]LLM failed for {p.dm.full_name} @ {p.company.name}: {e}[/]")
                continue
            if not body:
                continue
            msg.body = body

            async with default_uow() as uow:
                await uow.apply.save(msg)
                await uow.commit()
            saved += 1

            rows_for_csv.append({
                "score": p.score,
                "company": p.company.name,
                "job_title": p.job.title,
                "job_url": p.job.source_url or "",
                "dm_name": p.dm.full_name,
                "dm_role": p.dm.title_raw or p.dm.role.value,
                "dm_linkedin": p.dm.contacts.get("linkedin", ""),
                "dm_email_guess": p.dm.contacts.get("email_guess", ""),
                "channel": ch.value,
                "reasons": ", ".join(p.reasons),
                "body": body.replace("\n", " "),
            })
            console.print(f"  [{i}/{len(pairs)}] [green]{p.company.name}[/] / {p.dm.full_name} → {len(body)} chars")

        if rows_for_csv:
            _save_csv(output, rows_for_csv)
        console.print(f"\n[green]Saved {saved} messages to DB + {output}[/]")

    asyncio.run(_run())


@app.command("apply")
def apply_cmd(
    keywords: str = typer.Option("rust senior remote,python backend remote senior", help="Comma-separated keyword sets to search"),
    limit: int = typer.Option(1, help="How many real Easy Apply submissions this batch (cap MAX=5)"),
    headless: bool = typer.Option(True, help="Run Firefox headless (recommended)"),
    phone: str = typer.Option("", help="Phone for forms (empty = leave unfilled, may fail required-field check)"),
) -> None:
    """LinkedIn Easy Apply via Selenium-driven REAL Firefox.

    Uses a copy of your Firefox profile so LinkedIn sees your real session
    (full cookies, fingerprint matches what they expect from your account).

    Conservative: 30/day, 5/batch, 90+s gap between applies. Auto-aborts on
    LinkedIn warning pages (CAPTCHA / verify / restricted).
    """
    from app.infra.db import init_db
    from app.modules.automation import run_batch

    async def _run():
        await init_db()  # composition root applies migrations
        kws = [k.strip() for k in keywords.split(",") if k.strip()]
        result = await run_batch(kws, limit=limit, headless=headless, profile_phone=phone)
        console.print(f"\n[green]Done.[/] {result}")

    asyncio.run(_run())


@app.command("easy-apply")
def easy_apply_cmd(
    keywords: str = typer.Option("rust senior remote", help="LinkedIn search keywords"),
    limit: int = typer.Option(5, help="Max applies this batch (hard cap MAX_APPLIES_PER_BATCH=5)"),
    headless: bool = typer.Option(False, help="Run browser headless (False = you watch the bot)"),
    phone: str = typer.Option("", help="Phone number to fill in form (empty = leave blank, may fail required-field check)"),
) -> None:
    """Phase 2: LinkedIn Easy Apply auto-applier.

    Goes to LinkedIn jobs search with f_AL=true (Easy Apply only), past-week,
    remote, your keywords. For each unapplied job:
      - Click Easy Apply
      - Fill phone if asked
      - Click Continue/Review/Submit through up to 3 modal pages
      - Skip if too many custom questions or red error fields appear

    Hard guardrails (cannot bypass):
      30/day, 5/batch, 90+sec gap between applies.

    Stops batch immediately on CAPTCHA / verify / restricted-account warning.

    Selectors copied from wodsuz/EasyApplyJobsBot (battle-tested aria-label-based).
    """
    from app.modules.automation import run_easy_apply_batch
    from app.infra.db import init_db

    async def _run():
        await init_db()
        stats = await run_easy_apply_batch(
            keywords=keywords, limit=limit, headless=headless, profile_phone=phone,
        )
        console.print(f"\n[green]Done.[/] Stats: {stats}")

    asyncio.run(_run())


@app.command("send-outreach")
def send_outreach_cmd(
    limit: int = typer.Option(5, help="Max applies to send this batch (hard cap MAX_SEND_PER_BATCH=5)"),
    dry_run: bool = typer.Option(True, help="Just print what WOULD be sent, no browser"),
    headless: bool = typer.Option(True, help="Run browser headless (no visible window)"),
) -> None:
    """Phase 1: send DM outreach automatically via Camoufox + LinkedIn.

    Pulls applies WHERE flank='dm_outreach' AND sent_at IS NULL AND status IN ('generated', 'queued').
    For each, opens the DM's LinkedIn profile and sends Message (1st-degree)
    or Connect+note (2nd/3rd degree).

    Hard guardrails (cannot bypass):
      - 30/day cap
      - 5/batch cap
      - 2-3 minutes between sends

    Stops batch immediately if LinkedIn shows verification/CAPTCHA.

    First-time setup:
      1. Make sure Firefox has a logged-in LinkedIn session (we read cookies)
      2. Run with --no-headless once to confirm Camoufox lands on /feed/
    """
    from app.modules.automation import run_send_batch
    from app.infra.db import init_db

    async def _run():
        await init_db()
        stats = await run_send_batch(limit=limit, dry_run=dry_run, headless=headless)
        console.print(f"\n[green]Done.[/] Stats: {stats}")

    asyncio.run(_run())


@app.command("reset-db")
def reset_db() -> None:
    """Drop all tables and recreate (WIPES DATA, only for SQLite)."""
    import os
    from app.infra.db import database_url as _get_database_url
    from app.infra.db import init_db
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
