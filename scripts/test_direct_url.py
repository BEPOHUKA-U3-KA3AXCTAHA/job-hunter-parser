"""Drive an external ATS URL directly — bypass LinkedIn throttle.

Usage: test_direct_url.py <url>

Loops page-filler iterations until the submit succeeds or 6 attempts
exhausted. Auto-bypasses Cloudflare Turnstile if it pops up after submit.
"""
from __future__ import annotations

import re
import sys
import time

from loguru import logger

from app.modules.automation.adapters.external_apply.base import (
    detect_form_errors, click_button_by_text, upload_resume,
    load_ats_context,
)
from app.modules.automation.adapters.page_filler import (
    fill_form_via_page_snapshot,
)
from app.modules.automation.adapters.selenium_bot import (
    selenium_firefox, stealth_navigate, has_cloudflare_challenge,
    solve_cloudflare_checkbox,
)
from app.modules.users.models.candidate_profile import CandidateProfile


def _looks_like_success(driver) -> bool:
    """Heuristic: post-submit success usually shows a 'thank you' / 'we
    received your application' / 'application submitted' panel and either
    navigates away from the form URL or clears the form fields."""
    try:
        body = driver.execute_script("return document.body.innerText || '';") or ""
    except Exception:
        return False
    return bool(re.search(
        r"thanks for applying|thank you for applying|application (submitted|received|sent)|"
        r"we(?:'| ?ha)ve received your application|successfully submitted|"
        r"we'?ll be in touch|we will review",
        body, re.I,
    ))


def run(url: str):
    logger.warning("⏰ Browser opens in 5s")
    time.sleep(5)
    profile = CandidateProfile()
    with selenium_firefox(headless=False) as driver:
        stealth_navigate(driver, url)
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
            # If Turnstile widget is on the form (Rippling renders it
            # PRE-submit, not after), clear it before clicking Apply or the
            # button stays in a 'verifying...' state forever.
            if has_cloudflare_challenge(driver):
                logger.warning("🛑 Turnstile/Cloudflare widget detected pre-submit — solving")
                solve_cloudflare_checkbox(driver, max_wait_s=30)
                time.sleep(2)
            submitted = click_button_by_text(
                driver,
                r"^\s*(submit application|submit|send message|send|apply)\s*$",
                timeout=3,
            )
            if submitted:
                logger.info("attempt {}: submit clicked", attempt + 1)
                time.sleep(4)
                # Cloudflare Turnstile pops up AFTER submit on some ATSes —
                # try to clear it automatically (real browser fingerprint
                # from the user's profile + Turnstile checkbox click).
                if has_cloudflare_challenge(driver):
                    logger.warning("🛑 Cloudflare challenge detected — attempting auto-bypass")
                    cleared = solve_cloudflare_checkbox(driver, max_wait_s=30)
                    if cleared:
                        logger.info("✅ challenge cleared, waiting for form result")
                        time.sleep(4)
                    else:
                        logger.warning("⚠️ challenge still present — re-clicking submit")
                        click_button_by_text(
                            driver,
                            r"^\s*(submit application|submit|send message|send|apply)\s*$",
                            timeout=3,
                        )
                        time.sleep(4)
                last_errors = detect_form_errors(driver)
                if not last_errors and _looks_like_success(driver):
                    logger.success("🎯 FORM ACCEPTED on attempt {}", attempt + 1)
                    time.sleep(8)
                    return
                if not last_errors:
                    try:
                        driver.save_screenshot(f"/tmp/jhp_diag/aalyria_after_submit_{attempt+1}.png")
                        logger.info("screenshot saved /tmp/jhp_diag/aalyria_after_submit_{}.png", attempt + 1)
                    except Exception:
                        pass
                    logger.info(
                        "attempt {}: no validation errors visible — checking "
                        "page for success/cloudflare markers",
                        attempt + 1,
                    )
                    time.sleep(15)  # leave open so user / next iteration can see
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
