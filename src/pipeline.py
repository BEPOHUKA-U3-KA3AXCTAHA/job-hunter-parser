"""Full pipeline: scrape -> enrich contacts -> score -> generate messages -> CSV."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loguru import logger
from rich.console import Console
from rich.table import Table

from src.companies.models import Company, JobPosting
from src.companies.ports import CompanySource
from src.messages.models import Message, MessageChannel, MessageStatus
from src.messages.ports import LLMGenerator, ApplyRepository
from src.messages.scorer import RelevanceScorer
from src.people.models import DecisionMakerRole
from src.people.ports import ContactEnrichment, DecisionMakerSearch
from src.shared import CandidateProfile, SearchCriteria, TechStack

console = Console()

DEFAULT_ROLES = [
    DecisionMakerRole.FOUNDER,
    DecisionMakerRole.CEO,
    DecisionMakerRole.CTO,
    DecisionMakerRole.HEAD_OF_ENGINEERING,
    DecisionMakerRole.ENGINEERING_MANAGER,
]


@dataclass
class PipelineResult:
    message: Message
    contact_name: str
    contact_role: str
    contact_email: str
    contact_email_guess: str
    contact_linkedin: str
    contact_twitter: str
    contact_github: str
    body: str
    channel: str


async def run_pipeline(
    sources: list[CompanySource],
    dm_searches: list[DecisionMakerSearch],
    enrichments: list[ContactEnrichment],
    llm: LLMGenerator | None,
    criteria: SearchCriteria,
    profile: CandidateProfile,
    channel: MessageChannel = MessageChannel.LINKEDIN,
    output_csv: str = "messages_full.csv",
    skip_fresh_days: int = 30,
    repo: ApplyRepository | None = None,
) -> list[PipelineResult]:
    """Runs the full pipeline.

    skip_fresh_days: if contacts for a company were verified within N days,
    skip re-enrichment (saves API calls). Set to 0 to always refresh.
    """
    results: list[PipelineResult] = []

    # Step 1: Scrape companies + job postings from all sources
    console.print("\n[bold cyan]Step 1/4: Scraping companies + jobs...[/]")
    companies: list[Company] = []
    job_postings: list[JobPosting] = []

    for source in sources:
        try:
            console.print(f"  Scraping [yellow]{source.source_name}[/]...")
            async for company in source.fetch_companies(criteria):
                companies.append(company)
            async for jp in source.fetch_job_postings(criteria):
                job_postings.append(jp)
        except Exception as e:
            console.print(f"  [red]Error in {source.source_name}:[/] {e}")

    console.print(f"  [green]Found {len(companies)} companies, {len(job_postings)} jobs[/]")

    if not companies:
        console.print("[red]No companies found. Check your criteria.[/]")
        return results

    # Step 2: Find decision makers (cache-aware)
    console.print("\n[bold cyan]Step 2/4: Finding decision makers...[/]")
    if dm_searches:
        active = ", ".join(s.source_name for s in dm_searches)
        console.print(f"  Sources: [yellow]{active}[/], skip-fresh: [yellow]{skip_fresh_days}d[/]")

    scorer = RelevanceScorer(TechStack.from_strings(*profile.tech_stack))
    messages: list[Message] = []
    cache_hits = 0
    cache_misses = 0
    dm_scanned_company_names: set[str] = set()  # to mark in DB after companies are persisted

    for company in companies:
        dms_for_company: list = []

        # Check cache first
        if repo and skip_fresh_days > 0:
            cached = await repo.get_fresh_contacts(company.name, skip_fresh_days)
            if cached:
                dms_for_company = cached
                cache_hits += 1
                logger.debug("Cache hit for {}: {} contacts", company.name, len(cached))

        # Cache miss — fetch from sources, then mark company.last_dm_scan_at
        if not dms_for_company:
            cache_misses += 1
            for dm_search in dm_searches:
                try:
                    async for dm in dm_search.find(company, DEFAULT_ROLES, limit=3):
                        dms_for_company.append(dm)
                except Exception as e:
                    logger.warning("DM search {} failed for {}: {}", dm_search.source_name, company.name, e)

                if dms_for_company:
                    break

            # Remember to mark this company as scanned after companies are persisted to DB
            dm_scanned_company_names.add(company.name)

        # Run enrichers on every dm — both freshly fetched and cache-loaded.
        # Pattern-based enrichers (e.g. email guesser) are idempotent; API enrichers
        # should themselves no-op when a dm already has the relevant contact channel.
        domain = _company_domain(company)
        for dm in dms_for_company:
            for enr in enrichments:
                try:
                    dm = await enr.enrich(dm, domain)
                except Exception as e:
                    logger.debug("Enrichment {} failed: {}", enr.source_name, e)

        if not dms_for_company:
            dms_for_company = [_empty_dm(company)]

        for dm in dms_for_company:
            score = scorer.score(company, dm)
            messages.append(Message(
                decision_maker=dm,
                company=company,
                relevance_score=score,
                channel=channel,
            ))

    console.print(f"  [green]Built {len(messages)} messages[/] (cache hits: {cache_hits}, misses: {cache_misses})")

    # Step 3: Generate message bodies via LLM
    console.print("\n[bold cyan]Step 3/4: Generating message bodies...[/]")

    generated_count = 0
    for msg in messages:
        if llm and msg.decision_maker.full_name != "Unknown (find manually)":
            try:
                body = await llm.generate_body(msg, profile.summary)
                if body:
                    msg.body = body
                    msg.generated_at = datetime.utcnow()
                    msg.advance_status(MessageStatus.GENERATED)
                    generated_count += 1
            except Exception as e:
                logger.warning("LLM failed for {}: {}", msg.company.name, e)

    console.print(f"  [green]Generated {generated_count} message bodies[/]")

    # Save to DB
    if repo:
        console.print("\n[bold cyan]Saving to DB...[/]")
        try:
            # Only persist messages whose body has been generated.
            # Bodyless messages = just an enrichment record on the contact, kept in CSV but not in DB.
            real_messages = [
                m for m in messages
                if m.decision_maker.full_name != "Unknown (find manually)" and m.body
            ]
            await repo.save_many(real_messages)

            # Persist company+contact rows for messages that had no body generated
            # (no LLM key, or LLM call failed). save_many already covered the bodied ones.
            # Without this, job postings would have no company FK target on cold runs.
            bodyless = [
                m for m in messages
                if m.decision_maker.full_name != "Unknown (find manually)" and not m.body
            ]
            from src.messages.db import get_session_maker
            if bodyless:
                from src.messages.repo import _upsert_company, _upsert_decision_maker
                Session = get_session_maker()
                async with Session() as session:
                    for m in bodyless:
                        comp_row = await _upsert_company(session, m.company)
                        await _upsert_decision_maker(session, comp_row, m.decision_maker)
                    await session.commit()

            # Persist job postings, linked back to companies by name
            from src.messages.db import CompanyRow
            from sqlalchemy import select
            async with Session() as session:
                names = {c.name for c in companies}
                result = await session.execute(select(CompanyRow).where(CompanyRow.name.in_(names)))
                name_to_id = {r.name: r.id for r in result.scalars()}
            new_jobs = await repo.save_job_postings(job_postings, name_to_id)

            # Mark companies whose DM scan happened this run, so freshness cache works next run
            for n in dm_scanned_company_names:
                try:
                    await repo.mark_dm_scan_done(n)
                except Exception as e:
                    logger.debug("mark_dm_scan_done failed for {}: {}", n, e)

            total_messages = await repo.count()
            console.print(
                f"  [green]DB: {total_messages} messages, +{new_jobs} new jobs, marked {len(dm_scanned_company_names)} companies as dm-scanned[/]"
            )
        except Exception as e:
            console.print(f"  [red]DB save failed: {e}[/]")

    # Build results for CSV
    for msg in messages:
        dm = msg.decision_maker
        results.append(PipelineResult(
            message=msg,
            contact_name=dm.full_name,
            contact_role=dm.title_raw or dm.role.value,
            contact_email=str(dm.email) if dm.email else "",
            contact_email_guess=dm.contacts.get("email_guess", ""),
            contact_linkedin=str(dm.linkedin_url) if dm.linkedin_url else "",
            contact_twitter=dm.twitter_handle or "",
            contact_github=dm.github_handle or "",
            body=msg.body,
            channel=channel.value,
        ))

    # Step 4: Export to CSV
    console.print(f"\n[bold cyan]Step 4/4: Exporting to {output_csv}...[/]")
    _export_csv(results, output_csv)

    _print_summary(results)

    return results


def _company_domain(company: Company) -> str:
    if company.website:
        d = company.website.replace("https://", "").replace("http://", "").replace("www.", "")
        return d.split("/")[0]
    return company.name.lower().replace(" ", "") + ".com"


def _empty_dm(company: Company):
    from src.people.models import DecisionMaker
    return DecisionMaker(
        full_name="Unknown (find manually)",
        role=DecisionMakerRole.OTHER,
        company_id=company.id,
    )


def _export_csv(results: list[PipelineResult], filepath: str) -> None:
    path = Path(filepath)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "company", "location", "source", "source_url", "tech_stack",
            "contact_name", "contact_role",
            "contact_email", "contact_email_guess",
            "contact_linkedin", "contact_twitter", "contact_github",
            "relevance_score", "channel", "body",
        ])
        for r in results:
            c = r.message.company
            writer.writerow([
                c.name,
                c.location or "Remote",
                c.source or "",
                c.source_url or "",
                ", ".join(sorted(c.tech_stack.technologies)[:6]),
                r.contact_name,
                r.contact_role,
                r.contact_email,
                r.contact_email_guess,
                r.contact_linkedin,
                r.contact_twitter,
                r.contact_github,
                r.message.relevance_score,
                r.channel,
                r.body.replace("\n", " "),
            ])

    console.print(f"  [green]Saved {len(results)} rows to {path.resolve()}[/]")


def _print_summary(results: list[PipelineResult]) -> None:
    table = Table(title="Pipeline Results")
    table.add_column("#", style="dim", width=4)
    table.add_column("Company", style="cyan", min_width=20)
    table.add_column("Contact", min_width=20)
    table.add_column("Role", min_width=10)
    table.add_column("Email", style="green")
    table.add_column("Body", style="dim", max_width=40)

    for i, r in enumerate(results[:30], 1):
        preview = r.body[:40] + "..." if len(r.body) > 40 else r.body
        table.add_row(
            str(i),
            r.message.company.name,
            r.contact_name,
            r.contact_role,
            r.contact_email or "-",
            preview or "[empty]",
        )

    console.print(table)
    if len(results) > 30:
        console.print(f"  ... and {len(results) - 30} more in CSV")
