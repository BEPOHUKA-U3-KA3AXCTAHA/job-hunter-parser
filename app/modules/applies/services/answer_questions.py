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
from app.modules.applies.services.qa_cache import get_cached, save_to_cache
from app.shared import CandidateProfile

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

    return f"""You are filling out a job application form on behalf of the candidate. Use the candidate's CV (verbatim copy below) as the PRIMARY source of truth for every factual answer. Use the visa block for any work-authorization / sponsorship question. Reply ONLY with a JSON array, one object per question, in the same order.

Each object: {{"answer": "<string>", "confidence": <0.0-1.0>, "reasoning": "<short why>"}}

Rules:
- Treat the CV as ground truth. If a fact is in the CV (LinkedIn handle, languages, years of experience, projects, skills, education) — use it verbatim. NEVER fabricate a value the CV doesn't support.
- LinkedIn / LinkedIn URL field: use the LinkedIn handle from the CV. If the CV says "LinkedIn: <handle>" then answer "https://www.linkedin.com/in/<handle>/".
- Numeric / salary questions: respond with the number ONLY (e.g. "{profile.salary_floor_eur}", not "€{profile.salary_floor_eur:,}"). Default floor for senior backend remote: {profile.salary_floor_eur} EUR / {profile.salary_floor_usd} USD per year, adjust upward for FAANG / US-payroll / staff+ roles.
- Visa / sponsorship: candidate is NOT an EU citizen and has NO EU work permit (see visa block). If the role is in an EU country (Portugal, Germany, etc.) and the question asks "do you require sponsorship to work in <country>?" — answer YES (sponsorship needed). The fact that this question is ASKED at all means the role is not pure remote-anywhere. For "Are you authorized to work in <country>?" — answer NO unless the country happens to be Montenegro/Russia.
- English level — take from the CV's LANGUAGES line VERBATIM. If CV says "B2", do NOT upgrade to "Advanced" or "Fluent". Match the option closest to "B2" / "Upper Intermediate".
- Years of <tech>: derive from the CV. Years of total commercial experience = years since the earliest role.
- "Why are you interested?" / cover-letter style: 2-3 sentences max, reference the company name + one specific job-description detail + one matching CV highlight.
- "How did you hear about us?" — "LinkedIn".
- Optional fields: only fill if confidence ≥ 0.6. Otherwise answer "" with confidence < 0.5 and the bot will skip.
- If you genuinely cannot answer (e.g. US SSN, country-specific ID), answer "" and set confidence=0.

Return ONLY the JSON array, no prose, no markdown fences.

CANDIDATE CV (ground truth):
{cv}

VISA / WORK AUTHORIZATION:
{visa_block}

JOB:
{job_block}

QUESTIONS TO ANSWER:
{qs_block}
"""


async def answer_questions(
    questions: list[FormQuestion],
    job_title: str = "",
    job_description: str = "",
    company_name: str = "",
    profile: CandidateProfile | None = None,
    model: str = "claude-sonnet-4-6",
) -> list[FormAnswer]:
    """Ask Claude (via local `claude` CLI) to answer form questions, in order.

    Uses the user's Claude Max subscription via subprocess — no API key.
    Returns empty list if the CLI call failed — caller should treat as
    'cannot auto-fill, bail to the human'.
    """
    if not questions:
        return []

    profile = profile or CandidateProfile()

    # 1. Cache lookup — every question, in order. Hits replace the LLM round-trip.
    cached_answers: list[FormAnswer | None] = []
    for q in questions:
        hit = await get_cached(q.label)
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
            await save_to_cache(
                q.label, ans.answer, q.options or None,
                source="llm", confidence=ans.confidence,
                company=company_name, job_title=job_title,
            )

    return [a or FormAnswer(answer="", confidence=0.0) for a in final_answers]
