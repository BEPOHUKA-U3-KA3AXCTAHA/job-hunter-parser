"""Fallback handler for unknown ATS — uses the same Shadow-DOM walker the
Easy Apply modal uses. Best-effort: looks for any visible required form
fields, fills name / email / phone / linkedin / location heuristically,
runs the LLM autofill on what's left, attempts upload + submit.
"""
from __future__ import annotations

import time
from urllib.parse import urlparse

from loguru import logger

from app.modules.automation.adapters.external_apply.base import (
    AtsContext,
    AtsResult,
    click_button_by_text,
    fill_input,
    find_visible,
    upload_resume,
)


class GenericHandler:
    name = "generic"

    def can_handle(self, url: str) -> bool:
        return True   # last-resort fallback

    def apply(self, driver, ctx: AtsContext) -> AtsResult:
        time.sleep(2.5)
        host = urlparse(driver.current_url).netloc

        # ATS landing pages often show only a job description + an "Apply…"
        # button that takes you to the actual form. Match any leading "Apply"
        # word — covers "Apply", "Apply now", "Apply for this job",
        # "Apply to role" (YC), "Apply here", etc.
        for pat in [r"^\s*apply\b"]:
            if click_button_by_text(driver, pat, timeout=2):
                logger.info("generic[{}]: clicked landing-page Apply", host)
                time.sleep(2.5)
                # The new tab may have switched again — refresh handle if so
                handles = driver.window_handles
                if len(handles) > 1:
                    driver.switch_to.window(handles[-1])
                    time.sleep(1.5)
                break

        filled = 0

        # Heuristic name/email/phone fill — matches by input name/id substring
        for substr, value in [
            ("first", ctx.profile_first_name),
            ("last", ctx.profile_last_name),
            ("email", ctx.profile_email),
            ("phone", ctx.profile_phone),
            ("linkedin", ctx.profile_linkedin),
        ]:
            for sel in [
                f"input[name*='{substr}' i]",
                f"input[id*='{substr}' i]",
                f"input[placeholder*='{substr}' i]",
            ]:
                el = find_visible(driver, sel, timeout=0.5)
                if el and not el.get_attribute("value"):
                    if fill_input(driver, el, value):
                        filled += 1
                        break

        # Resume upload — try multiple selectors
        for sel in [
            "input[type=file][name*='resume' i]",
            "input[type=file][id*='resume' i]",
            "input[type=file][name*='cv' i]",
            "input[type=file]",
        ]:
            if upload_resume(driver, sel, ctx.resume_path):
                filled += 1
                logger.info("generic: resume uploaded via {}", sel)
                break

        # LLM autofill for remaining required fields
        from app.modules.applies import answer_questions, FormQuestion
        from app.modules.automation.adapters.selenium_bot import (
            extract_unfilled_questions, fill_answers,
        )

        raw_qs = extract_unfilled_questions(driver)
        if raw_qs:
            logger.info("generic[{}]: {} custom required field(s)", host, len(raw_qs))
            questions = [
                FormQuestion(
                    label=q["label"], type=q["type"], options=q.get("options") or [],
                    name=q.get("name", ""), required=q.get("required", True),
                )
                for q in raw_qs
            ]
            import asyncio
            answers = asyncio.run(answer_questions(
                questions, job_title=ctx.job_title, company_name=ctx.company,
            ))
            if answers:
                qa_pairs = [(raw_qs[i], answers[i].answer) for i in range(len(answers))]
                filled += fill_answers(driver, qa_pairs)

        time.sleep(1)
        # Submit — try clicking by visible text
        submitted = (
            click_button_by_text(driver, r"^submit application$", timeout=2)
            or click_button_by_text(driver, r"^submit$", timeout=1)
            or click_button_by_text(driver, r"^apply$", timeout=1)
        )
        if not submitted:
            return AtsResult(
                success=False,
                detail=f"generic[{host}]: no submit button found",
                ats_name=self.name,
                fields_filled=filled,
            )
        time.sleep(3)
        return AtsResult(
            success=True,
            detail=f"generic[{host}]: clicked submit ({filled} fields)",
            ats_name=self.name,
            pages=1,
            fields_filled=filled,
        )
