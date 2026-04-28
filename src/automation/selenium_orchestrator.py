"""Orchestrate Selenium-driven Easy Apply at conservative pace.

Pulls candidate job URLs from LinkedIn jobs search (live), filters out the
ones LinkedIn-search lied about (not actually Easy Apply once we land on
the page), applies to up to N real Easy Apply jobs, persists results.

Hard guardrails (cannot bypass):
  MAX_APPLIES_PER_DAY     = 30
  MAX_APPLIES_PER_BATCH   = 5
  MIN_GAP_BETWEEN_APPLIES = 90 sec
  MAX_TRIES_PER_BATCH     = 25 (skip non-Easy-Apply, keep trying within budget)
  STOP_ON_BLOCKED         = always
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from random import randint
from urllib.parse import quote_plus

import httpx
from loguru import logger
from sqlalchemy import and_, select

from src.automation.selenium_bot import (
    ApplyOutcome,
    ApplyResult,
    apply_to_job,
    human_sleep,
    selenium_firefox,
)
from src.messages.db import (
    ApplyRow,
    CompanyRow,
    DecisionMakerRow,
    JobPostingRow,
    get_session_maker,
    init_db,
)

MAX_APPLIES_PER_DAY = 30
MAX_APPLIES_PER_BATCH = 5
MIN_GAP_S = 90
MAX_TRIES_PER_BATCH = 25


def _build_search_urls(keywords_list: list[str]) -> list[str]:
    """Build search URLs for each keyword set with f_AL=1 (Easy Apply) + last week + remote."""
    out = []
    base = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    for kw in keywords_list:
        q = quote_plus(kw)
        out.append(f"{base}?keywords={q}&f_AL=1&f_TPR=r604800&start=0")
    return out


# Profile-match: title must contain at least one of these AND none of the rejects
_TITLE_REQUIRED_ANY = [
    "rust", "python", "backend", "back-end", "back end",
    "engineer", "developer", "platform", "infrastructure", "sre",
    "software", "fullstack", "full stack", "full-stack",
]
_TITLE_REJECT = [
    "frontend", "front-end", "front end", "ui ", "ux ", "designer",
    "manager", "director", "vp ", "head of", "chief ",
    "marketing", "sales", "account ", "support", "recruit", "hr ",
    "intern", "junior", "graduate ",
    "data analyst", "data scientist",
    "ml engineer", "ai engineer", "machine learning",
    "java ", " java", "scala", "ruby ", "rails", "php ", "salesforce",
    ".net", "c#", "wordpress", "android", "ios ", "mobile",
    "qa ", "tester", "writer", "copywriter",
    "specialist", "associate", "consultant", "advisor",
    "compliance", "legal", "operations manager", "project manager",
]


def _matches_profile(title: str) -> bool:
    """Filter jobs to those that fit Sergey's profile (Python+Rust senior backend).
    Conservative: must match at least one require and none of the rejects."""
    t = (title or "").lower()
    if not any(must in t for must in _TITLE_REQUIRED_ANY):
        return False
    if any(bad in t for bad in _TITLE_REJECT):
        return False
    return True


def _fetch_candidates_via_driver(driver, keywords_list: list[str], want: int = 25) -> list[tuple[str, str, str]]:
    """Pull (url, title, company) from LinkedIn search inside the logged-in Selenium session.

    Filters:
      - has "Easy Apply" badge (so real Easy Apply, not external)
      - not already applied
      - title matches user's profile (Python/Rust/backend/etc, no frontend/manager/etc)
    """
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    rejected_profile = 0
    for kw in keywords_list:
        q = quote_plus(kw)
        search_url = (
            "https://www.linkedin.com/jobs/search/"
            f"?keywords={q}&f_AL=true&f_TPR=r604800&f_WT=2&sortBy=DD"
        )
        logger.info("loading search: {}", search_url)
        try:
            driver.get(search_url)
        except Exception as e:
            logger.warning("search nav failed: {}", e)
            continue
        time.sleep(5)
        for _ in range(3):
            driver.execute_script("window.scrollBy(0, 1000);")
            time.sleep(1.5)

        cards = driver.execute_script("""
            const out = [];
            const seen = new Set();
            const cards = document.querySelectorAll('li[data-occludable-job-id], li.jobs-search-results__list-item, div.job-card-container');
            for (const c of cards) {
                const hasEasyApply = /easy apply/i.test(c.innerText || '');
                if (!hasEasyApply) continue;
                const a = c.querySelector('a[href*="/jobs/view/"]');
                if (!a) continue;
                const href = a.href.split('?')[0];
                if (seen.has(href)) continue;
                seen.add(href);
                const titleEl = c.querySelector('a[href*="/jobs/view/"] strong, a[href*="/jobs/view/"] span[aria-hidden="true"], .job-card-list__title');
                const title = (titleEl ? titleEl.textContent : a.textContent).trim();
                const compEl = c.querySelector('.artdeco-entity-lockup__subtitle, .job-card-container__primary-description');
                const company = compEl ? compEl.textContent.trim() : 'Unknown';
                const applied = /\\bApplied\\b/.test(c.innerText);
                out.push({href, title, company, applied});
            }
            return out;
        """)
        logger.info("Found {} EA cards for {!r}", len(cards), kw)
        for c in cards:
            if c["href"] in seen or c["applied"]:
                continue
            if not _matches_profile(c["title"]):
                rejected_profile += 1
                continue
            seen.add(c["href"])
            out.append((c["href"], c["title"][:120], c["company"][:80]))
            if len(out) >= want:
                logger.info("Profile-matched: {}, rejected: {}", len(out), rejected_profile)
                return out
    logger.info("Profile-matched: {}, rejected by profile filter: {}", len(out), rejected_profile)
    return out


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


async def _persist(company: str, title: str, url: str, result: ApplyResult) -> None:
    Session = get_session_maker()
    async with Session() as session:
        # Company
        comp_q = await session.execute(select(CompanyRow).where(CompanyRow.name == company))
        comp = comp_q.scalar_one_or_none()
        if not comp:
            comp = CompanyRow(name=company, source="linkedin_easy_apply", is_hiring=True)
            session.add(comp)
            await session.flush()
        # Synthetic Hiring Team DM
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
                company_id=comp.id, full_name="Hiring Team", role="hr",
                contacts={"channel": "linkedin_easy_apply"},
            )
            session.add(dm)
            await session.flush()
        # Job posting
        jp_q = await session.execute(select(JobPostingRow).where(JobPostingRow.source_url == url))
        jp = jp_q.scalar_one_or_none()
        if not jp:
            jp = JobPostingRow(
                title=title, company_id=comp.id,
                source="linkedin_easy_apply", source_url=url, is_active=True,
            )
            session.add(jp)
            await session.flush()
        now = datetime.utcnow()
        # Upsert — if (job, dm, attempt=1) exists, update; otherwise insert
        existing_q = await session.execute(
            select(ApplyRow).where(
                and_(
                    ApplyRow.job_posting_id == jp.id,
                    ApplyRow.decision_maker_id == dm.id,
                    ApplyRow.attempt_no == 1,
                )
            )
        )
        existing = existing_q.scalar_one_or_none()
        success = result.outcome in (ApplyOutcome.APPLIED, ApplyOutcome.INTEREST_SIGNALED)
        new_notes = f"selenium: outcome={result.outcome.value} pages={result.pages} {result.detail[:300]}"
        if existing:
            existing.status = "sent" if success else "failed"
            existing.method = "auto_apply"
            existing.channel = "ats_easy_apply"
            existing.apply_url = url
            if success and existing.sent_at is None:
                existing.sent_at = now
            existing.notes = (existing.notes or "") + " | " + new_notes
        else:
            ap = ApplyRow(
                job_posting_id=jp.id, decision_maker_id=dm.id, attempt_no=1,
                flank="mass_apply", method="auto_apply", channel="ats_easy_apply",
                relevance_score=50,
                status="sent" if success else "failed",
                apply_url=url,
                sent_at=now if success else None,
                generated_at=now,
                notes=new_notes,
            )
            session.add(ap)
        try:
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.warning("persist failed: {}", e)


async def run_batch(
    keywords_list: list[str] | None = None,
    limit: int = 1,
    headless: bool = True,
    profile_phone: str = "",
) -> dict:
    """Main entry. Search, filter, apply with conservative pacing."""
    keywords_list = keywords_list or ["rust senior remote", "python backend remote senior"]
    limit = min(limit, MAX_APPLIES_PER_BATCH)

    await init_db()
    today = await _count_applies_today()
    if today >= MAX_APPLIES_PER_DAY:
        return {"daily_cap_reached": True, "today": today}
    headroom = MAX_APPLIES_PER_DAY - today
    effective = min(limit, headroom)

    stats = {"applied": 0, "interest": 0, "already": 0, "no_ea": 0, "too_many": 0, "blocked": 0, "failed": 0}
    last_apply_at = 0.0

    with selenium_firefox(headless=headless) as driver:
        candidates = _fetch_candidates_via_driver(driver, keywords_list, want=MAX_TRIES_PER_BATCH)
        if not candidates:
            logger.warning("no candidates with Easy Apply badge from search")
            return {"error": "no_candidates", "stats": stats}
        logger.info("Got {} Easy-Apply-marked candidates", len(candidates))
        applied = 0
        for i, (url, title, company) in enumerate(candidates, 1):
            if applied >= effective:
                break
            if i > MAX_TRIES_PER_BATCH:
                break
            logger.info("[{}/{} cands; {}/{} applied] {} @ {}",
                        i, len(candidates), applied, effective, title[:50], company[:30])

            # Pace
            since = time.monotonic() - last_apply_at
            if applied > 0 and since < MIN_GAP_S:
                wait = MIN_GAP_S - since + randint(0, 60)
                logger.info("pacing: sleep {:.0f}s", wait)
                time.sleep(wait)

            result = apply_to_job(driver, url, profile_phone)
            last_apply_at = time.monotonic()
            await _persist(company, title, url, result)

            if result.outcome == ApplyOutcome.APPLIED:
                stats["applied"] += 1
                applied += 1
            elif result.outcome == ApplyOutcome.INTEREST_SIGNALED:
                stats["interest"] += 1
                applied += 1  # counts toward batch limit
            elif result.outcome == ApplyOutcome.ALREADY_APPLIED:
                stats["already"] += 1
            elif result.outcome == ApplyOutcome.NO_EASY_APPLY:
                stats["no_ea"] += 1  # silently skip, try next candidate
                human_sleep(2, 5)
            elif result.outcome == ApplyOutcome.TOO_MANY_QUESTIONS:
                stats["too_many"] += 1
                human_sleep(2, 5)
            elif result.outcome == ApplyOutcome.BLOCKED:
                stats["blocked"] += 1
                logger.error("BLOCKED — stopping batch")
                break
            else:
                stats["failed"] += 1
                human_sleep(3, 6)

    logger.info("Batch done: {}", stats)
    return {"stats": stats, "today_total": today + stats["applied"]}
