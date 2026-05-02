"""Ashby handler — jobs.ashbyhq.com/<co>/<uuid>."""
from __future__ import annotations

import time
from urllib.parse import urlparse

from loguru import logger

from app.modules.automation.adapters.external_apply.base import (
    AtsContext,
    AtsResult,
    click_submit,
    fill_input,
    find_visible,
    upload_resume,
)


class AshbyHandler:
    name = "ashby"

    def can_handle(self, url: str) -> bool:
        return "ashbyhq.com" in urlparse(url).netloc.lower()

    def apply(self, driver, ctx: AtsContext) -> AtsResult:
        time.sleep(2.5)
        # Click "Apply" if we landed on the description (Ashby has a button on /).
        from selenium.webdriver.common.by import By
        try:
            apply_btn = driver.find_element(By.CSS_SELECTOR, "[data-testid='apply-button'], button._apply_link, a[href*='application']")
            apply_btn.click()
            time.sleep(2)
        except Exception:
            pass

        filled = 0
        # Ashby system fields use _systemfield_ prefix
        for selector, value in [
            ("input[name='_systemfield_name']", f"{ctx.profile_first_name} {ctx.profile_last_name}"),
            ("input[name='_systemfield_email']", ctx.profile_email),
            ("input[name='_systemfield_phone']", ctx.profile_phone),
        ]:
            el = find_visible(driver, selector, timeout=1)
            if el and fill_input(driver, el, value):
                filled += 1

        if upload_resume(driver, "input[type=file][name='_systemfield_resume'], input[type=file]", ctx.resume_path):
            filled += 1
            logger.info("ashby: resume uploaded")
        else:
            return AtsResult(success=False, detail="resume upload failed", ats_name=self.name)

        # Custom questions
        from app.modules.applies import answer_questions, FormQuestion
        from app.modules.automation.adapters.selenium_bot import (
            extract_unfilled_questions, fill_answers,
        )

        raw_qs = extract_unfilled_questions(driver)
        if raw_qs:
            logger.info("ashby: {} custom required field(s)", len(raw_qs))
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
        if not click_submit(driver, [
            "button[type=submit]",
            "[data-testid='submit-button']",
        ]):
            return AtsResult(success=False, detail="submit not found", ats_name=self.name, fields_filled=filled)

        time.sleep(3)
        return AtsResult(success=True, detail=f"submitted ({filled} fields)", ats_name=self.name, pages=1, fields_filled=filled)
