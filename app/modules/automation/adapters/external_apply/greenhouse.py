"""Greenhouse handler — boards.greenhouse.io/<co>/jobs/<id> (Camoufox/Playwright).

Form structure is consistent across companies:
  #first_name, #last_name, #email, #phone, optional #location
  #resume (file input)
  #cover_letter (textarea, optional unless required asterisk)
  custom questions in <div class='question'> with various input types
  submit: input[type=submit][value='Submit Application']
"""
from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from loguru import logger

from app.modules.automation.adapters.external_apply.base import (
    AtsContext,
    AtsResult,
    click_submit,
    detect_form_errors,
    fill_input,
    upload_resume,
)
from app.modules.automation.ports.external_apply import AtsHandler


class GreenhouseHandler(AtsHandler):
    name = "greenhouse"

    def can_handle(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        return "greenhouse.io" in host or "boards-api.greenhouse.io" in host

    async def apply(self, page, ctx: AtsContext) -> AtsResult:
        await asyncio.sleep(2.5)  # let the form paint

        # Some Greenhouse pages embed the form via iframe — switch frame if so
        try:
            iframe_loc = page.locator(
                "iframe#grnhse_iframe, iframe[src*='greenhouse']"
            ).first
            if await iframe_loc.count() > 0:
                # In Playwright, switch to iframe = use frame_locator
                # Subsequent operations should target page.frame_locator(...).
                # For now log + let page operations work on whatever's reachable.
                logger.info("greenhouse: iframe detected (frame switching not wired)")
        except Exception:
            pass

        filled = 0
        for selector, value in [
            ("#first_name", ctx.profile_first_name),
            ("#last_name", ctx.profile_last_name),
            ("#email", ctx.profile_email),
            ("#phone", ctx.profile_phone),
        ]:
            if await fill_input(page, selector, value):
                filled += 1

        # Resume upload (always required on Greenhouse)
        if await upload_resume(
            page, "#resume, input[type=file][name='resume']", ctx.resume_path,
        ):
            filled += 1
            logger.info("greenhouse: resume uploaded")
        else:
            return AtsResult(
                success=False, detail="resume upload failed", ats_name=self.name,
            )

        # Custom required questions — page-snapshot Claude pass.
        from app.modules.automation.adapters.page_filler import (
            fill_form_via_page_snapshot,
        )
        from app.modules.users import CandidateProfile
        profile = CandidateProfile()
        done = await fill_form_via_page_snapshot(page, profile.user_info or "")
        filled += done

        await asyncio.sleep(1.0)
        if not await click_submit(page, [
            "input[type=submit][value*='Submit']",
            "button[type=submit]",
            "button.application--submit",
        ]):
            return AtsResult(
                success=False, detail="submit button not found",
                ats_name=self.name, fields_filled=filled,
            )

        await asyncio.sleep(3.0)
        errors = await detect_form_errors(page)
        if errors:
            joined = " | ".join(errors[:3])
            logger.warning("greenhouse: form rejected: {}", joined[:200])
            return AtsResult(
                success=False, detail=f"validation: {joined[:200]}",
                ats_name=self.name, fields_filled=filled,
            )
        return AtsResult(
            success=True, detail=f"submitted with {filled} fields",
            ats_name=self.name, pages=1, fields_filled=filled,
        )
