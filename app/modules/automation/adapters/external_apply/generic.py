"""Fallback handler for unknown ATS — uses the same Shadow-DOM walker the
Easy Apply modal uses. Best-effort: looks for any visible required form
fields, fills name / email / phone / linkedin / location heuristically,
runs the LLM autofill on what's left, attempts upload + submit.
"""
from __future__ import annotations

import re
import time
from urllib.parse import urlparse

from loguru import logger

from app.modules.automation.adapters.external_apply.base import (
    AtsContext,
    AtsResult,
    click_button_by_text,
    detect_form_errors,
    fill_input,
    find_visible,
    upload_resume,
)


class GenericHandler:
    name = "generic"

    def can_handle(self, url: str) -> bool:
        return True   # last-resort fallback

    def _fill_phone_with_country(self, driver, profile_phone: str) -> bool:
        """Fill the phone field, handling country-code combobox if present.

        ATSes like Rippling render phone as `[country combobox] [digits input]`
        and reject values that include a `+XXX` prefix mismatching the
        combobox selection. Strategy:
          1. Find the phone input (by placeholder/id/name/inputmode).
          2. Look up to ~5 ancestors for a [role=combobox] sibling — that's
             the country picker. If found, type 'Montenegro' and click the
             matching dropdown option, then drop the +382 prefix from the
             phone value.
          3. Fill the phone input with whatever is left.
        """
        from selenium.webdriver.common.by import By
        # Step 1 — locate the phone input.
        phone_el = None
        for sel in [
            "input[type=tel]",
            "input[inputmode=tel]",
            "input[name*='phone' i]",
            "input[id*='phone' i]",
            "input[placeholder*='phone' i]",
        ]:
            el = find_visible(driver, sel, timeout=0.5)
            if el and not (el.get_attribute("value") or "").strip():
                phone_el = el
                logger.info("phone: found field via {}", sel)
                break
        if not phone_el:
            logger.debug("phone: no candidate input found yet")
            return False

        digits_value = profile_phone
        # Step 2 — country combobox?
        try:
            combo = driver.execute_script(
                """
                const phone = arguments[0];
                let p = phone.parentElement;
                for (let i = 0; i < 5 && p; i++) {
                    const cands = p.querySelectorAll('[role=combobox], input[aria-haspopup=listbox]');
                    for (const c of cands) {
                        if (c === phone) continue;
                        return c;
                    }
                    p = p.parentElement;
                }
                return null;
                """,
                phone_el,
            )
        except Exception:
            combo = None
        logger.info("phone: combobox sibling = {}", "found" if combo else "none")
        # If a country combobox exists, strip the +XXX prefix from the phone
        # value regardless of whether we manage to set it — most ATSes treat
        # the bare-digit phone field + country picker as separate validators
        # and a +382 prefix in the digit field reliably fails.
        if combo and digits_value.startswith("+382"):
            digits_value = digits_value[4:]
        elif combo and digits_value.startswith("+"):
            digits_value = re.sub(r"^\+\d{1,3}", "", digits_value)

        if combo and profile_phone.startswith("+"):
            try:
                combo.click()
                time.sleep(0.3)
                combo.send_keys("Montenegro")
                time.sleep(0.8)
                # Click first visible role=option containing 'Montenegro'.
                clicked = driver.execute_script(
                    """
                    function* dn(r){const s=[r];while(s.length){const n=s.pop();if(!n)continue;
                        if(n.nodeType===1)yield n;if(n.shadowRoot)s.push(n.shadowRoot);
                        const k=n.children||n.childNodes||[];for(let i=k.length-1;i>=0;i--)s.push(k[i]);}}
                    for (const el of dn(document)) {
                        if (el.getAttribute && el.getAttribute('role') === 'option') {
                            const t = (el.textContent || '').toLowerCase();
                            if (!t.includes('montenegro')) continue;
                            const r = el.getBoundingClientRect();
                            if (r.width < 1 || r.height < 1) continue;
                            el.click();
                            return true;
                        }
                    }
                    return false;
                    """
                )
                if clicked:
                    time.sleep(0.4)
                    logger.info("generic: country set to Montenegro, phone digits='{}'", digits_value)
                else:
                    logger.info("phone: Montenegro option not found — country left at default; submitting digits anyway")
            except Exception as e:
                logger.debug("country-combobox setup failed: {}", e)

        # Step 3 — fill the phone input.
        try:
            phone_el.click()
            try:
                phone_el.clear()
            except Exception:
                pass
            phone_el.send_keys(digits_value)
            return True
        except Exception:
            return fill_input(driver, phone_el, digits_value)

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

        # Heuristic name/email/phone/location fill — matches by input
        # name/id/placeholder substring. Order matters: we want city/location
        # filled from profile BEFORE the LLM step picks it up as a "custom
        # field" and stuffs an unrelated answer (e.g. visa "No") into it.
        for substr, value in [
            ("first", ctx.profile_first_name),
            ("last", ctx.profile_last_name),
            ("email", ctx.profile_email),
            ("linkedin", ctx.profile_linkedin),
            ("city", ctx.profile_location),
            ("location", ctx.profile_location),
            ("address", ctx.profile_location),
        ]:
            if not value:
                continue
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

        # Phone — special-case because many ATSes split country code into a
        # separate combobox (Rippling, YC) and the bare phone field rejects
        # values with a +XXX prefix that doesn't match the combobox value.
        # Wait up to 4s for the phone field to paint before bailing.
        if ctx.profile_phone:
            phone_filled = False
            for _ in range(8):
                if self._fill_phone_with_country(driver, ctx.profile_phone):
                    phone_filled = True
                    filled += 1
                    break
                time.sleep(0.5)
            if not phone_filled:
                # Fallback: bare-substring fill, no country handling.
                for sel in ["input[name*='phone' i]",
                            "input[id*='phone' i]",
                            "input[placeholder*='phone' i]"]:
                    el = find_visible(driver, sel, timeout=1)
                    if el and not (el.get_attribute("value") or "").strip():
                        if fill_input(driver, el, ctx.profile_phone):
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

        # LLM autofill for remaining required fields. Up to 3 attempts:
        # ATSes often only flag missing fields after a submit attempt
        # (cross-field validation, country-mismatch, etc.) — re-extract
        # whatever's still empty and retry instead of giving up.
        from app.modules.applies import answer_questions, FormQuestion
        from app.modules.automation.adapters.selenium_bot import (
            extract_unfilled_questions, fill_answers,
        )
        import asyncio

        last_errors: list[str] = []
        for attempt in range(3):
            raw_qs = extract_unfilled_questions(driver)
            if raw_qs:
                logger.info("generic[{}] attempt {}: {} unfilled required field(s)",
                            host, attempt + 1, len(raw_qs))
                questions = [
                    FormQuestion(
                        label=q["label"], type=q["type"], options=q.get("options") or [],
                        name=q.get("name", ""), required=q.get("required", True),
                    )
                    for q in raw_qs
                ]
                answers = asyncio.run(answer_questions(
                    questions, job_title=ctx.job_title, company_name=ctx.company,
                ))
                if answers:
                    qa_pairs = [(raw_qs[i], answers[i].answer) for i in range(len(answers))]
                    filled += fill_answers(driver, qa_pairs)

            time.sleep(1)
            submitted = click_button_by_text(
                driver,
                r"^\s*(submit application|submit|send message|send|apply)\s*$",
                timeout=3,
            )
            if not submitted:
                return AtsResult(
                    success=False,
                    detail=f"generic[{host}]: no submit button found",
                    ats_name=self.name,
                    fields_filled=filled,
                )
            time.sleep(3)
            last_errors = detect_form_errors(driver)
            if not last_errors:
                break  # success
            joined = " | ".join(last_errors[:3])
            logger.warning("generic[{}] attempt {}/3 rejected: {}",
                           host, attempt + 1, joined[:200])

        if last_errors:
            joined = " | ".join(last_errors[:3])
            return AtsResult(
                success=False,
                detail=f"generic[{host}]: validation after 3 attempts: {joined[:200]}",
                ats_name=self.name,
                fields_filled=filled,
            )
        return AtsResult(
            success=True,
            detail=f"generic[{host}]: clicked submit ({filled} fields)",
            ats_name=self.name,
            pages=1,
            fields_filled=filled,
        )
