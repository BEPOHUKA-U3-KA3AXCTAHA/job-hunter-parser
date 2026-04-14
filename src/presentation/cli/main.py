from __future__ import annotations

import typer
from rich.console import Console

app = typer.Typer(
    name="jhp",
    help="Job Hunter Parser - multi-channel outreach automation",
    no_args_is_help=True,
)
console = Console()


@app.command()
def version() -> None:
    """Print version."""
    console.print("[bold cyan]job-hunter-parser[/] v0.1.0")


@app.command()
def scrape(
    source: str = typer.Argument(..., help="Source: yc | web3 | rustjobs | wellfound | remoteok"),
    limit: int = typer.Option(100, help="Max companies to fetch"),
    tech: list[str] | None = typer.Option(None, help="Tech stack filter, e.g. --tech python --tech rust"),
) -> None:
    """Scrape companies from a source."""
    console.print(f"[yellow]TODO:[/] scrape {source} (limit={limit}, tech={tech})")


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
    format: str = typer.Option("csv", help="csv | sheets"),
) -> None:
    """Export leads ready for outreach."""
    console.print(f"[yellow]TODO:[/] export to {output} (format={format})")


if __name__ == "__main__":
    app()
