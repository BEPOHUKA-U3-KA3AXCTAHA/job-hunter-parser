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
    system = (
        "You are filling out a job application form on behalf of the candidate. "
        "Reply ONLY with a JSON array, one object per question, in the same order. "
        "No prose, no markdown fences."
    )
    user_prompt = _build_prompt(questions, job_title, job_description, company_name, profile)

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

    if not isinstance(raw, list) or len(raw) != len(questions):
        logger.warning(
            "Claude returned {} answers for {} questions — shape mismatch",
            len(raw) if isinstance(raw, list) else "non-list", len(questions),
        )
        return []

    answers: list[FormAnswer] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            answers.append(FormAnswer(answer="", confidence=0.0, reasoning="bad shape"))
            continue
        ans = FormAnswer(
            answer=str(item.get("answer", "")),
            confidence=float(item.get("confidence", 1.0) or 0.0),
            reasoning=str(item.get("reasoning", ""))[:200],
        )
        # For select/radio: enforce option match
        if questions[i].options and ans.answer not in questions[i].options:
            logger.warning(
                "Q{} answer {!r} not in options {} — clearing",
                i + 1, ans.answer, questions[i].options,
            )
            ans = FormAnswer(answer="", confidence=0.0, reasoning="not in options")
        logger.info(
            "Q{} ({}): {!r} (conf={:.2f}) — {}",
            i + 1, questions[i].type, ans.answer[:60], ans.confidence, ans.reasoning[:100],
        )
        answers.append(ans)

    return answers
