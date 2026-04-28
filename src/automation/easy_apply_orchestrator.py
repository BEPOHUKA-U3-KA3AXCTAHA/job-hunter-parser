"""Orchestrate LinkedIn Easy Apply at conservative pace.

Goes straight to LinkedIn jobs search live (no DB dependency for job sourcing —
LinkedIn's own search has fresher Easy Apply data than our scraper). For each
job in the result page:
  - Skip if already applied
  - Skip if not Easy Apply
  - Skip if modal has too many custom questions
  - Otherwise: auto-fill phone + walk Submit
  - Persist outcome to applies table

Hard guardrails:
  MAX_APPLIES_PER_DAY     = 30
  MAX_APPLIES_PER_BATCH   = 5
  MIN_GAP_BETWEEN_APPLIES = 90 sec
  STOP_ON_BLOCKED         = always
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from urllib.parse import quote_plus

from loguru import logger
from sqlalchemy import and_, select

from src.automation.browser import browser_session, human_sleep
from src.automation.linkedin_easy_apply import (
    ApplyOutcome,
    ApplyResult,
    JOB_CARD,
    apply_to_job,
    is_blocked,
)
from src.messages.db import (
    ApplyRow,
    CompanyRow,
    DecisionMakerRow,
    JobPostingRow,
    get_session_maker,
)

MAX_APPLIES_PER_DAY = 30
MAX_APPLIES_PER_BATCH = 5
MIN_GAP_S = 90


def _build_search_url(keywords: str, remote_only: bool = True) -> str:
    """LinkedIn jobs search URL with Easy Apply filter (f_AL=true) + datePosted=past-week."""
    q = quote_plus(keywords)
    base = "https://www.linkedin.com/jobs/search/"
    params = [
        f"keywords={q}",
        "f_AL=true",                # Easy Apply only
        "f_TPR=r604800",            # last 7 days
        "sortBy=DD",                 # date posted descending
    ]
    if remote_only:
        params.append("f_WT=2")     # remote
    return base + "?" + "&".join(params)


async def _count_applies_today() -> int:
    Session = get_session_maker()
    cutoff = datetime.utcnow() - timedelta(hours=24)
    async with Session() as session:
        result = await session.execute(
            select(ApplyRow).where(
                and_(
                    ApplyRow.flank == "mass_apply",
                    ApplyRow.sent_at.is_not(None),
                    ApplyRow.sent_at >= cutoff,
                )
            )
        )
        return len(list(result.scalars()))


async def _get_or_create_hiring_team_dm(session, company_name: str) -> "DecisionMakerRow":
    """For mass_apply we don't have a real DM — use a synthetic 'Hiring Team' dm
    keyed to the company. This keeps the (job, dm) tuple uniform across flanks."""
    # Find or create the company
    comp_q = await session.execute(select(CompanyRow).where(CompanyRow.name == company_name))
    comp = comp_q.scalar_one_or_none()
    if not comp:
        comp = CompanyRow(name=company_name, source="linkedin_easy_apply", is_hiring=True)
        session.add(comp)
        await session.flush()
    # Find or create the synthetic hiring DM
    dm_q = await session.execute(
        select(DecisionMakerRow).where(
            and_(
                DecisionMakerRow.company_id == comp.id,
                DecisionMakerRow.full_name == "Hiring Team",
            )
        )
    )
    dm = dm_q.scalar_one_or_none()
    if not dm:
        dm = DecisionMakerRow(
            company_id=comp.id,
            full_name="Hiring Team",
            role="hr",
            contacts={"channel": "linkedin_easy_apply"},
        )
        session.add(dm)
        await session.flush()
    return dm, comp


async def _record_apply(
    company_name: str,
    job_title: str,
    job_url: str,
    result: ApplyResult,
) -> None:
    """Save the apply attempt to the applies table."""
    Session = get_session_maker()
    async with Session() as session:
        dm, comp = await _get_or_create_hiring_team_dm(session, company_name)
        # Find or create the job posting
        jp_q = await session.execute(
            select(JobPostingRow).where(JobPostingRow.source_url == job_url)
        )
        jp = jp_q.scalar_one_or_none()
        if not jp:
            jp = JobPostingRow(
                title=job_title,
                company_id=comp.id,
                source="linkedin_easy_apply",
                source_url=job_url,
                is_active=True,
            )
            session.add(jp)
            await session.flush()

        now = datetime.utcnow()
        ap = ApplyRow(
            job_posting_id=jp.id,
            decision_maker_id=dm.id,
            attempt_no=1,
            flank="mass_apply",
            method="auto_apply",
            channel="ats_easy_apply",
            relevance_score=50,
            status="sent" if result.outcome == ApplyOutcome.APPLIED else "failed",
            apply_url=job_url,
            sent_at=now if result.outcome == ApplyOutcome.APPLIED else None,
            generated_at=now,
            notes=f"easy_apply: outcome={result.outcome.value} pages={result.pages_traversed} {result.detail}",
        )
        session.add(ap)
        try:
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.debug("Apply already in DB (dupe) for {}: {}", job_url, e)


async def _collect_job_urls(page, search_url: str, want: int) -> list[tuple[str, str, str]]:
    """Visit search URL, collect (job_url, title, company) for first N Easy Apply jobs."""
    logger.info("Loading search results: {}", search_url)
    await page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
    await human_sleep(3, 5)

    if await is_blocked(page):
        logger.error("Search results page is blocked")
        return []

    # Scroll down a bit so more cards lazy-load
    await page.evaluate("window.scrollBy(0, 800)")
    await human_sleep(1, 2)
    await page.evaluate("window.scrollBy(0, 800)")
    await human_sleep(1, 2)

    cards = await page.evaluate("""() => {
        const out = [];
        const lis = document.querySelectorAll('li[data-occludable-job-id]');
        for (const li of lis) {
            const a = li.querySelector('a.job-card-container__link, a[href*="/jobs/view/"]');
            if (!a) continue;
            const href = a.href.split('?')[0];
            const titleEl = a.querySelector('strong, span[aria-hidden="true"]') || a;
            const title = titleEl.textContent.trim();
            const compEl = li.querySelector('.artdeco-entity-lockup__subtitle, .job-card-container__primary-description');
            const comp = compEl ? compEl.textContent.trim() : '';
            const applied = li.textContent.includes('Applied');
            out.push({href, title, comp, applied});
        }
        return out;
    }""")

    fresh = [c for c in cards if not c["applied"]]
    logger.info("Found {} cards, {} not yet applied", len(cards), len(fresh))
    return [(c["href"], c["title"], c["comp"]) for c in fresh[:want * 3]]  # over-fetch since some won't be Easy Apply


async def run_easy_apply_batch(
    keywords: str = "rust senior remote",
    limit: int = 5,
    headless: bool = False,
    profile_phone: str = "",
) -> dict:
    """Main entry. Search → filter Easy Apply → apply with conservative pacing."""
    limit = min(limit, MAX_APPLIES_PER_BATCH)
    today = await _count_applies_today()
    if today >= MAX_APPLIES_PER_DAY:
        logger.warning("Daily cap reached: {}/{}", today, MAX_APPLIES_PER_DAY)
        return {"daily_cap_reached": True, "today": today}

    headroom = MAX_APPLIES_PER_DAY - today
    effective_limit = min(limit, headroom)

    stats = {"applied": 0, "already": 0, "skipped_no_ea": 0, "skipped_questions": 0, "blocked": 0, "failed": 0}
    search_url = _build_search_url(keywords, remote_only=True)

    async with browser_session(headless=headless) as page:
        candidates = await _collect_job_urls(page, search_url, effective_limit)
        if not candidates:
            return {"pending": 0, "stats": stats}

        applied_count = 0
        for i, (job_url, title, comp) in enumerate(candidates, 1):
            if applied_count >= effective_limit:
                break
            logger.info("[try {}/{}] {} @ {}", applied_count + 1, effective_limit, title[:50], comp[:30])

            result = await apply_to_job(page, job_url, profile_phone)
            await _record_apply(comp, title, job_url, result)

            if result.outcome == ApplyOutcome.APPLIED:
                stats["applied"] += 1
                applied_count += 1
            elif result.outcome == ApplyOutcome.ALREADY_APPLIED:
                stats["already"] += 1
            elif result.outcome == ApplyOutcome.SKIPPED_NO_EASY_APPLY:
                stats["skipped_no_ea"] += 1
            elif result.outcome == ApplyOutcome.SKIPPED_TOO_MANY_QUESTIONS:
                stats["skipped_questions"] += 1
            elif result.outcome == ApplyOutcome.BLOCKED_RATE_LIMIT:
                stats["blocked"] += 1
                logger.error("BLOCKED — stopping batch immediately")
                break
            else:
                stats["failed"] += 1

            # Pause between apply attempts
            if applied_count < effective_limit and i < len(candidates):
                gap = MIN_GAP_S + random.randint(0, 60)
                logger.info("Sleeping {}s before next job", gap)
                await human_sleep(gap, gap + 30)

    logger.info("Batch done: {}", stats)
    return {"stats": stats, "today_total": today + stats["applied"]}
