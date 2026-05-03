"""Lever handler — jobs.lever.co/<co>/<id>/apply."""
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



class LeverHandler(AtsHandler):
    name = "lever"

    def can_handle(self, url: str) -> bool:
        return "lever.co" in urlparse(url).netloc.lower()

    def apply(self, driver, ctx: AtsContext) -> AtsResult:
        time.sleep(2)
        # Lever's apply page lives at /<id>/apply. If we landed on the
        # job description, click "Apply for this job".
        try:
            from selenium.webdriver.common.by import By
            apply_link = driver.find_element(By.CSS_SELECTOR, "a.template-btn-submit, a[href*='/apply']")
            if apply_link:
                apply_link.click()
                time.sleep(2)
        except Exception:
            pass

        filled = 0
        # Lever uses single 'name' input + email + phone
        for selector, value in [
            ("input[name='name']", f"{ctx.profile_first_name} {ctx.profile_last_name}"),
            ("input[name='email']", ctx.profile_email),
            ("input[name='phone']", ctx.profile_phone),
            ("input[name='org']", ""),  # current company — leave empty
            ("input[name='urls[LinkedIn]']", ctx.profile_linkedin),
        ]:
            if not value:
                continue
            el = find_visible(driver, selector, timeout=1)
            if el and fill_input(driver, el, value):
                filled += 1

        if upload_resume(driver, "input[name='resume']", ctx.resume_path):
            filled += 1
            logger.info("lever: resume uploaded")
        else:
            return AtsResult(success=False, detail="resume upload failed", ats_name=self.name)

        # Custom questions — Lever wraps them in .application-question
        from app.modules.applies import answer_questions, FormQuestion
        from app.modules.automation.adapters.selenium_bot import fill_answers

        custom_qs = self._extract_lever_questions(driver)
        if custom_qs:
            logger.info("lever: {} custom required field(s)", len(custom_qs))
            questions = [
                FormQuestion(
                    label=q["label"], type=q["type"], options=q.get("options") or [],
                    name=q.get("name", ""), required=q.get("required", True),
                )
                for q in custom_qs
            ]
            import asyncio
            answers = asyncio.run(answer_questions(
                questions, job_title=ctx.job_title, company_name=ctx.company,
            ))
            if answers:
                qa_pairs = [(custom_qs[i], answers[i].answer) for i in range(len(answers))]
                n = fill_answers(driver, qa_pairs)
                filled += n
                logger.info("lever: LLM filled {}/{}", n, len(answers))

        time.sleep(1)
        if not click_submit(driver, [
            "button[type=submit][data-qa='btn-submit']",
            "button[type=submit]",
            ".template-btn-submit",
        ]):
            return AtsResult(success=False, detail="submit not found", ats_name=self.name, fields_filled=filled)

        time.sleep(3)
        errors = detect_form_errors(driver)
        if errors:
            joined = " | ".join(errors[:3])
            logger.warning("lever: form rejected: {}", joined[:200])
            return AtsResult(
                success=False,
                detail=f"validation: {joined[:200]}",
                ats_name=self.name, fields_filled=filled,
            )
        return AtsResult(success=True, detail=f"submitted ({filled} fields)", ats_name=self.name, pages=1, fields_filled=filled)

    def _extract_lever_questions(self, driver) -> list[dict]:
        js = """
            const out = [];
            for (const q of document.querySelectorAll('.application-question, [data-qa^="question"]')) {
                const lbl = q.querySelector('label, .application-label');
                const labelText = lbl ? (lbl.textContent || '').trim() : '';
                const inp = q.querySelector('input[type=text], input[type=tel], input[type=email], input:not([type]), textarea, select');
                if (!inp) continue;
                if (inp.value && inp.value.trim()) continue;
                const required = q.classList.contains('required-q') ||
                                 inp.required || labelText.includes('✱') || labelText.includes('*');
                if (!required) continue;
                let type = inp.tagName.toLowerCase() === 'select' ? 'select' : (inp.type || 'text');
                let options = [];
                if (type === 'select') {
                    options = Array.from(inp.options).map(o => o.text.trim()).filter(t => t && !/^select|choose/i.test(t));
                }
                const id = inp.id || '';
                out.push({
                    label: labelText, type, options,
                    name: inp.name || id,
                    required: true,
                    _selector: id ? '#' + id : (inp.name ? `[name="${inp.name}"]` : ''),
                });
            }
            return out;
        """
        try:
            return driver.execute_script(js) or []
        except Exception:
            return []
