"""Ashby handler — jobs.ashbyhq.com/<co>/<uuid>."""
from __future__ import annotations

import time
from urllib.parse import urlparse

from loguru import logger

from app.modules.automation.adapters.external_apply.base import (
    AtsContext,
    AtsResult,
    click_submit,
    detect_form_errors,
    fill_input,
    find_visible,
    upload_resume,)

from app.modules.automation.ports.external_apply import AtsHandler



class AshbyHandler(AtsHandler):
    name = "ashby"

    def can_handle(self, url: str) -> bool:
        # Direct: jobs.ashbyhq.com/<co>/<uuid>
        if "ashbyhq.com" in urlparse(url).netloc.lower():
            return True
        # Embedded on a company careers page via query param,
        # e.g. https://www.foxglove.dev/careers?ashby_jid=...
        return "ashby_jid=" in url.lower() or "ashby_embed" in url.lower()

    def apply(self, driver, ctx: AtsContext) -> AtsResult:
        time.sleep(2.5)
        from selenium.webdriver.common.by import By

        # If Ashby is embedded in an iframe on a company careers page
        # (e.g. foxglove.dev/careers?ashby_jid=...), navigate directly to the
        # iframe's src — driving the iframe via switch_to is fragile when the
        # parent reloads/cookie banners interrupt.
        if "ashbyhq.com" not in driver.current_url:
            try:
                iframe = driver.find_element(By.CSS_SELECTOR, "iframe#ashby_embed_iframe, iframe[src*='ashbyhq.com']")
                src = iframe.get_attribute("src") or ""
                if src:
                    logger.info("ashby: embedded iframe → navigating to {}", src[:120])
                    driver.get(src)
                    time.sleep(3)
            except Exception as e:
                logger.debug("ashby: iframe lookup failed: {}", e)

        # Click "Apply" if we landed on the description (Ashby has a button on /).
        try:
            apply_btn = driver.find_element(By.CSS_SELECTOR, "[data-testid='apply-button'], button._apply_link, a[href*='application']")
            apply_btn.click()
            time.sleep(2)
        except Exception:
            pass

        filled = 0
        # Ashby system fields use _systemfield_ prefix; custom fields (e.g.
        # Phone Number on Foxglove) use UUID-named inputs — fall back to
        # type-based selectors when the canonical name doesn't match.
        for selectors, value in [
            (["input[name='_systemfield_name']", "input[name='name' i]"],
             f"{ctx.profile_first_name} {ctx.profile_last_name}"),
            (["input[name='_systemfield_email']", "input[type=email]"],
             ctx.profile_email),
            (["input[name='_systemfield_phone']", "input[type=tel]"],
             ctx.profile_phone),
        ]:
            if not value:
                continue
            for selector in selectors:
                el = find_visible(driver, selector, timeout=1)
                if el and not (el.get_attribute("value") or "").strip():
                    if fill_input(driver, el, value):
                        filled += 1
                        break

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
            "button.ashby-application-form-submit-button",
            "button[type=submit]",
            "[data-testid='submit-button']",
        ]):
            return AtsResult(success=False, detail="submit not found", ats_name=self.name, fields_filled=filled)

        time.sleep(3)
        # Verify the form actually accepted the submit. Ashby renders an
        # "Your form needs corrections" red banner with bullet-point errors
        # if any required field is empty/invalid — clicking submit doesn't
        # navigate away in that case.
        errors = detect_form_errors(driver)
        if errors:
            joined = " | ".join(errors[:3])
            logger.warning("ashby: form rejected by validation: {}", joined)
            return AtsResult(
                success=False,
                detail=f"validation: {joined[:200]}",
                ats_name=self.name, fields_filled=filled,
            )
        return AtsResult(success=True, detail=f"submitted ({filled} fields)", ats_name=self.name, pages=1, fields_filled=filled)
