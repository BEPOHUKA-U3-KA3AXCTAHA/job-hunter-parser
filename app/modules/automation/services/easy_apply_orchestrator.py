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
from urllib.parse import quote_plus

from loguru import logger

from app.modules.automation.adapters.camoufox import browser_session, human_sleep
from app.modules.automation.adapters.linkedin_easy_apply import (
    ApplyOutcome,
    ApplyResult,
    JOB_CARD,
    apply_to_job,
    is_blocked,
)

MAX_APPLIES_PER_DAY = 30
MAX_APPLIES_PER_BATCH = 5
MIN_GAP_S = 90


def _default_uow_factory():
    from app.modules.applies import default_uow
    return default_uow


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


async def _record_apply(
    uow_factory,
    company_name: str, job_title: str, job_url: str,
    result: ApplyResult,
) -> None:
    """Save the apply attempt via the MassApplyJournal port."""
    success = result.outcome == ApplyOutcome.APPLIED
    notes = (
        f"easy_apply: outcome={result.outcome.value} "
        f"pages={result.pages_traversed} {result.detail}"
    )
    async with uow_factory() as _uow:

        await _uow.mass_apply.upsert_mass_apply(
        company_name=company_name, job_title=job_title, job_url=job_url,
        channel="ats_easy_apply", success=success, notes=notes,
    )


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
    uow_factory=None,
) -> dict:
    """Main entry. Search → filter Easy Apply → apply with conservative pacing.

    `repo` is the MassApplyJournal port; defaults to SQLA-backed impl.
    """
    limit = min(limit, MAX_APPLIES_PER_BATCH)
    uow_factory = uow_factory or _default_uow_factory()
    async with uow_factory() as uow:

        today = await uow.mass_apply.count_applies_today("mass_apply")
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
            await _record_apply(repo, comp, title, job_url, result)

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
