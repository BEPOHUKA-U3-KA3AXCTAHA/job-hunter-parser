# Job Hunter Parser

Multi-channel job hunting automation built with **hexagonal architecture** organized by **bounded context** (package by feature, not by layer).

Scrapes job boards (Y Combinator, web3.career, RustJobs, Wellfound, RemoteOK), finds decision makers at target companies, generates personalized outreach messages via LLM, and exports leads ready for manual sending across LinkedIn, Email, Twitter, Telegram, Discord.

## Why package by feature?

Code belongs to the **module it describes**, not to the abstract layer it lives in. Everything about "companies" lives under `companies/`, everything about "leads" lives under `leads/`, etc. Each module is its own mini-hexagon (domain / application / infrastructure).

Benefits:
- Less jumping between folders when working on a feature
- Clear bounded contexts (easy to split into microservices later)
- Dependencies between modules are explicit (only via ports, never via transitive domain imports)
- New engineers onboard by reading one module at a time

## Why

Mass "Easy Apply" on LinkedIn has <0.5% conversion. Personalized direct outreach to decision makers (CTO, Head of Engineering, Founders) has 5-15% conversion. This tool scales that approach: parse once, send manually with full personalization.

## Architecture

```
src/
├── shared/
│   └── kernel/                      # shared value objects
│       ├── email.py
│       ├── linkedin_url.py
│       ├── tech_stack.py
│       └── seniority.py
│
├── companies/                       # MODULE: companies + job postings
│   ├── domain/
│   │   ├── company.py
│   │   └── job_posting.py
│   ├── application/ports/
│   │   └── company_source.py        # outbound port
│   └── infrastructure/scrapers/     # YC, web3.career, RustJobs, Wellfound
│
├── people/                          # MODULE: decision makers
│   ├── domain/
│   │   └── decision_maker.py
│   ├── application/ports/
│   │   ├── decision_maker_search.py
│   │   └── contact_enrichment.py
│   └── infrastructure/              # Apollo, Hunter, LinkedIn
│
├── leads/                           # MODULE: leads (Company + DecisionMaker aggregate)
│   ├── domain/
│   │   ├── lead.py
│   │   └── lead_scorer.py           # domain service
│   ├── application/ports/
│   │   └── lead_repository.py
│   └── infrastructure/              # PostgreSQL repo
│
├── outreach/                        # MODULE: outreach messages
│   ├── domain/
│   │   └── outreach_message.py
│   ├── application/ports/
│   │   ├── llm_generator.py
│   │   └── outreach_log.py
│   └── infrastructure/              # Claude, export
│
├── hunting/                         # MODULE: cross-module orchestration
│   └── application/
│       ├── ports/                   # JobHunterService (inbound facade)
│       └── use_cases/               # scrape_enrich_generate flow
│
├── presentation/cli/                # Typer CLI
└── di/                              # Dishka container
```

Each module follows the hexagonal architecture:
- **domain/** — pure business logic, no external dependencies
- **application/ports/** — interfaces (abstractions)
- **application/use_cases/** — orchestrate domain logic (in `hunting/` for cross-module flows)
- **infrastructure/** — concrete adapters implementing the ports

Modules never import domain entities from other modules directly except through well-defined ports or via `shared/kernel`.

## Pipeline

```
1. SCRAPE    → companies module parses job boards    → Company entities
2. ENRICH    → people module finds decision makers   → DecisionMaker entities
3. SCORE     → leads module pairs + scores           → Lead aggregates
4. GENERATE  → outreach module calls LLM per lead    → OutreachMessage
5. EXPORT    → leads exported to Google Sheets / CSV → you send manually
6. TRACK     → log replies and convert funnel
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
git clone https://github.com/BEPOHUKA-U3-KA3AXCTAHA/job-hunter-parser.git
cd job-hunter-parser

python -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"
playwright install chromium

cp .env.example .env
# fill in ANTHROPIC_API_KEY, APOLLO_API_KEY, etc.

docker-compose up -d
alembic upgrade head

jhp scrape yc --limit 100
jhp enrich --source apollo
jhp generate-messages
jhp export --format csv
```

## Roadmap

- [x] Repo setup and bounded-context module structure
- [x] Domain entities and ports across modules
- [ ] YC scraper adapter (`companies/infrastructure/scrapers/yc_scraper.py`)
- [ ] Apollo enrichment adapter (`people/infrastructure/apollo_adapter.py`)
- [ ] Claude LLM adapter (`outreach/infrastructure/claude_adapter.py`)
- [ ] PostgreSQL lead repository (`leads/infrastructure/lead_repository_pg.py`)
- [ ] `hunting` use cases wiring everything together
- [ ] Dishka DI container
- [ ] CLI commands
- [ ] Google Sheets export
- [ ] web3.career + RustJobs + Wellfound + RemoteOK scrapers
- [ ] Outreach tracking and funnel metrics
- [ ] Integration tests

## License

MIT
