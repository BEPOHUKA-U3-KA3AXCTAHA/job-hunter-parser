"""Ashby handler — jobs.ashbyhq.com/<co>/<uuid> (Camoufox/Playwright)."""
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


class AshbyHandler(AtsHandler):
    name = "ashby"

    def can_handle(self, url: str) -> bool:
        if "ashbyhq.com" in urlparse(url).netloc.lower():
            return True
        return "ashby_jid=" in url.lower() or "ashby_embed" in url.lower()

    async def apply(self, page, ctx: AtsContext) -> AtsResult:
        await asyncio.sleep(2.5)

        # If Ashby is embedded in an iframe on a company careers page,
        # navigate directly to the iframe's src — driving the iframe is
        # fragile when the parent reloads.
        if "ashbyhq.com" not in page.url:
            try:
                iframe_loc = page.locator(
                    "iframe#ashby_embed_iframe, iframe[src*='ashbyhq.com']"
                ).first
                if await iframe_loc.count() > 0:
                    src = await iframe_loc.get_attribute("src")
                    if src:
                        logger.info("ashby: embedded iframe → navigating to {}", src[:120])
                        await page.goto(src, wait_until="domcontentloaded")
                        await asyncio.sleep(3)
            except Exception as e:
                logger.debug("ashby: iframe lookup failed: {}", e)

        # Click "Apply" if we landed on the description.
        try:
            apply_btn = page.locator(
                "[data-testid='apply-button'], button._apply_link, "
                "a[href*='application']"
            ).first
            if await apply_btn.count() > 0 and await apply_btn.is_visible():
                await apply_btn.click()
                await asyncio.sleep(2)
        except Exception:
            pass

        filled = 0
        for selectors, value in [
            (["input[name='_systemfield_name']", "input[name='name' i]"],
             f"{ctx.profile_first_name} {ctx.profile_last_name}".strip()),
            (["input[name='_systemfield_email']", "input[type=email]"],
             ctx.profile_email),
            (["input[name='_systemfield_phone']", "input[type=tel]"],
             ctx.profile_phone),
        ]:
            if not value:
                continue
            for selector in selectors:
                if await fill_input(page, selector, value):
                    filled += 1
                    break

        if await upload_resume(
            page,
            "input[type=file][name='_systemfield_resume'], input[type=file]",
            ctx.resume_path,
        ):
            filled += 1
            logger.info("ashby: resume uploaded")
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

        await asyncio.sleep(1)
        if not await click_submit(page, [
            "button.ashby-application-form-submit-button",
            "button[type=submit]",
            "[data-testid='submit-button']",
        ]):
            return AtsResult(
                success=False, detail="submit not found",
                ats_name=self.name, fields_filled=filled,
            )

        await asyncio.sleep(3)
        errors = await detect_form_errors(page)
        if errors:
            joined = " | ".join(errors[:3])
            logger.warning("ashby: form rejected by validation: {}", joined)
            return AtsResult(
                success=False, detail=f"validation: {joined[:200]}",
                ats_name=self.name, fields_filled=filled,
            )
        return AtsResult(
            success=True, detail=f"submitted ({filled} fields)",
            ats_name=self.name, pages=1, fields_filled=filled,
        )
