# Job Hunter Parser

Multi-channel job hunting automation: scrape job boards, find decision makers, generate personalized outreach via LLM, export for manual sending across LinkedIn, Email, Twitter, Telegram, Discord.

Built with **hexagonal architecture** (ports & adapters) organized by feature module.

## Why

Mass "Easy Apply" on LinkedIn has <0.5% conversion. Personalized direct outreach to decision makers (CTO, Head of Engineering, Founders) has 5-15% conversion. This tool scales that approach: parse once, send manually with full personalization.

## Architecture

The project runs **two parallel flanks** against the job market:

- **Mass Apply** — automated LinkedIn Easy Apply via a Selenium-driven real Firefox profile. Conservative pacing (≤30/day, 90s gaps) to keep the account safe.
- **DM Outreach** — personalized LinkedIn DMs to decision makers (CTO, Head of Eng, Founders) with LLM-generated messages. Higher conversion, lower volume.

Each module is a feature slice with `models.py` (Pydantic data classes), `ports.py` (abstract interfaces), and adapters (concrete implementations). Modules communicate only through ports or `shared/`.

```text
src/
├── shared/                       # value objects (Email, LinkedInUrl, TechStack, Seniority, CandidateProfile)
│
├── companies/                    # companies + job postings
│   ├── models.py                 # Company, JobPosting
│   ├── ports.py                  # CompanySource
│   ├── job_enrich.py             # extract apply_email from posting descriptions
│   ├── email_extract.py          # regex + heuristic email finder
│   └── scrapers/                 # linkedin, remoteok, rustjobs, web3career
│
├── people/                       # decision makers
│   ├── models.py                 # DecisionMaker, DecisionMakerRole
│   ├── ports.py                  # DecisionMakerSearch, ContactEnrichment
│   └── adapters/                 # apollo, theorg, apify, email_guesser
│
├── messages/                     # apply attempts + outreach state (DB-backed)
│   ├── models.py                 # ApplyRow, MessageRow domain models
│   ├── db.py                     # SQLAlchemy schema + auto-migrations
│   ├── repo.py                   # CRUD over applies/companies/people
│   ├── scorer.py                 # relevance 0-100 for (job × decision_maker)
│   ├── curator.py                # rank candidate pairs for the day's batch
│   ├── llm_base.py               # BaseLLMAdapter (rate-limit + retry)
│   ├── llm.py                    # Claude / Anthropic
│   ├── llm_gemini.py             # Google Gemini fallback
│   └── llm_groq.py               # Groq (fast cheap model)
│
├── automation/                   # browser-driven actions
│   ├── selenium_bot.py           # Easy Apply flow: find <A>, click, walk Shadow-DOM modal
│   ├── selenium_orchestrator.py  # batch driver: profile-match filter, pacing, DB persist
│   ├── linkedin_outreach.py      # DM send via authenticated Selenium session
│   ├── send_orchestrator.py      # batch driver for DM outreach
│   ├── firefox_cookies.py        # locate user's real Firefox profile
│   ├── browser.py                # Camoufox session (legacy, soft-blocked by LinkedIn)
│   ├── api_server.py             # FastAPI bridge for browser-extension flows
│   └── llm_pool.py               # subprocess pool for parallel LLM calls
│
├── pipeline.py                   # one-shot orchestration: scrape → enrich → curate → generate
├── cli.py                        # Typer entry point — `jhp <command>`
└── config.py                     # Pydantic Settings (env-driven)
```

### Layering rules

- `models.py` depends only on `shared/`.
- `ports.py` is abstract classes only — no library imports.
- Adapters implement ports; they may pull any third-party library.
- `pipeline.py` and `automation/*orchestrator.py` orchestrate ports, never adapters directly.
- DB schema lives in `messages/db.py` with auto-migration on startup.

### Browser automation: why Selenium + real Firefox profile

LinkedIn aggressively detects automation. Earlier attempts with Camoufox (anti-detect Firefox) were soft-blocked on `/jobs/view/`. The current stack:

1. Copy the user's **real** Firefox profile to `/tmp/jhp_ff_profile` (cookies, history, fingerprint intact).
2. Strip Marionette/automation prefs from the copy.
3. Drive that profile via Selenium with explicit pacing (90s gaps, ≤5/batch, ≤30/day).
4. Walk **Shadow DOM** (`#interop-outlet`) — LinkedIn 2026 wraps the Easy Apply modal in a shadow root that `document.querySelector` cannot cross. See `JS_WALK_PROLOG` in `selenium_bot.py`.
5. Fail safely on additional questions (salary, years of experience) instead of guessing.

## Pipeline

```text
1. SCRAPE       → CompanySource yields jobs from LinkedIn / RemoteOK / RustJobs / web3.career
2. ENRICH       → DecisionMakerSearch finds CTOs/Heads-of-Eng (Apollo, TheOrg);
                  ContactEnrichment guesses + verifies emails
3. CURATE       → scorer rates each (job × dm) pair for fit; curator picks the day's batch
4. GENERATE     → LLM writes per-pair DM (Claude / Gemini / Groq with rate-limit pool)
5a. MASS APPLY  → selenium_orchestrator filters by profile, opens jobs, clicks Easy Apply,
                  navigates Shadow-DOM modal, persists ApplyRow with outcome
5b. DM OUTREACH → linkedin_outreach sends generated DMs through authenticated session
6. TRACK        → ApplyRow / MessageRow records status (sent / replied / failed),
                  enables funnel metrics and idempotent retries
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
