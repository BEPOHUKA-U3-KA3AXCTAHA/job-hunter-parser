# Job Hunter Parser

Multi-channel job hunting automation: scrape job boards, find decision makers, generate personalized outreach via LLM, export for manual sending across LinkedIn, Email, Twitter, Telegram, Discord.

Built with **hexagonal architecture** (ports & adapters) organized by feature module.

## Why

Mass "Easy Apply" on LinkedIn has <0.5% conversion. Personalized direct outreach to decision makers (CTO, Head of Engineering, Founders) has 5-15% conversion. This tool scales that approach: parse once, send manually with full personalization.

## Structure

Each module is a feature slice. Inside: `models.py` (data), `ports.py` (interfaces), and adapters (concrete implementations).

```text
src/
├── shared.py              # common value objects: Email, LinkedInUrl, TechStack, Seniority
│
├── companies/             # companies + job postings
│   ├── models.py          # Company, JobPosting
│   ├── ports.py           # CompanySource
│   └── scrapers/          # YC, web3, rustjobs, wellfound, remoteok
│
├── people/                # decision makers
│   ├── models.py          # DecisionMaker, DecisionMakerRole
│   ├── ports.py           # DecisionMakerSearch, ContactEnrichment
│   └── adapters/          # Apollo, Hunter, LinkedIn
│
├── leads/                 # Company + DecisionMaker pair
│   ├── models.py          # Lead, LeadStatus
│   ├── scorer.py          # LeadScorer (relevance 0-100)
│   ├── ports.py           # LeadRepository
│   └── repo.py            # Postgres implementation
│
├── outreach/              # personalized messages
│   ├── models.py          # OutreachMessage, OutreachChannel
│   ├── ports.py           # LLMGenerator, OutreachLog
│   ├── llm.py             # Claude adapter
│   └── log.py             # Postgres log
│
├── pipeline.py            # orchestrates: scrape → enrich → score → generate
├── cli.py                 # Typer CLI entry point
├── config.py              # Pydantic Settings
└── di.py                  # Dishka container (wires adapters to ports)
```

Rules:

- Modules talk to each other only through **ports** (interfaces) or `shared.py`.
- `models.py` has no external dependencies beyond `shared.py`.
- `ports.py` has abstract classes only.
- Adapters implement ports and can use whatever libraries they need.
- `pipeline.py` orchestrates ports, never adapters directly.

## Pipeline

```text
1. SCRAPE    → CompanySource yields companies from job boards
2. ENRICH    → DecisionMakerSearch finds CTOs/CEOs; ContactEnrichment adds emails
3. SCORE     → LeadScorer rates each (company, person) pair
4. GENERATE  → LLMGenerator creates personalized message
5. EXPORT    → leads dumped to Google Sheets / CSV for manual sending
6. TRACK     → OutreachLog records sent/replied, computes funnel
```

## Tech Stack

- **Python 3.12**
- **Pydantic v2** — models and settings
- **Dishka** — dependency injection
- **SQLAlchemy 2.0 async** + **asyncpg** — persistence
- **Alembic** — migrations
- **Typer** — CLI
- **Playwright** + **BeautifulSoup** — scraping
- **Anthropic SDK** — LLM
- **Loguru** — logging
- **Pytest** — tests
- **Ruff** + **Mypy** — lint and types
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

jhp scrape yc --limit 100 --tech python --tech rust
jhp enrich
jhp generate-messages --channel linkedin
jhp export --output leads.csv
```

## Roadmap

- [x] Module-per-feature structure
- [x] Models, ports, scorer, pipeline skeleton
- [ ] YC scraper (`companies/scrapers/yc.py`)
- [ ] Apollo adapter (`people/adapters/apollo.py`)
- [ ] Claude LLM (`outreach/llm.py`)
- [ ] Postgres repo (`leads/repo.py`) + Alembic migrations
- [ ] Dishka container (`di.py`)
- [ ] CLI wired to pipeline
- [ ] Google Sheets export
- [ ] web3.career, RustJobs, Wellfound, RemoteOK scrapers
- [ ] Outreach tracking and funnel metrics

## License

MIT
