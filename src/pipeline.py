"""Full pipeline: scrape -> enrich contacts -> score -> generate messages -> CSV."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from rich.console import Console
from rich.table import Table

from src.companies.models import Company
from src.companies.ports import CompanySource
from src.leads.models import Lead
from src.leads.scorer import LeadScorer
from src.outreach.models import OutreachChannel
from src.outreach.ports import LLMGenerator
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
    company: Company
    lead: Lead | None
    contact_name: str
    contact_role: str
    contact_email: str
    contact_linkedin: str
    contact_twitter: str
    message: str
    channel: str


async def run_pipeline(
    sources: list[CompanySource],
    dm_searches: list[DecisionMakerSearch],
    enrichments: list[ContactEnrichment],
    llm: LLMGenerator | None,
    criteria: SearchCriteria,
    profile: CandidateProfile,
    channel: OutreachChannel = OutreachChannel.LINKEDIN,
    output_csv: str = "leads_full.csv",
    campaign: str = "default",
) -> list[PipelineResult]:
    """Runs the full pipeline and returns results.

    dm_searches and enrichments are lists - tried in order until one returns data.
    """

    results: list[PipelineResult] = []

    # Step 1: Scrape companies from all sources
    console.print("\n[bold cyan]Step 1/4: Scraping companies...[/]")
    companies: list[Company] = []

    for source in sources:
        try:
            console.print(f"  Scraping [yellow]{source.source_name}[/]...")
            async for company in source.fetch_companies(criteria):
                companies.append(company)
        except Exception as e:
            console.print(f"  [red]Error in {source.source_name}:[/] {e}")

    console.print(f"  [green]Found {len(companies)} companies total[/]")

    if not companies:
        console.print("[red]No companies found. Check your criteria.[/]")
        return results

    # Step 2: Find decision makers - try each source in order
    console.print("\n[bold cyan]Step 2/4: Finding decision makers...[/]")
    if dm_searches:
        active_sources = ", ".join(s.source_name for s in dm_searches)
        console.print(f"  Sources: [yellow]{active_sources}[/]")

    scorer = LeadScorer(TechStack.from_strings(*profile.tech_stack))
    leads: list[Lead] = []

    for company in companies:
        dms_for_company: list = []

        # Try each source until we find any decision makers
        for dm_search in dm_searches:
            try:
                async for dm in dm_search.find(company, DEFAULT_ROLES, limit=3):
                    # Try to enrich with each enrichment source
                    for enr in enrichments:
                        try:
                            if company.website:
                                domain = company.website.replace("https://", "").replace("http://", "").split("/")[0]
                            else:
                                domain = company.name.lower().replace(" ", "") + ".com"
                            dm = await enr.enrich(dm, domain)
                        except Exception as e:
                            logger.debug("Enrichment {} failed: {}", enr.source_name, e)
                    dms_for_company.append(dm)
            except Exception as e:
                logger.warning("DM search {} failed for {}: {}", dm_search.source_name, company.name, e)

            if dms_for_company:
                break  # found contacts, no need to try other sources

        # If nothing found, add empty lead
        if not dms_for_company:
            dms_for_company = [_empty_dm(company)]

        for dm in dms_for_company:
            score = scorer.score(company, dm)
            leads.append(Lead(company=company, decision_maker=dm, relevance_score=score))

    console.print(f"  [green]Built {len(leads)} leads[/]")

    # Step 3: Generate outreach messages (if LLM available)
    console.print("\n[bold cyan]Step 3/4: Generating messages...[/]")

    for lead in leads:
        message_text = ""
        if llm:
            try:
                msg = await llm.generate_outreach(lead, channel, profile.summary)
                message_text = msg.body
            except Exception as e:
                logger.warning("LLM failed for {}: {}", lead.company.name, e)

        dm = lead.decision_maker
        results.append(PipelineResult(
            company=lead.company,
            lead=lead,
            contact_name=dm.full_name,
            contact_role=dm.title_raw or dm.role.value,
            contact_email=str(dm.email) if dm.email else "",
            contact_linkedin=str(dm.linkedin_url) if dm.linkedin_url else "",
            contact_twitter=dm.twitter_handle or "",
            message=message_text,
            channel=channel.value,
        ))

    console.print(f"  [green]Generated {sum(1 for r in results if r.message)} messages[/]")

    # Step 3.5: Save to DB (deduplication + history)
    console.print("\n[bold cyan]Saving to DB...[/]")
    try:
        from src.leads.db import init_db
        from src.leads.repo import SqliteLeadRepository
        await init_db()
        repo = SqliteLeadRepository(campaign=campaign)
        real_leads = [l for l in leads if l.decision_maker.full_name != "Unknown (find manually)"]
        await repo.save_many(real_leads)
        total_in_db = await repo.count()
        console.print(f"  [green]DB now has {total_in_db} total leads (campaign='{campaign}')[/]")
    except Exception as e:
        console.print(f"  [red]DB save failed: {e}[/]")

    # Step 4: Export to CSV
    console.print(f"\n[bold cyan]Step 4/4: Exporting to {output_csv}...[/]")
    _export_csv(results, output_csv)

    # Print summary table
    _print_summary(results)

    return results


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
            "contact_name", "contact_role", "contact_email", "contact_linkedin", "contact_twitter",
            "relevance_score", "channel", "message",
        ])
        for r in results:
            company = r.company
            writer.writerow([
                company.name,
                company.location or "Remote",
                company.source or "",
                company.source_url or "",
                ", ".join(sorted(company.tech_stack.technologies)[:6]),
                r.contact_name,
                r.contact_role,
                r.contact_email,
                r.contact_linkedin,
                r.contact_twitter,
                r.lead.relevance_score if r.lead else 0,
                r.channel,
                r.message.replace("\n", " "),
            ])

    console.print(f"  [green]Saved {len(results)} rows to {path.resolve()}[/]")


def _print_summary(results: list[PipelineResult]) -> None:
    table = Table(title="Pipeline Results")
    table.add_column("#", style="dim", width=4)
    table.add_column("Company", style="cyan", min_width=20)
    table.add_column("Contact", min_width=20)
    table.add_column("Role", min_width=10)
    table.add_column("Email", style="green")
    table.add_column("Message", style="dim", max_width=40)

    for i, r in enumerate(results[:30], 1):
        msg_preview = r.message[:40] + "..." if len(r.message) > 40 else r.message
        table.add_row(
            str(i),
            r.company.name,
            r.contact_name,
            r.contact_role,
            r.contact_email or "-",
            msg_preview or "[no message]",
        )

    console.print(table)
    if len(results) > 30:
        console.print(f"  ... and {len(results) - 30} more in CSV")
