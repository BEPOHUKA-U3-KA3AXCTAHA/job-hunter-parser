"""Drive an external ATS URL directly — bypass LinkedIn throttle.

Usage: test_direct_url.py <url>

Loops page-filler iterations until the submit succeeds (no
"This field is required" anywhere) or 6 attempts exhausted.
"""
from __future__ import annotations

import sys
import time

from loguru import logger

from app.modules.automation.adapters.external_apply.base import (
    detect_form_errors, click_button_by_text, upload_resume,
    load_ats_context,
)
from app.modules.automation.adapters.external_apply.dispatcher import pick_handler
from app.modules.automation.services.page_filler import (
    fill_form_via_page_snapshot,
)
from app.modules.automation.adapters.selenium_bot import selenium_firefox
from app.shared.candidate_profile import CandidateProfile


def run(url: str):
    logger.warning("⏰ Browser opens in 5s")
    time.sleep(5)
    profile = CandidateProfile()
    with selenium_firefox(headless=False) as driver:
        driver.get(url)
        time.sleep(3)
        # Click landing-page Apply if present
        if click_button_by_text(driver, r"^\s*apply\b", timeout=3):
            logger.info("clicked landing-page Apply")
            time.sleep(3)
            handles = driver.window_handles
            if len(handles) > 1:
                driver.switch_to.window(handles[-1])
                time.sleep(2)

        # Resume — upload first since most ATSes need it
        ctx = load_ats_context("Aalyria", "Backend SWE", url, driver.current_url)
        for sel in ["input[type=file][name*='resume' i]",
                    "input[type=file][id*='resume' i]",
                    "input[type=file]"]:
            if upload_resume(driver, sel, ctx.resume_path):
                logger.info("resume uploaded via {}", sel)
                break

        last_errors: list[str] = []
        for attempt in range(6):
            logger.info("==== ATTEMPT {}/6 ====", attempt + 1)
            n = fill_form_via_page_snapshot(
                driver, profile.user_info or "", prior_errors=last_errors,
            )
            logger.info("attempt {}: page-filler executed {} action(s)", attempt + 1, n)
            time.sleep(1)
            submitted = click_button_by_text(
                driver,
                r"^\s*(submit application|submit|send message|send|apply)\s*$",
                timeout=3,
            )
            if submitted:
                logger.info("attempt {}: submit clicked", attempt + 1)
                time.sleep(4)
                last_errors = detect_form_errors(driver)
                if not last_errors:
                    logger.success("🎯 FORM ACCEPTED on attempt {}", attempt + 1)
                    time.sleep(5)
                    return
                logger.warning("attempt {}: validation rejected — {}",
                               attempt + 1, " | ".join(last_errors[:3])[:200])
            else:
                last_errors = detect_form_errors(driver) or [
                    "submit button still disabled — required fields missing"
                ]
                logger.warning("attempt {}: submit disabled — {}",
                               attempt + 1, " | ".join(last_errors[:3])[:200])
        logger.error("Form not submitted after 6 attempts. Last errors: {}",
                     last_errors)
        time.sleep(15)  # leave browser open for inspection


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else (
        "https://ats.rippling.com/en-GB/aalyria-careers/jobs/"
        "d8ac14cd-efdc-4688-8c7c-821685da9b2c?source=LinkedIn"
    )
    run(url)
