# Job Hunter Parser

Multi-channel job hunting automation built with **hexagonal architecture**.

Scrapes job boards (Y Combinator, web3.career, RustJobs, Wellfound, RemoteOK), finds decision makers at target companies, generates personalized outreach messages via LLM, and exports leads ready for manual sending across LinkedIn, Email, Twitter, Telegram, Discord.

## Why

Mass "Easy Apply" on LinkedIn has <0.5% conversion. Personalized direct outreach to decision makers (CTO, Head of Engineering, Founders) has 5-15% conversion. This tool scales that approach: parse once, send manually with full personalization.

## Architecture

Clean hexagonal (ports & adapters) architecture:

```
src/
├── domain/              # Pure business logic, no external deps
│   ├── entities/        # Company, DecisionMaker, Lead, OutreachMessage
│   ├── value_objects/   # Email, LinkedInUrl, TechStack
│   └── services/        # Domain services (LeadEnricher, MessagePersonalizer)
│
├── application/         # Use cases + ports (interfaces)
│   ├── ports/
│   │   ├── inbound/     # Driver ports (what clients can do)
│   │   └── outbound/    # Driven ports (what we need from outside)
│   └── use_cases/       # Orchestrates domain logic
│
├── infrastructure/      # Adapters (concrete implementations)
│   ├── scrapers/        # YC, web3, RustJobs, Wellfound, RemoteOK
│   ├── enrichment/      # Apollo, Hunter
│   ├── llm/             # Claude, OpenAI
│   ├── persistence/     # PostgreSQL via SQLAlchemy
│   └── di/              # Dishka container
│
└── presentation/        # Entry points
    ├── cli/             # Typer CLI
    └── api/             # FastAPI (optional)
```

## Pipeline

```
1. SCRAPE    → parse companies from job boards (YC, web3, Rust, Wellfound, RemoteOK)
2. ENRICH    → find decision makers + contacts via Apollo/Hunter/LinkedIn
3. GENERATE  → LLM creates personalized message per (company, person) pair
4. EXPORT    → dump to Google Sheets / CSV for manual sending
5. TRACK     → log replies and convert funnel metrics
```

## Tech Stack

- **Python 3.12**
- **Pydantic v2** — validation and settings
- **Dishka** — dependency injection
- **SQLAlchemy 2.0 async** + **asyncpg** — persistence
- **Alembic** — migrations
- **Typer** — CLI
- **Playwright** + **BeautifulSoup** — scraping
- **Anthropic SDK** — LLM
- **Loguru** — logging
- **Pytest** — tests
- **Ruff** + **Mypy** — linting and type checking
- **Docker Compose** — local PostgreSQL

## Quick Start

```bash
# Clone
git clone https://github.com/BEPOHUKA-U3-KA3AXCTAHA/job-hunter-parser.git
cd job-hunter-parser

# Setup venv
python -m venv .venv
source .venv/bin/activate

# Install
pip install -e ".[dev]"
playwright install chromium

# Environment
cp .env.example .env
# fill in ANTHROPIC_API_KEY, APOLLO_API_KEY, etc.

# Start PostgreSQL
docker-compose up -d

# Run migrations
alembic upgrade head

# Use CLI
jhp scrape yc --limit 100
jhp enrich --source apollo
jhp generate-messages
jhp export --format csv
```

## Roadmap

- [x] Repo setup and hexagonal structure
- [ ] Domain entities and ports
- [ ] YC scraper adapter
- [ ] Apollo enrichment adapter
- [ ] Claude LLM adapter
- [ ] PostgreSQL repository
- [ ] CLI commands
- [ ] Google Sheets export
- [ ] web3.career + RustJobs + Wellfound + RemoteOK scrapers
- [ ] Outreach tracking and funnel metrics
- [ ] Tests

## License

MIT
