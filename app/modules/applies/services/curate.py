"""Curate (job_posting × decision_maker) pairs.

Pure scoring + filtering logic over domain objects. No SQLAlchemy here —
the data comes in as `list[CandidateBundle]` from a CandidateBundles
adapter (composition root wires the SQLA-backed instance).

Hard filters here are defensive — `matches_title` already rejects most
junk at scrape time, but old DB rows or schema-evolved exclude lists can
still leak through.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from loguru import logger

from app.modules.applies.ports.candidates import CandidateBundle
from app.modules.companies import Company, JobPosting
from app.modules.people import DecisionMaker, DecisionMakerRole
from app.modules.companies import TechStack
from app.modules.users import CandidateProfile

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
}

_LEADERSHIP_NON_ENG = {
    "marketing", "sales", "people", "talent", "finance", "legal",
    "compliance", "operations", "product management", "design",
    "growth", "community", "support", "customer", "hr ",
}
_LEADERSHIP_TOKENS = {"head of ", "vp of ", "vp ", "director of ", "director ", "chief "}

_TITLE_MUST_CONTAIN_ANY = {
    "engineer", "developer", "backend", "back-end", "back end",
    "rust", "python", "fullstack", "full-stack", "full stack",
    "platform", "infrastructure", "devops", "sre", "site reliability",
    "software", "programmer",
}

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


def filter_and_score(
    bundles: list[CandidateBundle],
    profile: CandidateProfile,
    max_age_days: int = 30,
    min_score: int = 30,
    dms_per_job: int = 2,
) -> list[CuratedPair]:
    """Apply hard filters and produce ranked CuratedPair list.

    For each surviving job we pick the top-K DMs (by role priority + has linkedin),
    so a single posting can be pitched to e.g. CTO + Founder. Set dms_per_job=1
    to fall back to the single-best behaviour.
    """
    pairs: list[CuratedPair] = []
    cutoff = datetime.utcnow() - timedelta(days=max_age_days)
    candidate_tech = {t.lower() for t in profile.tech_stack}

    rejected_old = 0
    rejected_junk = 0
    rejected_no_dm = 0
    rejected_low = 0

    for bundle in bundles:
        job, comp, dms = bundle.job, bundle.company, bundle.decision_makers

        if job.posted_at is None or job.posted_at < cutoff:
            rejected_old += 1
            continue

        title_l = (job.title or "").lower()
        if any(bad in title_l for bad in _TITLE_HARD_REJECT):
            rejected_junk += 1
            continue
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

        ranked_dms = sorted(
            dms,
            key=lambda d: (
                -d.role.priority,
                -int("linkedin" in (d.contacts or {})),
                d.full_name,
            ),
        )[:max(1, dms_per_job)]

        any_kept = False
        for dm in ranked_dms:
            score, reasons = _score_pair(job, comp, dm, candidate_tech)
            if score < min_score:
                continue
            any_kept = True
            pairs.append(CuratedPair(job=job, company=comp, dm=dm, score=score, reasons=reasons))
        if not any_kept:
            rejected_low += 1

    pairs.sort(key=lambda p: -p.score)
    logger.info(
        "Curate: kept {} pairs (rejected: stale={}, junk-title={}, no-dm={}, low-score={})",
        len(pairs), rejected_old, rejected_junk, rejected_no_dm, rejected_low,
    )
    return pairs


def _score_pair(
    job: JobPosting, company: Company, dm: DecisionMaker, candidate_tech: set[str]
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    title_l = (job.title or "").lower()
    job_tags_l = " ".join(sorted(job.tech_stack.technologies)) if job.tech_stack else ""
    comp_tags_l = " ".join(sorted(company.tech_stack.technologies)) if company.tech_stack else ""
    rest_l = f"{job.description or ''} {comp_tags_l}".lower()

    title_overlap = {t for t in candidate_tech if t in title_l}
    tag_overlap = {t for t in candidate_tech if t in job_tags_l} - title_overlap
    other_overlap = {t for t in candidate_tech if t in rest_l} - title_overlap - tag_overlap
    bonus_hits = {kw for kw in _TECH_KEYWORDS_BONUS if kw in f"{title_l} {job_tags_l} {rest_l}"}

    score += len(title_overlap) * 30
    score += len(tag_overlap) * 12
    score += len(other_overlap) * 6
    score += len(bonus_hits) * 4

    if {"rust", "python"} <= bonus_hits:
        score += 20
        reasons.append("python+rust combo")

    union = title_overlap | tag_overlap | other_overlap
    if union:
        reasons.append(f"tech: {', '.join(sorted(union))}")

    seniority_value = job.seniority.value if job.seniority else None
    if seniority_value in ("middle", "senior", "staff", "lead"):
        score += 15
        reasons.append(f"seniority={seniority_value}")
    elif seniority_value == "junior":
        score -= 10

    if job.posted_at:
        age = (datetime.utcnow() - job.posted_at).days
        if age <= 3:
            score += 15
            reasons.append("posted ≤3d")
        elif age <= 7:
            score += 8

    if job.applicants_count is not None and job.applicants_count <= 25:
        score += 12
        reasons.append(f"low apps={job.applicants_count}")

    contacts = dm.contacts or {}
    if "linkedin" in contacts:
        score += 8
    if "email" in contacts:
        score += 12
    if not contacts:
        score -= 5

    role = dm.role if isinstance(dm.role, DecisionMakerRole) else DecisionMakerRole.OTHER
    score += role.priority * 3

    return score, reasons
