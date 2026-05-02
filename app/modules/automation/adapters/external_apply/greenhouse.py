"""Greenhouse handler — boards.greenhouse.io/<co>/jobs/<id>.

Form structure is consistent across companies:
  #first_name, #last_name, #email, #phone, optional #location
  #resume (file input)
  #cover_letter (textarea, optional unless required asterisk)
  custom questions in <div class='question'> with various input types
  submit: input[type=submit][value='Submit Application']
"""
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
    upload_resume,
)


class GreenhouseHandler:
    name = "greenhouse"

    def can_handle(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        return "greenhouse.io" in host or "boards-api.greenhouse.io" in host

    def apply(self, driver, ctx: AtsContext) -> AtsResult:
        time.sleep(2.5)  # let the form paint

        # Some Greenhouse pages embed the form via iframe — switch in if so
        try:
            iframes = driver.find_elements("css selector", "iframe#grnhse_iframe, iframe[src*='greenhouse']")
            if iframes:
                driver.switch_to.frame(iframes[0])
                logger.info("greenhouse: switched into iframe")
                time.sleep(1.5)
        except Exception:
            pass

        filled = 0
        for selector, value in [
            ("#first_name", ctx.profile_first_name),
            ("#last_name", ctx.profile_last_name),
            ("#email", ctx.profile_email),
            ("#phone", ctx.profile_phone),
        ]:
            el = find_visible(driver, selector, timeout=1)
            if el and fill_input(driver, el, value):
                filled += 1

        # Resume upload (always required on Greenhouse)
        if upload_resume(driver, "#resume, input[type=file][name='resume']", ctx.resume_path):
            filled += 1
            logger.info("greenhouse: resume uploaded")
        else:
            return AtsResult(success=False, detail="resume upload failed", ats_name=self.name)

        # Run the LLM autofill for any custom required questions still empty
        from app.modules.automation.adapters.selenium_bot import (
            extract_unfilled_questions, fill_answers,
        )
        from app.modules.applies import answer_questions, FormQuestion

        raw_qs = extract_unfilled_questions(driver)
        # Greenhouse questions live OUTSIDE [role='dialog'] so the extractor's
        # in-dialog filter rejects them. Fall back to a simple selector pass.
        if not raw_qs:
            raw_qs = self._extract_greenhouse_fields(driver)
        if raw_qs:
            logger.info("greenhouse: {} custom required field(s)", len(raw_qs))
            questions = [
                FormQuestion(
                    label=q["label"], type=q["type"], options=q.get("options") or [],
                    name=q.get("name", ""), placeholder=q.get("placeholder", ""),
                    required=q.get("required", True),
                )
                for q in raw_qs
            ]
            import asyncio
            answers = asyncio.run(answer_questions(
                questions, job_title=ctx.job_title, company_name=ctx.company,
            ))
            if answers:
                qa_pairs = [(raw_qs[i], answers[i].answer) for i in range(len(answers))]
                n = fill_answers(driver, qa_pairs)
                filled += n
                logger.info("greenhouse: LLM filled {}/{} custom fields", n, len(answers))

        time.sleep(1.0)
        if not click_submit(driver, [
            "input[type=submit][value*='Submit']",
            "button[type=submit]",
            "button.application--submit",
        ]):
            return AtsResult(success=False, detail="submit button not found", ats_name=self.name, fields_filled=filled)

        time.sleep(3.0)
        errors = detect_form_errors(driver)
        if errors:
            joined = " | ".join(errors[:3])
            logger.warning("greenhouse: form rejected: {}", joined[:200])
            return AtsResult(
                success=False,
                detail=f"validation: {joined[:200]}",
                ats_name=self.name, fields_filled=filled,
            )
        return AtsResult(success=True, detail=f"submitted with {filled} fields", ats_name=self.name, pages=1, fields_filled=filled)

    def _extract_greenhouse_fields(self, driver) -> list[dict]:
        """Greenhouse-specific question extractor: <div class='question'> wrappers."""
        js = """
            const out = [];
            for (const q of document.querySelectorAll('div.question, .field')) {
                const lbl = q.querySelector('label');
                const labelText = lbl ? (lbl.textContent || '').trim() : '';
                const inp = q.querySelector('input[type=text], input[type=tel], input[type=email], input:not([type]), textarea, select');
                if (!inp) continue;
                if (inp.value && inp.value.trim()) continue;
                const required = q.classList.contains('required') ||
                                 inp.required || (lbl && lbl.textContent.includes('*'));
                if (!required) continue;
                let type = inp.tagName.toLowerCase();
                let options = [];
                if (type === 'select') {
                    options = Array.from(inp.options).map(o => o.text.trim()).filter(t => t && !/^select|choose|please/i.test(t));
                } else if (inp.type === 'radio') {
                    type = 'radio';
                } else if (type === 'input') {
                    type = inp.type || 'text';
                }
                const id = inp.id || '';
                out.push({
                    label: labelText, type, options,
                    name: inp.name || id,
                    placeholder: inp.placeholder || '',
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
