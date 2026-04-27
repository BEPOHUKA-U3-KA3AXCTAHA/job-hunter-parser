"""Curate (job_posting × decision_maker) pairs from the DB.

Loads everything fresh, applies hard filters (junk titles, stale posts),
scores each (job, dm) pair against the candidate profile, and returns the
top-N pairs ready for letter generation.

Hard filters here are defensive — `matches_title` already rejects most junk
at scrape time, but old DB rows or schema-evolved exclude lists can still
leak through.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import select

from src.companies.models import Company, JobPosting
from src.messages.db import (
    CompanyRow,
    DecisionMakerRow,
    JobPostingRow,
    get_session_maker,
)
from src.people.models import DecisionMaker, DecisionMakerRole
from src.shared import CandidateProfile, Seniority, TechStack

# Junk-keyword skip list — title-level. We only message backend/eng roles.
_TITLE_HARD_REJECT = {
    "intern", "internship", "junior", "graduate ", "trainee",
    "frontend", "front-end", "front end", "ui ", "ux ",
    "qa ", "qa engineer", "tester", "manual qa", "designer", "marketing",
    "sales", "account ", "account executive", "support", "customer", "recruit",
    "hr ", "human resources", "social media", "community", "growth",
    "writer", "content", "copywriter", "video", "compliance", "legal",
    "counsel", "tax ", "accountant", "office manager", "executive assistant",
    "trader ", "trading desk", "procurement", "risk management", "vip ",
    "specialist", "associate ", " analyst", "consultant", "advisor",
    "ios ", "android ", "mobile ", "angular", "react native", "swift",
    "kotlin developer", "flutter", "unity", "game", "3d artist",
    "data analyst", "data scientist",
    "machine learning research", "ml research", "research engineer",
    "scala", "ruby ", "rails", "php ", "salesforce",
    "operations manager", "logistics", "delivery manager", "project manager",
    "strategy", "treasury", "investor relations", "talent", "people ",
    "workday", "salesforce", "sap ", "oracle erp", "netsuite",
    "solutions engineer", "sales engineer", "pre-sales",
    "offensive security", "penetration tester", "appsec",
    "technical writer", "documentation",
    # leadership roles only when paired with a non-eng domain (handled below)
}

# Phrases that, when present alongside a leadership keyword, mean it's NOT
# eng leadership ("Head of Marketing", "VP of Sales", etc.). When alongside
# eng keywords, the role is fine ("Head of Engineering").
_LEADERSHIP_NON_ENG = {
    "marketing", "sales", "people", "talent", "finance", "legal",
    "compliance", "operations", "product management", "design",
    "growth", "community", "support", "customer", "hr ",
}
_LEADERSHIP_TOKENS = {"head of ", "vp of ", "vp ", "director of ", "director ", "chief "}

# Title MUST contain one of these or it's not engineering for our candidate
_TITLE_MUST_CONTAIN_ANY = {
    "engineer", "developer", "backend", "back-end", "back end",
    "rust", "python", "fullstack", "full-stack", "full stack",
    "platform", "infrastructure", "devops", "sre", "site reliability",
    "software", "programmer",
}

# Match against the candidate's tech_stack — overlap = signal.
# Plus: keywords that suggest backend/distributed/perf work the candidate cares about.
_TECH_KEYWORDS_BONUS = {
    "rust", "python", "go", "golang", "fastapi", "django", "flask",
    "postgres", "postgresql", "redis", "kafka", "rabbitmq", "mqtt",
    "microservice", "microservices", "distributed", "high-throughput",
    "low-latency", "actix", "tokio", "asyncio", "trading", "iot",
    "telemetry", "real-time", "websocket", "grpc",
}


@dataclass
class CuratedPair:
    """One scored (job_posting, decision_maker) pair, ready for outreach."""
    job: JobPosting
    company: Company
    dm: DecisionMaker
    score: int
    reasons: list[str]


async def load_candidates_from_db() -> tuple[list[tuple[JobPostingRow, CompanyRow, list[DecisionMakerRow]]]]:
    """Fetch every (job, company, dms-of-company) tuple. Skips orphan jobs.
    Returns: list of (job_row, company_row, [dm_rows])
    """
    Session = get_session_maker()
    out: list[tuple[JobPostingRow, CompanyRow, list[DecisionMakerRow]]] = []

    async with Session() as session:
        result = await session.execute(
            select(JobPostingRow, CompanyRow)
            .join(CompanyRow, CompanyRow.id == JobPostingRow.company_id)
            .order_by(JobPostingRow.posted_at.desc().nullslast())
        )
        rows = result.all()

        # Group dms by company_id in one query
        company_ids = {c.id for _, c in rows}
        dm_result = await session.execute(
            select(DecisionMakerRow).where(DecisionMakerRow.company_id.in_(company_ids))
        )
        dms_by_company: dict = {}
        for dm in dm_result.scalars():
            dms_by_company.setdefault(dm.company_id, []).append(dm)

        for jp, comp in rows:
            out.append((jp, comp, dms_by_company.get(comp.id, [])))

    return (out,)


def filter_and_score(
    bundle: list[tuple[JobPostingRow, CompanyRow, list[DecisionMakerRow]]],
    profile: CandidateProfile,
    max_age_days: int = 30,
    min_score: int = 30,
    dms_per_job: int = 2,
) -> list[CuratedPair]:
    """Apply hard filters and produce ranked CuratedPair list.

    For each surviving job we pick the top-K DMs (by role priority + has linkedin),
    so a single posting can be pitched to e.g. CTO + Founder. Set dms_per_job=1
    to fall back to the old single-best behaviour.
    """
    pairs: list[CuratedPair] = []
    cutoff = datetime.utcnow() - timedelta(days=max_age_days)
    candidate_tech = {t.lower() for t in profile.tech_stack}

    rejected_old = 0
    rejected_junk = 0
    rejected_no_dm = 0
    rejected_low = 0

    for jp, comp, dms in bundle:
        if jp.posted_at is None or jp.posted_at < cutoff:
            rejected_old += 1
            continue

        title_l = (jp.title or "").lower()
        if any(bad in title_l for bad in _TITLE_HARD_REJECT):
            rejected_junk += 1
            continue
        # Reject leadership titles ONLY when paired with a non-eng domain
        # ("Head of Marketing" out, "Head of Engineering" stays in).
        if any(tok in title_l for tok in _LEADERSHIP_TOKENS):
            if any(neg in title_l for neg in _LEADERSHIP_NON_ENG):
                rejected_junk += 1
                continue
        if not any(must in title_l for must in _TITLE_MUST_CONTAIN_ANY):
            rejected_junk += 1
            continue

        if not dms:
            rejected_no_dm += 1
            continue

        # Pick top-K DMs by role priority + linkedin presence
        ranked_dms = sorted(
            dms,
            key=lambda d: (
                -_role_priority(d.role),
                -int("linkedin" in (d.contacts or {})),
                d.full_name,
            ),
        )[:max(1, dms_per_job)]

        any_kept = False
        for dm in ranked_dms:
            score, reasons = _score_pair(jp, comp, dm, candidate_tech)
            if score < min_score:
                continue
            any_kept = True
            pairs.append(CuratedPair(
                job=_jp_row_to_domain(jp, comp),
                company=_company_row_to_domain(comp),
                dm=_dm_row_to_domain(dm),
                score=score,
                reasons=reasons,
            ))
        if not any_kept:
            rejected_low += 1

    pairs.sort(key=lambda p: -p.score)
    logger.info(
        "Curate: kept {} pairs (rejected: stale={}, junk-title={}, no-dm={}, low-score={})",
        len(pairs), rejected_old, rejected_junk, rejected_no_dm, rejected_low,
    )
    return pairs


def _score_pair(
    jp: JobPostingRow, comp: CompanyRow, dm: DecisionMakerRow, candidate_tech: set[str]
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    # Tech overlap — strongest signal. Job title carries more weight than the
    # company tech_stack tag bag (which is noisy aggregation of all their postings).
    title_l = (jp.title or "").lower()
    tags_l = f"{jp.tech_stack or ''}".lower()
    rest_l = f"{jp.description or ''} {comp.tech_stack or ''}".lower()

    title_overlap = {t for t in candidate_tech if t in title_l}
    tag_overlap = {t for t in candidate_tech if t in tags_l} - title_overlap
    other_overlap = {t for t in candidate_tech if t in rest_l} - title_overlap - tag_overlap
    bonus_hits = {kw for kw in _TECH_KEYWORDS_BONUS if kw in f"{title_l} {tags_l} {rest_l}"}

    score += len(title_overlap) * 30   # tech IN the title is the strongest signal
    score += len(tag_overlap) * 12
    score += len(other_overlap) * 6
    score += len(bonus_hits) * 4

    # Special bonus for the candidate's rare combo (python + rust)
    if {"rust", "python"} <= bonus_hits:
        score += 20
        reasons.append("python+rust combo")

    union = title_overlap | tag_overlap | other_overlap
    if union:
        reasons.append(f"tech: {', '.join(sorted(union))}")

    # Seniority match — middle/senior preferred
    if jp.seniority in ("middle", "senior", "staff", "lead"):
        score += 15
        reasons.append(f"seniority={jp.seniority}")
    elif jp.seniority == "junior":
        score -= 10

    # Recency boost
    if jp.posted_at:
        age = (datetime.utcnow() - jp.posted_at).days
        if age <= 3:
            score += 15
            reasons.append("posted ≤3d")
        elif age <= 7:
            score += 8

    # Low applicants when known
    if jp.applicants_count is not None and jp.applicants_count <= 25:
        score += 12
        reasons.append(f"low apps={jp.applicants_count}")

    # DM has contacts
    contacts = dm.contacts or {}
    if "linkedin" in contacts:
        score += 8
    if "email" in contacts:
        score += 12  # verified email is gold
    if not contacts:
        score -= 5

    # DM role weight
    role_obj = _safe_role(dm.role)
    score += role_obj.priority * 3

    return score, reasons


def _role_priority(role_str: str) -> int:
    return _safe_role(role_str).priority


def _safe_role(role_str: str) -> DecisionMakerRole:
    try:
        return DecisionMakerRole(role_str)
    except ValueError:
        return DecisionMakerRole.OTHER


def _jp_row_to_domain(jp: JobPostingRow, comp: CompanyRow) -> JobPosting:
    return JobPosting(
        title=jp.title,
        company_id=comp.id,
        company_name=comp.name,
        description=jp.description,
        tech_stack=TechStack.from_strings(*(jp.tech_stack or "").split(", ")),
        seniority=_safe_seniority(jp.seniority),
        is_remote=jp.is_remote,
        location=jp.location,
        salary_min=jp.salary_min,
        salary_max=jp.salary_max,
        salary_currency=jp.salary_currency,
        source=jp.source,
        source_url=jp.source_url,
        applicants_count=jp.applicants_count,
        posted_at=jp.posted_at,
        id=jp.id,
    )


def _company_row_to_domain(comp: CompanyRow) -> Company:
    return Company(
        name=comp.name,
        website=comp.website,
        tech_stack=TechStack.from_strings(*(comp.tech_stack or "").split(", ")),
        headcount=comp.headcount,
        location=comp.location,
        is_hiring=comp.is_hiring,
        source=comp.source,
        source_url=comp.source_url,
        id=comp.id,
    )


def _dm_row_to_domain(dm: DecisionMakerRow) -> DecisionMaker:
    return DecisionMaker(
        full_name=dm.full_name,
        role=_safe_role(dm.role),
        company_id=dm.company_id,
        title_raw=dm.title_raw,
        location=dm.location,
        contacts=dict(dm.contacts or {}),
        id=dm.id,
    )


def _safe_seniority(s: str | None) -> Seniority:
    if not s:
        return Seniority.UNKNOWN
    try:
        return Seniority(s)
    except ValueError:
        return Seniority.UNKNOWN
