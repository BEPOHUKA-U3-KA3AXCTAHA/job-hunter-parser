# Roadmap

Two-flank automation: Mass Apply (volume) + DM Outreach (precision).

## Architecture decisions (locked)

- **Browser:** Camoufox (anti-detect Firefox, MIT, free) — driven via Playwright Python API
- **Cookies:** import LinkedIn cookies from user's real Firefox `cookies.sqlite` once on first launch
- **LLM:** Claude CLI subprocess pool (5 workers concurrent), using user's Claude Max subscription
  - Falls back to Anthropic API only if subprocess pool can't keep up
- **Persistence:** SQLite (jhp.db), single source of truth for jobs / dms / applies
- **No cloud, no proxies** — runs entirely on user's machine, uses user's IP and Camoufox profile

## Phases

### Phase 0 — Foundation ⏳
0.1 Schema rename `messages` → `applies` + new fields
0.2 Update repo / curator / cli to use ApplyRow
0.3 Re-insert 26 hand-written letters with method=hand_written, flank=dm_outreach
0.4 Bootstrap Camoufox: install + Firefox cookie importer + first-run profile setup
0.5 Claude CLI subprocess pool with concurrency=5

### Phase 1 — DM Outreach automation (Flank 2)
- `src/automation/browser.py` — Camoufox session manager
- `src/automation/linkedin_outreach.py` — open profile, detect Connect/Message, fill note, send
- Daily limits: max 10 connect/day, max 30 messages/day, random pauses
- Behavioral mimicry: log-normal delays, mouse curves, occasional scroll
- Status tracking writes back to applies table (sent_at, status)
- CLI: `jhp send-outreach --limit 5 --dry-run`

### Phase 2 — Mass Apply automation (Flank 1)
- `src/automation/easy_apply_linkedin.py` — LinkedIn Easy Apply forms
- `src/automation/ats/` — per-ATS form fillers (Workday, Greenhouse, Lever, Ashby)
- `src/messages/cl_generator.py` — batch cover letter generation via Claude CLI pool
- Form-fill strategy: pre-defined answers from CandidateProfile + LLM fallback for free-text
- Daily limits: max 50 applies/day with Random spread across business hours
- CLI: `jhp send-applies --limit 20 --channels easy_apply,workday`

### Phase 3 — Source expansion + monitoring
- `src/companies/scrapers/hn_hiring.py` — HN Who is Hiring monthly thread scraper (real apply emails)
- `src/companies/scrapers/weworkremotely.py` — WeWorkRemotely scraper
- `src/people/adapters/social_scraper.py` — public bio enrichment (GitHub, personal site, Twitter via nitter)
- `src/cli.py daily-report` — shows what was sent yesterday, response rates per channel

## Anti-ban guardrails (apply to all phases)

- **Hard limits in code** (cannot be bypassed by CLI flags):
  - LinkedIn: max 10 connect requests/day, max 30 messages/day
  - Mass apply: max 50 applies/day across all platforms
- **Random pauses 1-30 minutes** between batches (not 100 actions back-to-back)
- **"Human breaks":** bot randomly idles for 1-2h during daytime
- **Behavioral mimicry on every action:**
  - Bezier mouse curves (not straight jumps)
  - Log-normal delays between clicks (not equal intervals)
  - Occasional scroll-up-and-back-down (humans re-read)
- **Detect rate-limit signals from LinkedIn** (CAPTCHA shown, "unusual activity" page) → immediately stop, alert user

## Cost (recurring monthly)

- Claude Max subscription: **$100/мес** (5x or 20x — user has Max)
- LinkedIn Premium Career: **$30/мес** (optional but recommended for unlimited connect notes + 5 InMail)
- Hunter.io for email verify: **$0** (50 free credits/мес is enough)
- Wellfound + Hired profiles: **$0**
- Camoufox + Playwright: **$0** (open source)
- VPS / proxies: **$0** (running locally on user's machine)
- **Total: $130/мес maximum**

## What this is NOT

- Not a full LinkedIn scraping framework (we're a job seeker tool, not a sales tool)
- Not a SaaS product (single-user, runs locally)
- Not aggressive automation (low daily limits, behavioral mimicry, fail-safe on detection)
- Not a "magic find me a job" button (still requires user to do interviews, negotiate, deliver)
