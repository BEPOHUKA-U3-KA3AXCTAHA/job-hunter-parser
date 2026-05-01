"""Orchestrator for the DM-outreach flank.

Pulls pending `applies` rows from the DB, for each one:
  1. Resolves the DM's LinkedIn URL from contacts JSON
  2. Generates a short connect-invite note from the body (≤300 chars)
  3. Calls linkedin_outreach.send_outreach() in the Camoufox session
  4. Updates apply.sent_at + status based on the OutreachOutcome
  5. Enforces daily caps + inter-action pauses

Hard guardrails (cannot be bypassed by CLI flag):
  MAX_SENT_PER_DAY        = 30   # combined Connect + Message
  MAX_SEND_PER_BATCH      = 5    # per single CLI invocation
  MIN_GAP_BETWEEN_SENDS_S = 120  # 2 min minimum between actions

Exits early if BLOCKED_RATE_LIMIT is returned — we don't want to keep poking
LinkedIn after they show a verification page.
"""
from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import and_, or_, select

from app.modules.automation.adapters.camoufox import browser_session, human_sleep
from app.modules.automation.adapters.linkedin_outreach import (
    INVITE_NOTE_MAX_CHARS,
    OutreachOutcome,
    OutreachResult,
    send_outreach,
)
from app.infra.db import get_session_maker
from app.modules.applies.adapters.orm import ApplyRow
from app.modules.companies.adapters.orm import CompanyRow, JobPostingRow
from app.modules.people.adapters.orm import DecisionMakerRow

MAX_SENT_PER_DAY = 30
MAX_SEND_PER_BATCH = 5
MIN_GAP_BETWEEN_SENDS_S = 120


@dataclass
class _PendingApply:
    """In-memory snapshot of one row we want to send."""
    apply_id: str
    body: str
    dm_full_name: str
    dm_linkedin: str
    company_name: str
    job_title: str | None


def make_short_note(body: str, name: str) -> str:
    """Trim a long letter body down to a ≤300-char Connect-invite note.

    Strategy:
      1. Take first 2 sentences after the greeting
      2. Strip sign-off and Job/CV link tail
      3. Clamp to INVITE_NOTE_MAX_CHARS, ending at a word boundary
    """
    # Drop the "Job:" / "CV:" tail
    text = re.split(r"\n\s*(?:Job|CV)\s*:\s*", body, maxsplit=1)[0].strip()
    # Drop sign-off ("Thanks", "Best", "Sergey")
    text = re.split(r"\n\s*(?:Thanks|Best|Cheers|Sergey)", text, maxsplit=1)[0].strip()
    # If first line is "Hi <name>," — keep it, otherwise prepend
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        lines = [body.strip()[:200]]
    if not lines[0].lower().startswith("hi "):
        first_name = name.split()[0] if name else "there"
        lines.insert(0, f"Hi {first_name},")

    # Take greeting + 2 sentences from the body
    body_text = " ".join(lines[1:])
    sentences = re.split(r"(?<=[.!?])\s+", body_text)
    short_body = " ".join(sentences[:2]).strip()

    candidate = f"{lines[0]}\n\n{short_body}"
    if len(candidate) <= INVITE_NOTE_MAX_CHARS:
        return candidate

    # Clamp at word boundary
    truncated = candidate[: INVITE_NOTE_MAX_CHARS - 3]
    last_space = truncated.rfind(" ")
    if last_space > INVITE_NOTE_MAX_CHARS * 0.7:
        truncated = truncated[:last_space]
    return truncated.rstrip() + "..."


async def _load_pending(limit: int) -> list[_PendingApply]:
    """Pull applies that are ready to send: dm_outreach flank, generated/queued
    status, never sent, has DM LinkedIn URL."""
    Session = get_session_maker()
    out: list[_PendingApply] = []
    async with Session() as session:
        result = await session.execute(
            select(ApplyRow, DecisionMakerRow, CompanyRow, JobPostingRow)
            .join(DecisionMakerRow, ApplyRow.decision_maker_id == DecisionMakerRow.id)
            .join(CompanyRow, DecisionMakerRow.company_id == CompanyRow.id)
            .outerjoin(JobPostingRow, ApplyRow.job_posting_id == JobPostingRow.id)
            .where(
                and_(
                    ApplyRow.flank == "dm_outreach",
                    ApplyRow.sent_at.is_(None),
                    or_(ApplyRow.status == "generated", ApplyRow.status == "queued"),
                    ApplyRow.body.is_not(None),
                )
            )
            .order_by(ApplyRow.relevance_score.desc(), ApplyRow.generated_at.asc())
            .limit(limit)
        )
        for ap, dm, comp, jp in result.all():
            contacts = dict(dm.contacts or {})
            li = contacts.get("linkedin") or ""
            if not li or "linkedin.com/in/" not in li:
                continue
            out.append(_PendingApply(
                apply_id=ap.id.hex if hasattr(ap.id, "hex") else str(ap.id),
                body=ap.body or "",
                dm_full_name=dm.full_name,
                dm_linkedin=li,
                company_name=comp.name,
                job_title=jp.title if jp else None,
            ))
    return out


async def _count_sent_today() -> int:
    """How many applies were sent in the last 24h (any flank, any channel)."""
    Session = get_session_maker()
    cutoff = datetime.utcnow() - timedelta(hours=24)
    async with Session() as session:
        result = await session.execute(
            select(ApplyRow).where(
                and_(ApplyRow.sent_at.is_not(None), ApplyRow.sent_at >= cutoff)
            )
        )
        return len(list(result.scalars()))


async def _record_outcome(apply_id: str, outcome: OutreachOutcome, detail: str = "") -> None:
    """Update the apply row with the result of the send attempt."""
    Session = get_session_maker()
    async with Session() as session:
        from uuid import UUID
        # ID was hexed back; SQLite stores as bytes via SA's UUID type
        try:
            uuid_obj = UUID(hex=apply_id)
        except ValueError:
            uuid_obj = apply_id  # fallback
        result = await session.execute(
            select(ApplyRow).where(ApplyRow.id == uuid_obj)
        )
        row = result.scalar_one_or_none()
        if not row:
            logger.warning("apply {} not found when recording outcome", apply_id)
            return

        now = datetime.utcnow()
        if outcome in (OutreachOutcome.SENT_VIA_MESSAGE, OutreachOutcome.SENT_VIA_INVITE):
            row.sent_at = now
            row.status = "sent"
            row.method = "auto_outreach"
            row.notes = (row.notes or "") + f" | sent via {outcome.value} at {now.isoformat()}"
        elif outcome == OutreachOutcome.SKIPPED_NO_BUTTON:
            row.notes = (row.notes or "") + f" | skipped (no Connect/Message button) at {now.isoformat()}"
        elif outcome == OutreachOutcome.SKIPPED_PROFILE_GONE:
            row.status = "failed"
            row.notes = (row.notes or "") + f" | profile gone at {now.isoformat()}"
        elif outcome == OutreachOutcome.BLOCKED_RATE_LIMIT:
            row.notes = (row.notes or "") + f" | BLOCKED rate-limit at {now.isoformat()}: {detail}"
        else:  # FAILED
            row.notes = (row.notes or "") + f" | failed at {now.isoformat()}: {detail}"
        await session.commit()


async def run_send_batch(
    limit: int = MAX_SEND_PER_BATCH,
    dry_run: bool = True,
    headless: bool = True,
) -> dict:
    """Main entry point. Loads pending applies, sends up to `limit` of them.

    Returns stats dict: {sent_invite, sent_message, skipped, blocked, failed}.
    """
    limit = min(limit, MAX_SEND_PER_BATCH)
    sent_today = await _count_sent_today()
    if sent_today >= MAX_SENT_PER_DAY:
        logger.warning("Daily cap reached: {}/{} already sent in last 24h",
                       sent_today, MAX_SENT_PER_DAY)
        return {"daily_cap_reached": True, "sent_today": sent_today}

    headroom = MAX_SENT_PER_DAY - sent_today
    effective_limit = min(limit, headroom)

    pending = await _load_pending(effective_limit)
    logger.info("Pending applies for dm_outreach: {} (loading top {})",
                len(pending), effective_limit)

    if not pending:
        return {"pending": 0}

    stats = {
        "sent_invite": 0, "sent_message": 0,
        "skipped_no_button": 0, "skipped_gone": 0,
        "blocked": 0, "failed": 0,
    }

    if dry_run:
        logger.info("--dry-run: not opening browser. Showing what WOULD be sent:")
        for p in pending:
            note = make_short_note(p.body, p.dm_full_name)
            print(f"\n=== {p.dm_full_name} ({p.company_name}) ===")
            print(f"Profile: {p.dm_linkedin}")
            print(f"Note ({len(note)}c):\n{note}")
            print(f"Body ({len(p.body)}c) — used if Message button available")
        return {"pending": len(pending), "dry_run": True}

    async with browser_session(headless=headless) as page:
        for i, p in enumerate(pending, 1):
            logger.info("[{}/{}] {} ({})", i, len(pending), p.dm_full_name, p.company_name)
            note = make_short_note(p.body, p.dm_full_name)
            try:
                result: OutreachResult = await send_outreach(
                    page, p.dm_linkedin, note=note, body=p.body,
                )
            except Exception as e:
                logger.exception("send_outreach raised: {}", e)
                result = OutreachResult(OutreachOutcome.FAILED, str(e))

            await _record_outcome(p.apply_id, result.outcome, result.detail)

            if result.outcome == OutreachOutcome.SENT_VIA_INVITE:
                stats["sent_invite"] += 1
            elif result.outcome == OutreachOutcome.SENT_VIA_MESSAGE:
                stats["sent_message"] += 1
            elif result.outcome == OutreachOutcome.SKIPPED_NO_BUTTON:
                stats["skipped_no_button"] += 1
            elif result.outcome == OutreachOutcome.SKIPPED_PROFILE_GONE:
                stats["skipped_gone"] += 1
            elif result.outcome == OutreachOutcome.BLOCKED_RATE_LIMIT:
                stats["blocked"] += 1
                logger.error("STOPPING batch: LinkedIn rate-limited us. Wait 24h before retrying.")
                break
            else:
                stats["failed"] += 1

            # Pause between actions
            if i < len(pending):
                gap = MIN_GAP_BETWEEN_SENDS_S + random.randint(0, 60)
                logger.info("Pausing {}s before next send (anti-pattern guard)", gap)
                await human_sleep(gap, gap + 30)

    logger.info("Batch done: {}", stats)
    return stats
