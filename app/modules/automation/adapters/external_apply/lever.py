"""Lever handler — jobs.lever.co/<co>/<id>/apply (Camoufox/Playwright async)."""
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


class LeverHandler(AtsHandler):
    name = "lever"

    def can_handle(self, url: str) -> bool:
        return "lever.co" in urlparse(url).netloc.lower()

    async def apply(self, page, ctx: AtsContext) -> AtsResult:
        await asyncio.sleep(2)
        # Lever's apply page lives at /<id>/apply. If we landed on the
        # job description, click the apply CTA.
        try:
            apply_link = page.locator(
                "a.template-btn-submit, a[href*='/apply']"
            ).first
            if await apply_link.count() > 0 and await apply_link.is_visible():
                await apply_link.click()
                await asyncio.sleep(2)
        except Exception:
            pass

        filled = 0
        full_name = f"{ctx.profile_first_name} {ctx.profile_last_name}".strip()
        for selector, value in [
            ("input[name='name']", full_name),
            ("input[name='email']", ctx.profile_email),
            ("input[name='phone']", ctx.profile_phone),
            ("input[name='urls[LinkedIn]']", ctx.profile_linkedin),
        ]:
            if not value:
                continue
            if await fill_input(page, selector, value):
                filled += 1

        if await upload_resume(page, "input[name='resume']", ctx.resume_path):
            filled += 1
            logger.info("lever: resume uploaded")
        else:
            return AtsResult(
                success=False, detail="resume upload failed", ats_name=self.name,
            )

        # Custom required questions — fall through to page-snapshot Claude.
        from app.modules.automation.adapters.page_filler import (
            fill_form_via_page_snapshot,
        )
        from app.modules.users import CandidateProfile
        profile = CandidateProfile()
        done = await fill_form_via_page_snapshot(page, profile.user_info or "")
        filled += done

        await asyncio.sleep(1)
        if not await click_submit(page, [
            "button[type=submit][data-qa='btn-submit']",
            "button[type=submit]",
            ".template-btn-submit",
        ]):
            return AtsResult(
                success=False, detail="submit not found",
                ats_name=self.name, fields_filled=filled,
            )

        await asyncio.sleep(3)
        errors = await detect_form_errors(page)
        if errors:
            joined = " | ".join(errors[:3])
            logger.warning("lever: form rejected: {}", joined[:200])
            return AtsResult(
                success=False, detail=f"validation: {joined[:200]}",
                ats_name=self.name, fields_filled=filled,
            )
        return AtsResult(
            success=True, detail=f"submitted ({filled} fields)",
            ats_name=self.name, pages=1, fields_filled=filled,
        )
