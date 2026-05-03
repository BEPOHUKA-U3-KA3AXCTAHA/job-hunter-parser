"""LLM-backed answer generation for ATS additional-questions forms.

Different jobs ask different questions (salary expectation, years of Python,
why this company, work auth, notice period, etc). Hard-coded heuristics
won't scale — we feed every unfilled question to Claude with the candidate
profile + job context and it returns answers shaped to the input type.

Backed by the Claude CLI subprocess (user's Claude Max subscription) — no
API key needed. Default model: claude-sonnet-4-6 (Haiku is too dumb for
nuanced HR-questions per user feedback).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

from loguru import logger

from app.modules.applies.adapters.llm.cli import ClaudeCLIPool
from app.modules.applies.ports.qa_cache import QACacheRepository
from app.modules.users import CandidateProfile

QuestionType = Literal["text", "number", "tel", "email", "textarea", "select", "radio", "checkbox"]


@dataclass(slots=True)
class FormQuestion:
    """One unfilled form field discovered on a modal page."""

    label: str                              # the visible question text
    type: QuestionType
    options: list[str] = field(default_factory=list)   # for select/radio
    name: str = ""                          # input name/id (helps the LLM disambiguate)
    placeholder: str = ""
    required: bool = True


@dataclass(slots=True)
class FormAnswer:
    """LLM's answer for a single FormQuestion. Shape matches the input:
    - text/number/tel/email/textarea: free-form string
    - select/radio: exactly one of the question's options
    - checkbox: '' to leave unchecked, 'true' to check
    """

    answer: str
    confidence: float = 1.0                 # 0..1 — caller may decide to skip-and-bail on < threshold
    reasoning: str = ""                     # short why-this-answer (for logs / debugging)


def _build_prompt(
    questions: list[FormQuestion],
    job_title: str,
    job_description: str,
    company_name: str,
    profile: CandidateProfile,
) -> str:
    user_info_block = (
        profile.user_info
        or "(no user profile in DB yet — fall back to CV + visa block below)"
    )
    cv = profile.cv_text or "(CV file not available — fall back to general knowledge.)"
    visa_block = (
        f"- EU citizen: {'yes' if profile.eu_citizen else 'NO'}\n"
        f"- Work permit in EU: {'yes' if profile.work_permit_eu else 'NO'}\n"
        f"- Status: {profile.permit_status}"
    )
    job_block = (
        f"Title: {job_title}\n"
        f"Company: {company_name}\n"
        f"Description (snippet): {(job_description or '').strip()[:2000]}"
    )

    qs_block_lines = []
    for i, q in enumerate(questions, 1):
        opts = f" Options (reply with one verbatim): {q.options}" if q.options else ""
        req = " [REQUIRED]" if q.required else " [optional — fill if you have a confident answer]"
        qs_block_lines.append(
            f"{i}. [type={q.type}{req}] {q.label}{opts}"
            + (f"\n   placeholder: {q.placeholder}" if q.placeholder else "")
        )
    qs_block = "\n".join(qs_block_lines)

    return f"""You are filling out a job application form on behalf of the candidate. Reply ONLY with a JSON array, one object per question, in the same order.

Each object: {{"answer": "<string>", "confidence": <0.0-1.0>, "reasoning": "<short why>"}}

Rules:
- USER PROFILE block at the BOTTOM is the ABSOLUTE ground truth. It overrides EVERYTHING else (CV, common knowledge, your priors). Read it carefully every time.
- CITY / LOCATION questions: ALWAYS answer with the EXACT city listed in USER PROFILE. NEVER default to the country name or the capital. If profile says "Bar, Montenegro" and the question asks "Where do you live?" — answer "Bar, Montenegro", not "Montenegro" or "Podgorica". This is a hard rule — DO NOT rationalize past it.
- LinkedIn / LinkedIn URL: use the EXACT URL from USER PROFILE. If the profile only has a handle, expand to "https://www.linkedin.com/in/<handle>/". Never invent a URL.
- Numeric / salary: number ONLY (e.g. "{profile.salary_floor_eur}", not "€{profile.salary_floor_eur:,}"). Floor: {profile.salary_floor_eur} EUR / {profile.salary_floor_usd} USD per year for senior backend remote. Adjust upward for FAANG / US-payroll / staff+ roles.
- Visa / sponsorship: see VISA block. If the role is in an EU country and asks "do you require sponsorship?" → YES. For "Are you authorized to work in <EU country>?" → NO. The very fact that this question is asked means the role isn't pure remote-anywhere.
- Language level: take VERBATIM from USER PROFILE or CV. If it says "English: B2", do NOT upgrade to "Advanced" or "Fluent".
- Years of <tech>: derive from CV / USER PROFILE.
- "Why are you interested?" / cover letter: 2-3 sentences max, reference company name + one job-description detail + one matching highlight.
- "How did you hear about us?" — "LinkedIn".
- Optional fields (no asterisk / not required): only fill if confidence ≥ 0.6. Else answer "" with conf < 0.5.
- Genuinely unknown (US SSN, country-specific ID): answer "" with conf=0.

Return ONLY the JSON array, no prose, no markdown fences.

CANDIDATE CV (secondary source — may carry stale info, USER PROFILE wins):
{cv}

VISA / WORK AUTHORIZATION:
{visa_block}

JOB:
{job_block}

USER PROFILE (★ GROUND TRUTH — overrides CV and any common-sense default ★):
{user_info_block}

QUESTIONS TO ANSWER:
{qs_block}
"""


def _default_uow_factory():
    """Lazily resolve the production UoW factory so importing this service
    doesn't drag the ORM in at module-load time."""
    from app.modules.applies import default_uow
    return default_uow


async def answer_questions(
    questions: list[FormQuestion],
    job_title: str = "",
    job_description: str = "",
    company_name: str = "",
    profile: CandidateProfile | None = None,
    model: str = "claude-sonnet-4-6",
    uow_factory=None,
) -> list[FormAnswer]:
    """Ask Claude (via local `claude` CLI) to answer form questions, in order.

    Uses the user's Claude Max subscription via subprocess — no API key.
    Returns empty list if the CLI call failed — caller should treat as
    'cannot auto-fill, bail to the human'.

    `cache` is the QACacheRepository port; defaults to SQLA-backed impl.
    Injectable so callers can swap in an in-memory cache for tests.
    """
    if not questions:
        return []

    profile = profile or CandidateProfile()
    uow_factory = uow_factory or _default_uow_factory()

    # 1. Cache lookup — every question, in order. Hits replace the LLM round-trip.
    cached_answers: list[FormAnswer | None] = []
    async with uow_factory() as _read_uow:
        cache = _read_uow.qa_cache
        for q in questions:
            hit = await cache.get_cached(q.label)
            if hit:
                ans_text, source, conf = hit
                # If LLM-cached but the question has a closed option list and the
                # cached answer no longer matches the current options — invalidate
                # (form options can change between job postings).
                if q.options and ans_text not in q.options:
                    cached_answers.append(None)
                    continue
                cached_answers.append(FormAnswer(
                    answer=ans_text, confidence=conf,
                    reasoning=f"cached ({source})",
                ))
                logger.info(
                    "Q (cache-{}): {!r} → {!r} (conf={:.2f})",
                    source, q.label[:50], ans_text[:60], conf,
                )
            else:
                cached_answers.append(None)

    # 2. If every question is cached, skip LLM entirely
    pending_idx = [i for i, a in enumerate(cached_answers) if a is None]
    if not pending_idx:
        return [a for a in cached_answers if a is not None]

    # 3. LLM only for the uncached questions
    pending_qs = [questions[i] for i in pending_idx]
    system = (
        "You are filling out a job application form on behalf of the candidate. "
        "Reply ONLY with a JSON array, one object per question, in the same order. "
        "No prose, no markdown fences."
    )
    user_prompt = _build_prompt(pending_qs, job_title, job_description, company_name, profile)

    pool = ClaudeCLIPool(workers=1, model=model, timeout_s=120)
    results = await pool.batch_generate([(system, user_prompt)])
    if not results or not results[0].ok:
        err = results[0].error if results else "no result"
        logger.error("Claude CLI question-answer failed: {}", err)
        return []
    body = results[0].text.strip()

    # Strip accidental markdown fences
    if body.startswith("```"):
        body = body.strip("`")
        if body.lower().startswith("json"):
            body = body[4:]
        body = body.strip()

    try:
        raw = json.loads(body)
    except json.JSONDecodeError as e:
        logger.error("Claude returned non-JSON for form answers: {} | body={!r}", e, body[:300])
        return []

    if not isinstance(raw, list) or len(raw) != len(pending_qs):
        logger.warning(
            "Claude returned {} answers for {} pending questions — shape mismatch",
            len(raw) if isinstance(raw, list) else "non-list", len(pending_qs),
        )
        return [a or FormAnswer(answer="", confidence=0.0, reasoning="LLM mismatch")
                for a in cached_answers]

    # 4. Merge LLM answers back into the original order
    final_answers = list(cached_answers)
    for k, item in enumerate(raw):
        idx = pending_idx[k]
        q = questions[idx]
        if not isinstance(item, dict):
            final_answers[idx] = FormAnswer(answer="", confidence=0.0, reasoning="bad shape")
            continue
        ans = FormAnswer(
            answer=str(item.get("answer", "")),
            confidence=float(item.get("confidence", 1.0) or 0.0),
            reasoning=str(item.get("reasoning", ""))[:200],
        )
        # For select/radio: enforce option match
        if q.options and ans.answer not in q.options:
            logger.warning(
                "Q{} answer {!r} not in options {} — clearing",
                idx + 1, ans.answer, q.options,
            )
            ans = FormAnswer(answer="", confidence=0.0, reasoning="not in options")
        logger.info(
            "Q{} ({}, llm): {!r} (conf={:.2f}) — {}",
            idx + 1, q.type, ans.answer[:60], ans.confidence, ans.reasoning[:100],
        )
        final_answers[idx] = ans

        # Persist confident LLM answers — user can review/correct via `jhp qa review`
        if ans.answer and ans.confidence >= 0.6:
            async with uow_factory() as _write_uow:
                await _write_uow.qa_cache.save_to_cache(
                    q.label, ans.answer, q.options or None,
                    source="llm", confidence=ans.confidence,
                    company=company_name, job_title=job_title,
                )
                await _write_uow.commit()

    return [a or FormAnswer(answer="", confidence=0.0) for a in final_answers]
