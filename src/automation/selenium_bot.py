"""Selenium-driven Firefox bot for LinkedIn Easy Apply.

Why Selenium and not Camoufox/Playwright:
- Camoufox got soft-blocked on /jobs/view/ pages (LinkedIn served guest wall)
- Selenium with USER'S real Firefox profile = real fingerprint, real cookies,
  real history → LinkedIn sees a normal user

Strategy:
1. Copy user's Firefox profile to /tmp (avoid conflict with running Firefox)
2. Strip Marionette automation markers from preferences
3. Launch headless Firefox via Selenium with that profile
4. Navigate, smart text-based button find, click, walk modal
"""
from __future__ import annotations

import re
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from random import lognormvariate, uniform

from loguru import logger
from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.support.ui import WebDriverWait

from src.automation.firefox_cookies import find_default_profile

PROFILE_COPY_DIR = Path("/tmp/jhp_ff_profile")
DIAG_DIR = Path("/tmp/jhp_diag")

MAX_MODAL_PAGES = 4
RATE_LIMIT_MARKERS = [
    "let's confirm it's really you",
    "we've restricted your account",
    "unusual activity",
    "complete this puzzle",
]


class ApplyOutcome(StrEnum):
    APPLIED = "applied"                   # full Easy Apply submitted
    INTEREST_SIGNALED = "interest_signaled"  # "I'm interested" clicked (LinkedIn's soft-signal)
    ALREADY_APPLIED = "already_applied"
    NO_EASY_APPLY = "no_easy_apply"       # neither Easy Apply nor I'm interested
    TOO_MANY_QUESTIONS = "too_many_questions"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass
class ApplyResult:
    outcome: ApplyOutcome
    detail: str = ""
    pages: int = 0


def human_sleep(min_s: float = 0.6, max_s: float = 2.4) -> None:
    midpoint = (min_s + max_s) / 2
    sigma = (max_s - min_s) / 4
    delay = lognormvariate(0, 0.4) * midpoint
    delay = max(min_s, min(max_s, delay))
    delay += uniform(-sigma * 0.2, sigma * 0.2)
    time.sleep(max(0.05, delay))


def _prepare_profile_copy() -> Path:
    """Clone user's Firefox profile to /tmp so we don't conflict with their
    running Firefox. Strip Marionette / automation hints from prefs.
    """
    src = find_default_profile()
    if PROFILE_COPY_DIR.exists():
        shutil.rmtree(PROFILE_COPY_DIR)
    logger.info("Copying Firefox profile {} → {}", src.name, PROFILE_COPY_DIR)
    # Use ignore for big lock files / cache
    shutil.copytree(
        src, PROFILE_COPY_DIR,
        ignore=shutil.ignore_patterns(
            "lock", "parent.lock", ".parentlock",
            "cache2", "thumbnails", "startupCache",
            "minidumps", "datareporting", "saved-telemetry-pings",
            "shader-cache", "AlternateServices.bin",
        ),
    )
    # Make sure user.js doesn't disable cookies
    user_js = PROFILE_COPY_DIR / "user.js"
    user_js.write_text(
        # Disable Firefox automation banners and any "this profile is opened by another Firefox" check
        'user_pref("datareporting.healthreport.uploadEnabled", false);\n'
        'user_pref("dom.disable_open_during_load", false);\n'
        'user_pref("dom.webdriver.enabled", false);\n'
        'user_pref("useAutomationExtension", false);\n'
        'user_pref("browser.startup.homepage_override.mstone", "ignore");\n'
        'user_pref("browser.startup.page", 0);\n'
    )
    return PROFILE_COPY_DIR


@contextmanager
def selenium_firefox(headless: bool = False, copy_profile: bool = True):
    """Launch Selenium Firefox with user's profile (copied) for LinkedIn auth.

    Args:
        headless: True to run without window (some sites detect headless,
                  but for LinkedIn it generally works)
        copy_profile: True (recommended) → copy user's profile to /tmp
                      False → use a fresh empty profile (no LinkedIn auth)
    """
    options = FirefoxOptions()
    if headless:
        options.add_argument("--headless")
    if copy_profile:
        profile_path = str(_prepare_profile_copy())
        options.add_argument("-profile")
        options.add_argument(profile_path)
    # Hide automation flag (Selenium sets navigator.webdriver=true by default)
    options.set_preference("dom.webdriver.enabled", False)
    options.set_preference("useAutomationExtension", False)

    service = FirefoxService(log_output=str(Path("/tmp/jhp_geckodriver.log")))
    driver = webdriver.Firefox(options=options, service=service)
    driver.implicitly_wait(0)  # we'll handle waits explicitly
    try:
        yield driver
    finally:
        try:
            driver.quit()
        except Exception:
            pass


# --- Diagnostics ---

def _diag_save(driver, tag: str) -> None:
    """Best-effort screenshot + html dump for failure diagnostics."""
    try:
        DIAG_DIR.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", tag)[:40]
        png = DIAG_DIR / f"{ts}_{safe}.png"
        html = DIAG_DIR / f"{ts}_{safe}.html"
        driver.save_screenshot(str(png))
        try:
            html.write_text(driver.page_source[:300_000])
        except Exception:
            pass
        logger.warning("diag saved: {}", png.name)
    except Exception as e:
        logger.debug("diag save failed: {}", e)


# --- DOM helpers (text-based, not class-based) ---

def find_button_by_text(driver, text_regex: str, timeout: float = 4.0):
    """Find first VISIBLE clickable element (button OR anchor with role=button)
    whose text or aria-label matches regex.
    text_regex: JS-style regex string like 'easy apply\\b'
    """
    end = time.monotonic() + timeout
    # Search BOTH buttons and anchors with role=button — LinkedIn uses both.
    # Prefer the EA button outside the "Save" / "More" sections by scoring
    # buttons in the top-card region higher.
    js = """
        const re = new RegExp(arguments[0], 'i');
        const candidates = [];
        const sel = "button, a[role='button'], a.jobs-apply-button";
        for (const b of document.querySelectorAll(sel)) {
            if (b.offsetParent === null) continue;
            if (b.disabled || b.getAttribute('aria-disabled') === 'true') continue;
            const t = (b.textContent || '').trim();
            const a = (b.getAttribute('aria-label') || '').trim();
            if (re.test(t) || re.test(a)) candidates.push(b);
        }
        if (!candidates.length) return null;
        // Prefer the one inside the top job card / apply section
        for (const b of candidates) {
            if (b.closest('.jobs-apply-button--top-card, .jobs-s-apply, .jobs-unified-top-card, .job-details-jobs-unified-top-card')) {
                return b;
            }
        }
        return candidates[0];
    """
    while time.monotonic() < end:
        el = driver.execute_script(js, text_regex)
        if el is not None:
            return el
        time.sleep(0.2)
    return None


def robust_click(driver, el, label: str = "btn") -> bool:
    """Click that survives overlays, animations, off-screen elements.
    Tries scrollIntoView → native click → JS click → Actions click.
    Returns True if any strategy didn't raise."""
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center', behavior:'instant'});", el,
        )
        time.sleep(0.3)
    except Exception:
        pass
    # Strategy 1: native
    try:
        el.click()
        logger.debug("{}: native click ok", label)
        return True
    except Exception as e:
        logger.debug("{}: native click failed: {}", label, e)
    # Strategy 2: JS click
    try:
        driver.execute_script("arguments[0].click();", el)
        logger.debug("{}: JS click ok", label)
        return True
    except Exception as e:
        logger.debug("{}: JS click failed: {}", label, e)
    # Strategy 3: Actions
    try:
        from selenium.webdriver.common.action_chains import ActionChains
        ActionChains(driver).move_to_element(el).pause(0.2).click().perform()
        logger.debug("{}: Actions click ok", label)
        return True
    except Exception as e:
        logger.debug("{}: Actions click failed: {}", label, e)
    return False


def wait_for_modal(driver, timeout: float = 6.0) -> bool:
    """Wait for the Easy Apply modal/dialog to appear after clicking Apply."""
    js = """
        return !!document.querySelector(
            "div[role='dialog'][aria-labelledby*='ply'], "
            + ".jobs-easy-apply-modal, "
            + ".artdeco-modal[aria-labelledby*='easy'], "
            + ".jobs-easy-apply-content"
        );
    """
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if driver.execute_script(js):
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def find_input_by_label(driver, label_regex: str):
    """Find first VISIBLE input whose label/placeholder/aria matches regex."""
    js = """
        const re = new RegExp(arguments[0], 'i');
        for (const inp of document.querySelectorAll("input[type='text'], input[type='tel'], input:not([type])")) {
            if (inp.offsetParent === null) continue;
            const id = inp.id || '';
            const name = inp.name || '';
            const placeholder = inp.placeholder || '';
            const aria = inp.getAttribute('aria-label') || '';
            const labelEl = id ? document.querySelector(`label[for="${id}"]`) : null;
            const labelText = labelEl ? labelEl.textContent : '';
            if (re.test([id, name, placeholder, aria, labelText].join(' '))) return inp;
        }
        return null;
    """
    return driver.execute_script(js, label_regex)


def is_blocked_page(driver) -> bool:
    try:
        text = driver.execute_script(
            "return (document.body.innerText || '').toLowerCase().substring(0, 5000);"
        )
    except Exception:
        return False
    return any(m in text for m in RATE_LIMIT_MARKERS)


def page_url(driver) -> str:
    try:
        return driver.current_url
    except Exception:
        return ""


def dump_buttons(driver, limit: int = 12) -> list[dict]:
    return driver.execute_script(f"""
        return Array.from(document.querySelectorAll('button'))
            .filter(b => b.offsetParent !== null && !b.disabled)
            .slice(0, {limit})
            .map(b => ({{
                text: (b.textContent || '').trim().substring(0, 50),
                aria: (b.getAttribute('aria-label') || '').substring(0, 80),
            }}));
    """)


# --- Easy Apply flow ---

def apply_to_job(driver, job_url: str, profile_phone: str = "") -> ApplyResult:
    logger.info("Opening: {}", job_url)
    job_id = re.search(r"/jobs/view/(\d+)", job_url)
    job_tag = job_id.group(1) if job_id else "nojid"
    try:
        driver.get(job_url)
    except WebDriverException as e:
        return ApplyResult(ApplyOutcome.FAILED, f"goto failed: {e}")
    human_sleep(3, 5)

    if is_blocked_page(driver):
        _diag_save(driver, f"{job_tag}_blocked")
        return ApplyResult(ApplyOutcome.BLOCKED, "warning page detected")

    # Verify we landed on the job page (not authwall)
    url_now = page_url(driver)
    if "linkedin.com/jobs/" not in url_now:
        _diag_save(driver, f"{job_tag}_redirect")
        return ApplyResult(ApplyOutcome.FAILED, f"redirected to {url_now}")

    # Some pages need a scroll to wire up the apply button
    try:
        driver.execute_script("window.scrollBy(0, 400);")
        time.sleep(0.5)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.3)
    except Exception:
        pass

    # Already applied? Check for "Applied" / "Submitted resume" indicator
    already_text = driver.execute_script(
        "return (document.body.innerText || '').substring(0, 4000);"
    ).lower()
    if "applied" in already_text and not find_button_by_text(driver, r"^easy apply\b", timeout=1):
        return ApplyResult(ApplyOutcome.ALREADY_APPLIED)

    # Try Easy Apply first (full apply with modal)
    ea_btn = find_button_by_text(driver, r"easy apply\b", timeout=6)
    if ea_btn:
        human_sleep(0.8, 1.8)
        if not robust_click(driver, ea_btn, "easy_apply"):
            _diag_save(driver, f"{job_tag}_ea_click_failed")
            return ApplyResult(ApplyOutcome.FAILED, "all click strategies failed on Easy Apply")
        logger.info("clicked Easy Apply, waiting for modal")
        if not wait_for_modal(driver, timeout=6):
            _diag_save(driver, f"{job_tag}_no_modal")
            # Maybe the click opened an external apply tab — re-check buttons
            return ApplyResult(ApplyOutcome.FAILED, "Easy Apply clicked but modal never appeared")
        logger.info("modal appeared")
        return _walk_modal(driver, profile_phone, job_tag)

    # Fallback: "I'm interested" — LinkedIn's soft-signal alternative
    interested = find_button_by_text(driver, r"i.?m interested", timeout=2)
    if interested:
        human_sleep(0.8, 1.8)
        if not robust_click(driver, interested, "interested"):
            _diag_save(driver, f"{job_tag}_interested_click_failed")
            return ApplyResult(ApplyOutcome.FAILED, "I'm interested click failed")
        logger.info("clicked I'm interested (soft-signal)")
        human_sleep(1.5, 3)
        # Some flows show a confirmation modal with "Submit" or auto-close
        confirm = find_button_by_text(driver, r"^(?:submit|confirm|done|got it)$", timeout=2)
        if confirm:
            robust_click(driver, confirm, "interested_confirm")
            human_sleep(1, 2)
        return ApplyResult(ApplyOutcome.INTEREST_SIGNALED, detail="clicked I'm interested")

    # Neither button → really no apply path
    dump = dump_buttons(driver)
    logger.info("No Apply or I'm interested. Buttons: {}", dump)
    _diag_save(driver, f"{job_tag}_no_apply")
    return ApplyResult(ApplyOutcome.NO_EASY_APPLY, detail=f"buttons={dump}")


def _walk_modal(driver, profile_phone: str, job_tag: str = "nojid") -> ApplyResult:
    for page_idx in range(MAX_MODAL_PAGES):
        human_sleep(0.6, 1.4)
        if is_blocked_page(driver):
            _diag_save(driver, f"{job_tag}_modal_blocked_p{page_idx}")
            return ApplyResult(ApplyOutcome.BLOCKED, f"page {page_idx}")

        # Phone fill
        if profile_phone:
            inp = find_input_by_label(driver, r"phone|mobile|tel")
            if inp:
                current = driver.execute_script("return arguments[0].value;", inp)
                if not current.strip():
                    driver.execute_script(
                        "arguments[0].focus(); arguments[0].value = arguments[1]; "
                        "arguments[0].dispatchEvent(new Event('input', {bubbles:true})); "
                        "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                        inp, profile_phone,
                    )
                    human_sleep(0.3, 0.7)

        # Submit?
        submit = find_button_by_text(driver, r"^submit application$", timeout=1)
        if submit:
            logger.info("Submit at page {}", page_idx + 1)
            _uncheck_follow(driver)
            human_sleep(0.7, 1.4)
            if not robust_click(driver, submit, "submit"):
                _diag_save(driver, f"{job_tag}_submit_click_failed")
                return ApplyResult(ApplyOutcome.FAILED, "submit click failed", pages=page_idx + 1)
            human_sleep(2.5, 4)
            return ApplyResult(ApplyOutcome.APPLIED, pages=page_idx + 1)

        # Review?
        review = find_button_by_text(driver, r"^review( your application)?$", timeout=1)
        if review:
            logger.debug("Review at page {}", page_idx + 1)
            robust_click(driver, review, "review")
            human_sleep(1, 2)
            continue

        # Continue / Next?
        cont = find_button_by_text(driver, r"^(?:continue( to next step)?|next)$", timeout=1)
        if cont:
            errs = driver.execute_script("""
                return Array.from(document.querySelectorAll(
                    "[role='alert'], .artdeco-inline-feedback--error"
                )).filter(e => e.offsetParent !== null && (e.textContent || '').trim()).length;
            """)
            if errs > 0:
                logger.info("Unfilled required fields at page {}", page_idx + 1)
                _diag_save(driver, f"{job_tag}_required_fields_p{page_idx}")
                _close_modal(driver)
                return ApplyResult(
                    ApplyOutcome.TOO_MANY_QUESTIONS,
                    detail=f"red errors at page {page_idx + 1}",
                    pages=page_idx + 1,
                )
            logger.debug("Continue at page {}", page_idx + 1)
            robust_click(driver, cont, "continue")
            human_sleep(1, 2)
            continue

        # Nothing matches → bail with diagnostic
        dump = dump_buttons(driver, 8)
        _diag_save(driver, f"{job_tag}_no_nav_p{page_idx}")
        return ApplyResult(
            ApplyOutcome.FAILED,
            detail=f"no nav at page {page_idx + 1}: {dump}",
            pages=page_idx + 1,
        )

    _close_modal(driver)
    return ApplyResult(
        ApplyOutcome.TOO_MANY_QUESTIONS,
        detail=f">{MAX_MODAL_PAGES} modal pages",
        pages=MAX_MODAL_PAGES,
    )


def _uncheck_follow(driver):
    driver.execute_script("""
        for (const cb of document.querySelectorAll("input[type='checkbox']")) {
            const lbl = cb.closest('label') || document.querySelector(`label[for='${cb.id}']`);
            const t = lbl ? lbl.textContent.toLowerCase() : '';
            if (/follow.*compan/i.test(t) && cb.checked) {
                lbl.click();
                return;
            }
        }
    """)


def _close_modal(driver):
    close = find_button_by_text(driver, r"^(?:dismiss|close)$", timeout=1)
    if close:
        try:
            close.click()
            human_sleep(0.6, 1.2)
            discard = find_button_by_text(driver, r"^discard$", timeout=1)
            if discard:
                discard.click()
                human_sleep(0.4, 0.8)
        except Exception:
            pass


# --- main test entry ---

if __name__ == "__main__":
    # Simple smoke test: open feed, then a job, report
    with selenium_firefox(headless=True) as driver:
        logger.info("Opening LinkedIn feed for auth check...")
        driver.get("https://www.linkedin.com/feed/")
        human_sleep(3, 5)
        url = page_url(driver)
        title = driver.title
        logger.info("URL: {} | Title: {!r}", url, title)
        if "/feed/" in url and "login" not in url.lower():
            logger.success("✅ Logged in via copied profile")
        else:
            logger.warning("Not logged in: {}", url)

        # Try a known LinkedIn job
        test_url = "https://www.linkedin.com/jobs/view/4408048368/"
        result = apply_to_job(driver, test_url, profile_phone="")
        logger.info("Apply result on test job: {}", result)
