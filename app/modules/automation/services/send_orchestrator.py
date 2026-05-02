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

import random
import re
from datetime import datetime

from loguru import logger

from app.modules.applies.ports.mass_apply import MassApplyRepository, PendingOutreach
from app.modules.automation.adapters.camoufox import browser_session, human_sleep
from app.modules.automation.adapters.linkedin_outreach import (
    INVITE_NOTE_MAX_CHARS,
    OutreachOutcome,
    OutreachResult,
    send_outreach,
)

MAX_SENT_PER_DAY = 30
MAX_SEND_PER_BATCH = 5
MIN_GAP_BETWEEN_SENDS_S = 120


def _default_repo() -> MassApplyRepository:
    from app.modules.applies.adapters.repository.mass_apply import SqlaMassApplyRepository
    return SqlaMassApplyRepository()


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


async def _record_outcome(
    repo: MassApplyRepository, apply_id, outcome: OutreachOutcome, detail: str = "",
) -> None:
    """Update the apply row with the result of the send attempt via port."""
    success = outcome in (OutreachOutcome.SENT_VIA_MESSAGE, OutreachOutcome.SENT_VIA_INVITE)
    now = datetime.utcnow().isoformat()
    if success:
        notes = f"sent via {outcome.value} at {now}"
    elif outcome == OutreachOutcome.SKIPPED_NO_BUTTON:
        notes = f"skipped (no Connect/Message button) at {now}"
    elif outcome == OutreachOutcome.SKIPPED_PROFILE_GONE:
        notes = f"profile gone at {now}"
    elif outcome == OutreachOutcome.BLOCKED_RATE_LIMIT:
        notes = f"BLOCKED rate-limit at {now}: {detail}"
    else:
        notes = f"failed at {now}: {detail}"
    await repo.mark_apply_sent(apply_id, success=success, notes=notes)


async def run_send_batch(
    limit: int = MAX_SEND_PER_BATCH,
    dry_run: bool = True,
    headless: bool = True,
    repo: MassApplyRepository | None = None,
) -> dict:
    """Main entry point. Loads pending applies, sends up to `limit` of them.

    Returns stats dict: {sent_invite, sent_message, skipped, blocked, failed}.
    `repo` is the MassApplyRepository port; defaults to SQLA-backed impl.
    """
    limit = min(limit, MAX_SEND_PER_BATCH)
    repo = repo or _default_repo()
    sent_today = await repo.count_applies_today("dm_outreach")
    if sent_today >= MAX_SENT_PER_DAY:
        logger.warning("Daily cap reached: {}/{} already sent in last 24h",
                       sent_today, MAX_SENT_PER_DAY)
        return {"daily_cap_reached": True, "sent_today": sent_today}

    headroom = MAX_SENT_PER_DAY - sent_today
    effective_limit = min(limit, headroom)

    pending: list[PendingOutreach] = await repo.list_pending_dm_outreach(effective_limit)
    # Filter rows that don't have a usable LinkedIn URL — the repository
    # returns the contact dict's "linkedin" verbatim; not every DM has one.
    pending = [p for p in pending if p.dm_linkedin_url and "linkedin.com/in/" in p.dm_linkedin_url]
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
            print(f"Profile: {p.dm_linkedin_url}")
            print(f"Note ({len(note)}c):\n{note}")
            print(f"Body ({len(p.body)}c) — used if Message button available")
        return {"pending": len(pending), "dry_run": True}

    async with browser_session(headless=headless) as page:
        for i, p in enumerate(pending, 1):
            logger.info("[{}/{}] {} ({})", i, len(pending), p.dm_full_name, p.company_name)
            note = make_short_note(p.body, p.dm_full_name)
            try:
                result: OutreachResult = await send_outreach(
                    page, p.dm_linkedin_url, note=note, body=p.body,
                )
            except Exception as e:
                logger.exception("send_outreach raised: {}", e)
                result = OutreachResult(OutreachOutcome.FAILED, str(e))

            await _record_outcome(repo, p.apply_id, result.outcome, result.detail)

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

            if i < len(pending):
                gap = MIN_GAP_BETWEEN_SENDS_S + random.randint(0, 60)
                logger.info("Pausing {}s before next send (anti-pattern guard)", gap)
                await human_sleep(gap, gap + 30)

    logger.info("Batch done: {}", stats)
    return stats
