"""Workday handler — *.myworkdayjobs.com (Camoufox/Playwright async).

Workday is multi-step (Account → My Information → Resume → Voluntary
Disclosures → Self-Identify → Review → Submit). Most steps require an
account or sign-in. We don't sign in — we drop the bot at the first page
that requires an account and report 'too_many_questions' so a human can
finish manually.
"""
from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from loguru import logger

from app.modules.automation.adapters.external_apply.base import (
    AtsContext,
    AtsResult,
    click_button_by_text,
)
from app.modules.automation.ports.external_apply import AtsHandler


class WorkdayHandler(AtsHandler):
    name = "workday"

    def can_handle(self, url: str) -> bool:
        return "myworkdayjobs.com" in urlparse(url).netloc.lower()

    async def apply(self, page, ctx: AtsContext) -> AtsResult:
        await asyncio.sleep(3)
        clicked_apply = await click_button_by_text(page, r"^apply\b", timeout=4)
        if clicked_apply:
            await asyncio.sleep(2)
            await click_button_by_text(page, r"apply manually", timeout=2)
            await asyncio.sleep(2)

        logger.warning(
            "workday: stopping at sign-in / multi-step page — bot does not "
            "yet handle Workday account creation. URL: {}", page.url,
        )
        return AtsResult(
            success=False,
            detail="workday multi-step (account required) — handed back to user",
            ats_name=self.name,
            pages=1,
            fields_filled=0,
        )
