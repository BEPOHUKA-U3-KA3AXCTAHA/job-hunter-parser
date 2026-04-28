# Project structure

Map of what lives where. Updated as the codebase changes.

## Top-level layout

```
job-hunter-parser/
├── src/
│   ├── cli.py                       # Typer CLI: hunt, curate, jobs-list, contacts, stats, …
│   ├── config.py                    # Secrets (.env via Pydantic) + AppConfig (config.toml)
│   ├── pipeline.py                  # End-to-end hunt orchestrator (scrape → enrich → score → persist)
│   ├── companies/                   # Job postings + company sourcing
│   ├── people/                      # Decision makers + contact enrichment
│   ├── messages/                    # Outreach attempts ("applies" semantically)
│   └── shared/                      # Shared value objects (TechStack, Email, SearchCriteria, …)
├── docs/                            # Architecture + roadmap docs (this file lives here)
├── scripts/                         # One-off helpers (show_letter.sh, …)
├── config.toml                      # Non-sensitive app config
├── .env                             # Secrets (git-ignored)
├── jhp.db                           # SQLite database (git-ignored)
├── pyproject.toml                   # Project deps + entrypoint (`jhp = src.cli:app`)
└── README.md
```

## src/companies/ — job postings and companies

```
src/companies/
├── models.py                        # Company + JobPosting dataclasses
├── ports.py                         # CompanySource ABC (scrape contract)
├── email_extract.py                 # extract_apply_email(text, company) — pulls real apply@ from descriptions
├── job_enrich.py                    # On-demand fetch of detail pages to grab apply_email for curated set
└── scrapers/
    ├── linkedin.py                  # LinkedIn public job search (Easy Apply)
    ├── remoteok.py                  # RemoteOK API (JSON)
    ├── web3career.py                # web3.career HTML, multi-category (rust/python/backend/senior)
    └── rustjobs.py                  # rustjobs.dev (Playwright, currently disabled — Vercel challenge)
```

**Data model:**
- `Company`: name, website, tech_stack, headcount, location, source, last_dm_scan_at
- `JobPosting`: title, company_name, description, tech_stack, seniority, salary, posted_at, applicants_count, **apply_email**

## src/people/ — decision makers and contact enrichment

```
src/people/
├── models.py                        # DecisionMaker dataclass with contacts JSON
├── ports.py                         # DecisionMakerSearch + ContactEnrichment ABCs
└── adapters/
    ├── theorg.py                    # TheOrg.com scraper — main DM source (LinkedIn URLs)
    ├── apollo.py                    # Apollo.io paid (free tier blocked, silent-disabled after first 403)
    ├── apify.py                     # Apify-based LinkedIn scraping (paid)
    └── email_guesser.py             # Pattern-based email guess: firstname.lastname@<domain>
```

**Data model:**
- `DecisionMaker`: full_name, role (enum), company_id, title_raw, **contacts JSON** (linkedin/email/email_guess/email_alts/twitter/github/telegram/website)

## src/messages/ — outreach attempts (a.k.a. "applies")

```
src/messages/
├── db.py                            # SQLAlchemy engine + ORM (CompanyRow, DecisionMakerRow, JobPostingRow, ApplyRow)
├── models.py                        # Apply + ApplyStatus + ApplyChannel domain dataclasses
├── ports.py                         # ApplyRepository + LLMGenerator ABCs
├── repo.py                          # SqliteApplyRepository — persistence
├── curator.py                       # filter_and_score: jobs × dms → ranked CuratedPair list
├── scorer.py                        # RelevanceScorer — quick tech-stack match score
├── llm_base.py                      # BaseLLMAdapter + shared SYSTEM_PROMPT + build_user_prompt
├── llm.py                           # ClaudeLLMAdapter (Anthropic API, default Haiku 4.5)
├── llm_gemini.py                    # GeminiLLMAdapter (free tier 1500/day)
└── llm_groq.py                      # GroqLLMAdapter (free tier Llama 3.3 70B)
```

**Data model — ApplyRow (table `applies`):**

| field | type | notes |
|---|---|---|
| id | UUID PK | |
| job_posting_id | UUID FK nullable | which posting we're applying for |
| decision_maker_id | UUID FK | who we're contacting (real person OR synthetic "Hiring Team") |
| attempt_no | int default=1 | bump for retries |
| flank | str enum | `mass_apply` or `dm_outreach` |
| method | str enum | `manual` / `auto_apply` / `auto_outreach` / `semi_auto` / `hand_written` |
| channel | str enum | `linkedin` / `linkedin_inmail` / `email` / `telegram` / `ats_workday` / `ats_greenhouse` / `ats_lever` / `ats_easy_apply` |
| status | str enum | `new` → `generated` → `queued` → `sent` → `seen` → `replied` → `interview_scheduled` → `interviewing` → `offer` / `accepted` / `rejected` / `no_reply` / `failed` |
| relevance_score | int | 0-100 from curator |
| subject | str nullable | email Subject: line |
| body | str nullable | DM message body or email body |
| cover_letter | str nullable | for ATS forms (separate from body for tracking) |
| form_responses | JSON nullable | `{field_name: answer}` for ATS form fields |
| apply_url | str nullable | exact URL we applied through |
| notes | str | freeform metadata |
| generated_at | datetime | when content was generated |
| sent_at | datetime nullable | when actually delivered to recipient |
| response_at | datetime nullable | when first response received |

**Unique key:** `(job_posting_id, decision_maker_id, attempt_no)`

## src/shared/

```
src/shared/
├── __init__.py                      # re-exports
├── candidate_profile.py             # CandidateProfile (Sergey's bio for LLM prompts)
├── search_criteria.py               # SearchCriteria — what jobs to look for + matches_*()
├── seniority.py                     # Seniority enum (junior/middle/senior/staff/lead)
├── tech_stack.py                    # TechStack frozen set
├── email.py                         # Email validated string
└── linkedin_url.py                  # LinkedInUrl validated string
```

## Database — high-level dataflow

```
[Scrapers]                                        [Enrichers]
   │                                                  │
   │ JobPosting                                       │ DecisionMaker
   ↓                                                  ↓
job_postings ──┐                              ┌── decision_makers
  (FK ──────►) │                              │ (FK ──►)
companies ─────┤                              │
               │       ┌──────────────────┐   │
               └──────►│ applies          │◄──┘
                       │ (job × dm pair)  │
                       └──────────────────┘
```

## Auto-migration in db.py

- `init_db()` runs on every CLI invocation
- `_drop_stale_unique_tables()` — drops tables in `SAFE_TO_WIPE = {"messages", "applies"}` if their unique constraints diverged from the model. Lets us evolve unique keys without manual migrations.
- `_sync_columns()` — uses `sqlalchemy.inspect` to ADD/DROP columns to match the model. Lets us evolve fields without losing data on stable tables.

## Roadmap (see docs/ROADMAP.md)

**Phase 0 — Foundation** ⏳ in progress
- 0.1 Schema rename `messages` → `applies` + new fields (cover_letter, form_responses, method, flank, sent_at, response_at, apply_url)
- 0.2 Update repo / curator / cli
- 0.3 Re-insert hand-written 26 letters into new schema
- 0.4 Bootstrap Camoufox + Firefox cookie importer
- 0.5 Claude CLI subprocess pool (5 workers)

**Phase 1 — DM Outreach automation** (Flank 2)
- LinkedIn module via Camoufox: open profile, detect Connect vs Message, fill note, send
- Daily limit guard (5-10 actions/day)
- Status tracking: sent_at, response detection

**Phase 2 — Mass Apply automation** (Flank 1)
- LinkedIn Easy Apply automator
- Workday / Greenhouse / Lever ATS form fillers
- Cover letter batch generation via Claude CLI pool
- Form response templates + LLM fallback for free-text fields

**Phase 3 — Source expansion + monitoring**
- HN Who is Hiring scraper (apply emails from posts)
- WeWorkRemotely scraper
- Daily report: actions taken, responses received

## Conventions

- **Hexagonal architecture:** ports (`*_ports.py`) define ABCs, adapters in `adapters/` or `scrapers/` implement them
- **Async everywhere** for I/O (httpx async, SQLAlchemy async)
- **No state in modules** — services use dependency injection
- **Loguru** for logging (no print)
- **Domain models in `models.py`** as `@dataclass(slots=True)`, ORM rows in `db.py` as SQLAlchemy `DeclarativeBase`
