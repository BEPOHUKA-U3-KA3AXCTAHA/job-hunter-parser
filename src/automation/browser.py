"""Camoufox-based browser session for LinkedIn / ATS automation.

Camoufox is an anti-detect Firefox build (MIT, github.com/daijro/camoufox)
that patches navigator.webdriver, canvas/WebGL/font fingerprints, and
automation signatures so a Playwright-driven session looks like a regular
user. Far less detectable than vanilla Playwright Chromium + stealth plugins.

This module:
- Manages a persistent Camoufox profile (~/.jhp/camoufox-profile)
- On first launch, imports LinkedIn cookies from the user's REAL Firefox so
  there's no "sign in from a new device" alert from LinkedIn
- Exposes a context manager `BrowserSession()` that yields a Playwright Page

Usage:
    async with BrowserSession() as page:
        await page.goto("https://www.linkedin.com/feed/")
        # ... automate
"""
from __future__ import annotations

import asyncio
import random
from contextlib import asynccontextmanager
from pathlib import Path

from camoufox.async_api import AsyncCamoufox
from loguru import logger

from src.automation.firefox_cookies import export_cookies_for_domain

PROFILE_DIR = Path.home() / ".jhp" / "camoufox-profile"
COOKIES_INJECTED_FLAG = PROFILE_DIR / ".cookies_injected"


def _ensure_profile_dir() -> None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)


async def _inject_linkedin_cookies(context) -> int:
    """One-shot: import LinkedIn cookies from real Firefox into the Camoufox context.

    Marks PROFILE_DIR/.cookies_injected after success so we don't re-import on
    every launch. Re-import only if you delete the flag (e.g. cookies expired).
    """
    if COOKIES_INJECTED_FLAG.exists():
        logger.debug("Cookies already injected previously (flag at {})", COOKIES_INJECTED_FLAG)
        return 0

    try:
        cookies = export_cookies_for_domain("linkedin")
    except FileNotFoundError as e:
        logger.warning("Cannot import Firefox cookies: {}. You'll need to log in manually.", e)
        return 0

    if not cookies:
        logger.warning("No LinkedIn cookies in Firefox profile. Log in there first, then retry.")
        return 0

    await context.add_cookies(cookies)
    COOKIES_INJECTED_FLAG.touch()
    logger.info("Injected {} LinkedIn cookies from Firefox into Camoufox profile", len(cookies))
    return len(cookies)


async def human_sleep(min_s: float = 0.6, max_s: float = 2.4) -> None:
    """Sleep for a random duration drawn from a log-normal-ish distribution.

    Avoids the "exactly 1.0s every time" pattern that bot detectors flag.
    """
    # log-normal centered around midpoint; clamp to range
    midpoint = (min_s + max_s) / 2
    sigma = (max_s - min_s) / 4
    delay = random.lognormvariate(0, 0.4) * midpoint
    delay = max(min_s, min(max_s, delay))
    delay += random.uniform(-sigma * 0.2, sigma * 0.2)
    await asyncio.sleep(max(0.05, delay))


@asynccontextmanager
async def browser_session(headless: bool = False, geoip: bool = True):
    """Yield a Camoufox Page with persistent profile + injected LinkedIn cookies.

    Args:
        headless: True for background runs, False to watch what the bot does
                  (recommended during development — visual debugging is gold)
        geoip: True to spoof timezone/locale to match the connection's IP
               (helps anti-detect; ours always True)

    First launch downloads ~80MB Camoufox binary if not already present.
    """
    _ensure_profile_dir()

    async with AsyncCamoufox(
        headless=headless,
        geoip=geoip,
        humanize=True,           # adds bezier mouse curves + reading-speed pauses
        i_know_what_im_doing=True,  # silences the "this is automation" warning
        persistent_context=True,
        user_data_dir=str(PROFILE_DIR),
    ) as browser:
        # AsyncCamoufox with persistent_context=True yields a BrowserContext directly
        context = browser
        await _inject_linkedin_cookies(context)

        page = await context.new_page()
        try:
            yield page
        finally:
            await page.close()


if __name__ == "__main__":
    # Smoke test: open LinkedIn feed, confirm we're logged in, take screenshot
    import asyncio

    async def main():
        async with browser_session(headless=False) as page:
            logger.info("Opening LinkedIn feed…")
            await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
            await human_sleep(2, 5)
            url = page.url
            title = await page.title()
            logger.info("Landed on: {} — title: {!r}", url, title)
            if "/feed/" in url and "login" not in url.lower():
                logger.success("Logged in via cookies — Camoufox session works")
            else:
                logger.warning("Not logged in (URL: {}) — manual login needed in this profile", url)
                logger.info("Browser will stay open for 60 seconds, log in manually if needed")
                await asyncio.sleep(60)
            await page.screenshot(path="/tmp/jhp_linkedin_screenshot.png")
            logger.info("Screenshot saved /tmp/jhp_linkedin_screenshot.png")

    asyncio.run(main())
