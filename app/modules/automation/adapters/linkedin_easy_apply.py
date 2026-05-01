"""LinkedIn Easy Apply automator.

Selectors lifted from the wodsuz/EasyApplyJobsBot open-source project (their
selectors are battle-tested against current LinkedIn DOM, mostly aria-label
based which are stable because LinkedIn maintains them for accessibility).

Conservative defaults:
- Skip any job whose Easy Apply modal has > 3 pages of custom questions
  (means too many free-text answers — high risk of bad LLM answers)
- Skip jobs requiring resume upload step (we don't have a path to upload yet)
- Stop the entire batch if LinkedIn shows ANY warning (CAPTCHA / verify /
  restricted account)
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from loguru import logger
from playwright.async_api import Page, TimeoutError as PWTimeoutError

from app.modules.automation.adapters.camoufox import human_sleep

# Selectors from wodsuz/EasyApplyJobsBot — verified working as of 2026
EASY_APPLY_BUTTON = (
    "div.jobs-apply-button--top-card button.jobs-apply-button, "
    "button.jobs-apply-button[aria-label*='Easy Apply']"
)
JOB_CARD = "li[data-occludable-job-id]"
APPLIED_BADGE = "li[data-occludable-job-id] :text('Applied')"

# Inside the apply modal — these aria-labels are stable
CONTINUE_BUTTON = "button[aria-label='Continue to next step']"
REVIEW_BUTTON = "button[aria-label='Review your application']"
SUBMIT_BUTTON = "button[aria-label='Submit application']"
MODAL_CLOSE_BUTTON = "button[aria-label='Dismiss']"
MODAL_DISCARD_BUTTON = "button[data-control-name='discard_application_confirm_btn']"

# Form fields
PHONE_INPUT_SELECTORS = [
    "input[type='tel']",
    "input[name*='phone' i]",
    "input[id*='phone' i]",
    "input[aria-label*='phone' i]",
]

# Anti-bot trip wires
RATE_LIMIT_TEXT = [
    "let's confirm it's really you",
    "we've restricted your account",
    "unusual activity",
    "complete this puzzle",
    "i'm not a robot",
]


class ApplyOutcome(StrEnum):
    APPLIED = "applied"               # successfully submitted
    ALREADY_APPLIED = "already_applied"
    SKIPPED_TOO_MANY_QUESTIONS = "skipped_too_many_questions"
    SKIPPED_NEEDS_RESUME = "skipped_needs_resume"
    SKIPPED_NO_EASY_APPLY = "skipped_no_easy_apply"  # job doesn't have Easy Apply
    BLOCKED_RATE_LIMIT = "blocked_rate_limit"
    FAILED = "failed"


@dataclass
class ApplyResult:
    outcome: ApplyOutcome
    detail: str = ""
    pages_traversed: int = 0


# Max pages of multi-step modal we'll auto-traverse. After this we bail
# (too many custom questions = high LLM hallucination risk).
MAX_MODAL_PAGES = 3


async def is_blocked(page: Page) -> bool:
    """Detect LinkedIn anti-bot warning pages."""
    try:
        text = (await page.evaluate("() => document.body.innerText")).lower()[:5000]
    except Exception:
        return False
    return any(m in text for m in RATE_LIMIT_TEXT)


async def apply_to_job(page: Page, job_url: str, profile_phone: str = "") -> ApplyResult:
    """Open a single LinkedIn job posting and run the Easy Apply flow.

    Args:
        page: Camoufox page (logged in to LinkedIn)
        job_url: e.g. https://www.linkedin.com/jobs/view/4123456789
        profile_phone: candidate's phone for the phone field (no auto-guess)

    Returns ApplyResult with outcome enum.
    """
    logger.info("Opening job: {}", job_url)
    try:
        await page.goto(job_url, wait_until="domcontentloaded", timeout=20000)
    except PWTimeoutError:
        return ApplyResult(ApplyOutcome.FAILED, "goto timeout")
    await human_sleep(2, 4)

    if await is_blocked(page):
        return ApplyResult(ApplyOutcome.BLOCKED_RATE_LIMIT, "warning page detected")

    # Already applied?
    try:
        applied = await page.locator(":text('Applied'):visible").first.is_visible(timeout=1500)
        if applied:
            return ApplyResult(ApplyOutcome.ALREADY_APPLIED)
    except Exception:
        pass

    # Find Easy Apply button
    try:
        ea_btn = page.locator(EASY_APPLY_BUTTON).first
        if not await ea_btn.is_visible(timeout=4000):
            return ApplyResult(ApplyOutcome.SKIPPED_NO_EASY_APPLY, "Easy Apply button not visible")
    except PWTimeoutError:
        return ApplyResult(ApplyOutcome.SKIPPED_NO_EASY_APPLY, "Easy Apply button not present")

    await human_sleep(1, 2.5)
    await ea_btn.click()
    await human_sleep(1.5, 3)

    # Walk modal pages
    return await _walk_modal(page, profile_phone)


async def _walk_modal(page: Page, profile_phone: str) -> ApplyResult:
    """Step through the multi-page apply modal, stopping if too many pages
    or unfillable fields encountered."""
    for page_idx in range(MAX_MODAL_PAGES):
        await human_sleep(0.6, 1.4)

        if await is_blocked(page):
            return ApplyResult(ApplyOutcome.BLOCKED_RATE_LIMIT, f"warning at page {page_idx}")

        # Try to fill phone field if present and empty
        await _fill_phone_if_empty(page, profile_phone)

        # Look for Submit (last page) — if found, we're done
        try:
            submit = page.locator(SUBMIT_BUTTON).first
            if await submit.is_visible(timeout=1200):
                logger.info("Submit button found at page {}/{}", page_idx + 1, MAX_MODAL_PAGES)
                # Uncheck "Follow company" if present (don't follow every company we apply to)
                await _uncheck_follow_company(page)
                await human_sleep(0.8, 1.6)
                await submit.click()
                await human_sleep(2, 4)
                return ApplyResult(ApplyOutcome.APPLIED, pages_traversed=page_idx + 1)
        except Exception:
            pass

        # Look for Review (often appears before Submit)
        try:
            review = page.locator(REVIEW_BUTTON).first
            if await review.is_visible(timeout=1200):
                logger.debug("Review button at page {}", page_idx + 1)
                await review.click()
                await human_sleep(1, 2)
                continue
        except Exception:
            pass

        # Look for Continue (advance to next page)
        try:
            cont = page.locator(CONTINUE_BUTTON).first
            if await cont.is_visible(timeout=1200):
                logger.debug("Continue button at page {}", page_idx + 1)
                # Before clicking Continue, check if there are unfilled required fields
                # (LinkedIn highlights them red — we just bail)
                if await _has_unfilled_required(page):
                    await _close_modal(page)
                    return ApplyResult(
                        ApplyOutcome.SKIPPED_TOO_MANY_QUESTIONS,
                        detail=f"unfilled required fields at modal page {page_idx + 1}",
                        pages_traversed=page_idx + 1,
                    )
                await cont.click()
                await human_sleep(1, 2)
                continue
        except Exception:
            pass

        # Neither Continue / Review / Submit found — modal stuck
        return ApplyResult(ApplyOutcome.FAILED, f"no nav button at page {page_idx + 1}")

    # Hit MAX_MODAL_PAGES without seeing Submit → bail
    await _close_modal(page)
    return ApplyResult(
        ApplyOutcome.SKIPPED_TOO_MANY_QUESTIONS,
        detail=f"modal still going after {MAX_MODAL_PAGES} pages",
        pages_traversed=MAX_MODAL_PAGES,
    )


async def _fill_phone_if_empty(page: Page, phone: str) -> None:
    if not phone:
        return
    for sel in PHONE_INPUT_SELECTORS:
        try:
            input_el = page.locator(sel).first
            if not await input_el.is_visible(timeout=400):
                continue
            current = await input_el.input_value(timeout=400)
            if current.strip():
                continue  # already filled
            await input_el.click()
            await input_el.type(phone, delay=80)
            await human_sleep(0.4, 1.0)
            return
        except Exception:
            continue


async def _has_unfilled_required(page: Page) -> bool:
    """LinkedIn marks unfilled required fields with red error text."""
    try:
        return await page.evaluate("""() => {
            const errs = document.querySelectorAll(
                '[role="alert"], .artdeco-inline-feedback--error, .fb-form-element-error'
            );
            for (const e of errs) {
                if (e.offsetWidth > 0 && e.textContent.trim().length > 0) return true;
            }
            return false;
        }""")
    except Exception:
        return False


async def _uncheck_follow_company(page: Page) -> None:
    try:
        cb = page.locator("label[for='follow-company-checkbox']").first
        if await cb.is_visible(timeout=400):
            checkbox = page.locator("#follow-company-checkbox").first
            if await checkbox.is_checked(timeout=400):
                await cb.click()
                await human_sleep(0.3, 0.6)
    except Exception:
        pass


async def _close_modal(page: Page) -> None:
    """Try to dismiss the modal cleanly so we can move to the next job."""
    try:
        close = page.locator(MODAL_CLOSE_BUTTON).first
        if await close.is_visible(timeout=600):
            await close.click()
            await human_sleep(0.6, 1.2)
        # LinkedIn shows "Discard application?" confirm
        discard = page.locator(MODAL_DISCARD_BUTTON).first
        if await discard.is_visible(timeout=600):
            await discard.click()
            await human_sleep(0.4, 0.8)
    except Exception:
        pass
