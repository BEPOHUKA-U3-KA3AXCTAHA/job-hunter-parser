"""Workday handler — *.myworkdayjobs.com.

Workday is multi-step (Account → My Information → Resume → Voluntary
Disclosures → Self-Identify → Review → Submit). Most steps require an
account or sign-in. We don't sign in — we drop the bot at the first page
that requires an account and report 'too_many_questions' so a human can
finish manually.

This handler still tries: it'll click 'Apply' / 'Apply Manually', then
pause. Future work: persist a Workday account login in the user profile
and complete multi-step flow.
"""
from __future__ import annotations

import time
from urllib.parse import urlparse

from loguru import logger

from app.modules.automation.adapters.external_apply.base import (
    AtsContext,
    AtsResult,
    click_button_by_text,)

from app.modules.automation.ports.external_apply import AtsHandler



class WorkdayHandler(AtsHandler):
    name = "workday"

    def can_handle(self, url: str) -> bool:
        return "myworkdayjobs.com" in urlparse(url).netloc.lower()

    def apply(self, driver, ctx: AtsContext) -> AtsResult:
        time.sleep(3)
        # Workday: click Apply → Apply Manually → arrives at sign-in / new-account
        clicked_apply = click_button_by_text(driver, r"^apply\b", timeout=4)
        if clicked_apply:
            time.sleep(2)
            click_button_by_text(driver, r"apply manually", timeout=2)
            time.sleep(2)

        logger.warning(
            "workday: stopping at sign-in / multi-step page — bot does not "
            "yet handle Workday account creation. URL: {}", driver.current_url,
        )
        return AtsResult(
            success=False,
            detail="workday multi-step (account required) — handed back to user",
            ats_name=self.name,
            pages=1,
            fields_filled=0,
        )
