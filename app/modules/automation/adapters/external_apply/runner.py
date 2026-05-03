"""Camoufox session runner for external ATS apply.

Bridges the Selenium-based LinkedIn flow → Camoufox-based ATS handlers.
The selenium_bot dispatcher calls `run_external_apply_sync(url, ctx)`
when it detects an external Apply button; we open a Camoufox session
(patched Firefox that bypasses Cloudflare's fingerprint check), navigate
to the ATS URL, dispatch to the matching handler, and return the result.

This is what unlocks Cloudflare-Turnstile-protected ATSes (Rippling,
Aalyria etc.). Selenium gets fingerprint-flagged before the form even
renders; Camoufox passes silently.
"""
from __future__ import annotations

import asyncio

from loguru import logger

from app.modules.automation.adapters.camoufox import browser_session
from app.modules.automation.adapters.external_apply.dispatcher import pick_handler
from app.modules.automation.ports.external_apply import AtsContext, AtsResult


async def run_external_apply(url: str, ctx: AtsContext, headless: bool = False) -> AtsResult:
    """Open a Camoufox session, navigate to `url`, dispatch + run the
    matching ATS handler. Returns the AtsResult."""
    handler = pick_handler(url)
    logger.info("camoufox apply: ATS={} URL={}", handler.name, url[:120])

    async with browser_session(headless=headless) as page:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            logger.warning("camoufox apply: navigation failed: {}", e)
            return AtsResult(
                success=False, detail=f"navigation failed: {e}",
                ats_name=handler.name,
            )
        await asyncio.sleep(2)
        try:
            return await handler.apply(page, ctx)
        except Exception as e:
            logger.exception("camoufox apply: handler {} crashed: {}", handler.name, e)
            return AtsResult(
                success=False, detail=f"handler crashed: {e}",
                ats_name=handler.name,
            )


def run_external_apply_sync(url: str, ctx: AtsContext, headless: bool = False) -> AtsResult:
    """Sync wrapper for the Selenium dispatcher (LinkedIn external Apply
    branch lives in sync code; Camoufox is async). Spins up its own event
    loop — caller must NOT already be inside one."""
    return asyncio.run(run_external_apply(url, ctx, headless=headless))
