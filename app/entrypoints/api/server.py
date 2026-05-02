"""Local HTTP API for the Firefox extension to consume.

Runs on http://localhost:8765 (browser-only access, no auth needed since
single-user single-machine).

Endpoints:
  GET  /next-job        → next pending Easy Apply candidate
  POST /apply-result    → record outcome of an apply attempt
  GET  /stats           → counters (applied today / pending)

Job source: LinkedIn jobs search live, NOT our DB. Reason: LinkedIn's
own search has fresher Easy Apply data than our scrapers, and the
extension navigates to LinkedIn anyway, so we just point it at the next
job ID we haven't tried.

How it works:
  1. On startup, we hit LinkedIn jobs search via httpx (with user's cookies
     if available) to get a list of candidate job IDs
  2. /next-job pops one off the queue
  3. Extension applies, posts result, gets next, repeat

Start:
    .venv/bin/python3 -m app.entrypoints.api.server
"""
from __future__ import annotations

import asyncio
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote_plus

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pydantic import BaseModel

from app.infra.db import get_session_maker, init_db
from app.infra.db.orm.applies import ApplyRow


@dataclass
class JobCandidate:
    job_id: str
    url: str
    title: str
    company: str


class _State:
    queue: deque[JobCandidate] = field(default_factory=deque)
    in_flight: dict[str, JobCandidate] = field(default_factory=dict)


state = _State()
state.queue = deque()
state.in_flight = {}


app = FastAPI(title="JHP Easy Apply API")

# Browser extension running on linkedin.com needs CORS to call us
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class ApplyResult(BaseModel):
    job_id: str
    outcome: str  # applied | already_applied | no_easy_apply | too_many_questions | blocked | failed
    detail: Optional[str] = ""
    pages: Optional[int] = None


@app.get("/healthz")
async def healthz():
    return {"ok": True, "queue_size": len(state.queue), "in_flight": len(state.in_flight)}


@app.get("/next-job")
async def next_job():
    """Pop next candidate. Returns 404-shape JSON when queue empty so the
    extension can stop the batch."""
    if not state.queue:
        await _refill_queue()
    if not state.queue:
        return {"empty": True}
    cand = state.queue.popleft()
    state.in_flight[cand.job_id] = cand
    logger.info("Next job: {} @ {}", cand.title[:50], cand.company[:30])
    return {
        "id": cand.job_id,
        "url": cand.url,
        "title": cand.title,
        "company": cand.company,
    }


@app.post("/apply-result")
async def apply_result(result: ApplyResult):
    cand = state.in_flight.pop(result.job_id, None)
    if not cand:
        logger.warning("Result for unknown job {}", result.job_id)
        return {"ok": False}
    await _persist_apply(cand, result)
    logger.info("Job {} → {} ({})", cand.job_id, result.outcome, result.detail or "")
    return {"ok": True}


@app.get("/stats")
async def stats():
    Session = get_session_maker()
    cutoff = datetime.utcnow() - timedelta(hours=24)
    async with Session() as session:
        from sqlalchemy import and_, select

        result = await session.execute(
            select(ApplyRow).where(
                and_(
                    ApplyRow.flank == "mass_apply",
                    ApplyRow.sent_at.is_not(None),
                    ApplyRow.sent_at >= cutoff,
                )
            )
        )
        applied_today = len(list(result.scalars()))
    return {
        "applied_today": applied_today,
        "queue_size": len(state.queue),
        "in_flight": len(state.in_flight),
    }


async def _refill_queue(keywords: str = "rust senior remote", limit: int = 10):
    """Hit LinkedIn jobs search guest endpoint to grab job IDs.

    Uses the public /jobs-guest/jobs/api/seeMoreJobPostings endpoint which
    returns HTML of job cards — no auth required for the listing itself.
    Filters: f_AL=1 (Easy Apply), f_TPR=r604800 (past week).
    """
    q = quote_plus(keywords)
    url = (
        "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
        f"keywords={q}&f_AL=1&f_TPR=r604800&start=0"
    )
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Firefox/135.0"}

    try:
        async with httpx.AsyncClient(headers=headers, timeout=20, follow_redirects=True) as c:
            r = await c.get(url)
        if r.status_code != 200:
            logger.warning("LinkedIn jobs search returned {}", r.status_code)
            return
        # Cards use <div class="base-card ..." data-entity-urn="urn:li:jobPosting:N">
        cards = re.split(r'<div class="base-card', r.text)[1:]
        for card in cards[:limit]:
            m_id = re.search(r'data-entity-urn="urn:li:jobPosting:(\d+)"', card)
            if not m_id:
                continue
            jid = m_id.group(1)
            m_title = re.search(r'<h3 class="base-search-card__title"[^>]*>\s*([^<]+?)\s*</h3>', card)
            m_comp = re.search(r'<h4 class="base-search-card__subtitle"[^>]*>\s*<a[^>]*>\s*([^<]+?)\s*</a>', card)
            title = (m_title.group(1).strip() if m_title else f"job-{jid}")[:120]
            company = (m_comp.group(1).strip() if m_comp else "Unknown")[:80]
            state.queue.append(JobCandidate(
                job_id=jid,
                url=f"https://www.linkedin.com/jobs/view/{jid}/",
                title=title,
                company=company,
            ))
        logger.info("Refilled queue: {} candidates from search", len(state.queue))
    except Exception as e:
        logger.exception("queue refill failed: {}", e)


async def _persist_apply(cand: JobCandidate, result: ApplyResult):
    """Save the apply attempt outcome to the applies table."""
    Session = get_session_maker()
    from sqlalchemy import and_, select

    from app.infra.db.orm.companies import CompanyRow, JobPostingRow
    from app.infra.db.orm.people import DecisionMakerRow

    async with Session() as session:
        # Company
        comp_q = await session.execute(
            select(CompanyRow).where(CompanyRow.name == cand.company)
        )
        comp = comp_q.scalar_one_or_none()
        if not comp:
            comp = CompanyRow(name=cand.company, source="linkedin_easy_apply", is_hiring=True)
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
                company_id=comp.id,
                full_name="Hiring Team",
                role="hr",
                contacts={"channel": "linkedin_easy_apply"},
            )
            session.add(dm)
            await session.flush()
        # Job posting
        jp_q = await session.execute(
            select(JobPostingRow).where(JobPostingRow.source_url == cand.url)
        )
        jp = jp_q.scalar_one_or_none()
        if not jp:
            jp = JobPostingRow(
                title=cand.title,
                company_id=comp.id,
                source="linkedin_easy_apply",
                source_url=cand.url,
                is_active=True,
            )
            session.add(jp)
            await session.flush()
        # Apply row
        now = datetime.utcnow()
        ap = ApplyRow(
            job_posting_id=jp.id,
            decision_maker_id=dm.id,
            attempt_no=1,
            flank="mass_apply",
            method="auto_apply",
            channel="ats_easy_apply",
            relevance_score=50,
            status="sent" if result.outcome == "applied" else "failed",
            apply_url=cand.url,
            sent_at=now if result.outcome == "applied" else None,
            generated_at=now,
            notes=f"firefox-ext: outcome={result.outcome} pages={result.pages or 0} {result.detail or ''}",
        )
        session.add(ap)
        try:
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.debug("apply already in DB: {}", e)


@app.on_event("startup")
async def _startup():
    await init_db()
    await _refill_queue()
    logger.info("API ready on http://localhost:8765 — queue: {}", len(state.queue))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.entrypoints.api.server:app", host="127.0.0.1", port=8765, log_level="info")
