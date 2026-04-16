from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="jhp",
    help="Job Hunter Parser - multi-channel outreach automation",
    no_args_is_help=True,
)
console = Console()

SOURCES = {
    "remoteok": "src.companies.scrapers.remoteok:RemoteOKScraper",
    "web3": "src.companies.scrapers.web3career:Web3CareerScraper",
}


def _get_scraper(source: str):
    if source == "remoteok":
        from src.companies.scrapers.remoteok import RemoteOKScraper
        return RemoteOKScraper()
    elif source == "web3":
        from src.companies.scrapers.web3career import Web3CareerScraper
        return Web3CareerScraper(category="rust")
    else:
        console.print(f"[red]Unknown source:[/] {source}. Available: {', '.join(SOURCES)}")
        raise typer.Exit(1)


@app.command()
def version() -> None:
    """Print version."""
    console.print("[bold cyan]job-hunter-parser[/] v0.1.0")


@app.command()
def scrape(
    source: str = typer.Argument(..., help="remoteok | web3"),
    limit: int = typer.Option(50, help="Max companies to fetch"),
    tech: list[str] | None = typer.Option(None, help="Filter by tech, e.g. --tech python --tech rust"),
) -> None:
    """Scrape companies from a source."""

    async def _run():
        scraper = _get_scraper(source)

        table = Table(title=f"Companies from {source}")
        table.add_column("Company", style="cyan", min_width=20)
        table.add_column("Location", min_width=15)
        table.add_column("Tech Stack", min_width=30)
        table.add_column("URL", style="dim")

        count = 0
        async for company in scraper.fetch_companies(tech_stack_filter=tech, limit=limit):
            techs = ", ".join(sorted(company.tech_stack.technologies)[:6])
            table.add_row(
                company.name,
                company.location or "Remote",
                techs,
                company.source_url or "-",
            )
            count += 1

        console.print(table)
        console.print(f"\n[green]Total:[/] {count} companies")

    asyncio.run(_run())


@app.command()
def jobs(
    source: str = typer.Argument(..., help="remoteok | web3"),
    limit: int = typer.Option(20, help="Max job postings"),
    tech: list[str] | None = typer.Option(None, help="Filter by tech"),
) -> None:
    """List job postings from a source."""

    async def _run():
        scraper = _get_scraper(source)

        table = Table(title=f"Job Postings from {source}")
        table.add_column("Title", style="cyan", min_width=30)
        table.add_column("Seniority", min_width=8)
        table.add_column("Salary", style="green", min_width=12)
        table.add_column("Location", min_width=15)
        table.add_column("URL", style="dim")

        count = 0
        async for posting in scraper.fetch_job_postings(tech_stack_filter=tech, limit=limit):
            if posting.salary_min and posting.salary_max:
                salary = f"${posting.salary_min // 1000}k-${posting.salary_max // 1000}k"
            else:
                salary = "-"

            table.add_row(
                posting.title[:50],
                str(posting.seniority),
                salary,
                posting.location or "Remote",
                posting.source_url or "-",
            )
            count += 1

        console.print(table)
        console.print(f"\n[green]Total:[/] {count} postings")

    asyncio.run(_run())


@app.command()
def enrich() -> None:
    """Find decision makers and enrich with contacts."""
    console.print("[yellow]TODO:[/] enrich")


@app.command("generate-messages")
def generate_messages(
    channel: str = typer.Option("linkedin", help="linkedin | email | twitter"),
) -> None:
    """Generate personalized outreach messages via LLM."""
    console.print(f"[yellow]TODO:[/] generate messages for {channel}")


@app.command()
def export(
    output: str = typer.Option("leads.csv", help="Output file path"),
    fmt: str = typer.Option("csv", help="csv | sheets"),
) -> None:
    """Export leads ready for outreach."""
    console.print(f"[yellow]TODO:[/] export to {output} (format={fmt})")


if __name__ == "__main__":
    app()
