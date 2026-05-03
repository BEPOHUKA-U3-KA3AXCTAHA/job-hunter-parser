"""Proof-of-concept: open Aalyria/Rippling form via Camoufox (patched
Firefox) instead of Selenium-Firefox, and check whether Cloudflare Turnstile
treats us as a human browser.

Run: DISPLAY=:1 PYTHONPATH=. .venv/bin/python scripts/test_camoufox_aalyria.py

What to do when the browser opens:
  1. Wait until the form is visible
  2. Manually click the Turnstile "Verify you are human" checkbox
  3. Watch this terminal — script polls cf-turnstile-response every second.
     If you see "✅ Turnstile token received" — fingerprint isn't flagged,
     migration unblocks ATS auto-fill on hardened sites.
     If you see only "❌ Turnstile failed" — Camoufox is also detected and
     we need patchright/nodriver instead.
"""
from __future__ import annotations

import asyncio
import sys

from loguru import logger

from app.modules.automation.adapters.camoufox import browser_session, human_sleep

DEFAULT_URL = (
    "https://ats.rippling.com/en-GB/aalyria-careers/jobs/"
    "d8ac14cd-efdc-4688-8c7c-821685da9b2c?source=LinkedIn"
)


# JS to detect/inspect Turnstile state on the page (walks shadow DOM).
TURNSTILE_PROBE = r"""
() => {
    function* dn(r){const s=[r];while(s.length){const n=s.pop();if(!n)return;
        if(n.nodeType===1)yield n;if(n.shadowRoot)s.push(n.shadowRoot);
        const k=n.children||n.childNodes||[];for(let i=k.length-1;i>=0;i--)s.push(k[i]);}}
    let token = '';
    let containerRect = null;
    let failed = false;
    const iframes = [];
    for (const e of dn(document)) {
        if (e.tagName === 'INPUT') {
            const n = (e.name || '').toLowerCase();
            const tid = (e.getAttribute && (e.getAttribute('data-testid') || '')).toLowerCase();
            if (n === 'cf-turnstile-response' || tid === 'input-turnstile-required') {
                if ((e.value || '').trim()) token = e.value;
            }
        }
        if (e.id === 'turnstile-container' || (typeof e.className === 'string'
            && /\bcf-turnstile\b/i.test(e.className))) {
            const r = e.getBoundingClientRect();
            if (r.width > 4) containerRect = {x: r.x, y: r.y, w: r.width, h: r.height};
        }
        if (e.tagName === 'IFRAME') {
            const r = e.getBoundingClientRect();
            iframes.push({src: (e.src || '').slice(0, 120), w: r.width, h: r.height,
                          x: r.x, y: r.y});
        }
        const txt = (e.textContent || '').slice(0, 200);
        if (/failed|error|try again/i.test(txt) && /captcha|challenge|verify/i.test(txt)) {
            failed = true;
        }
    }
    return {url: location.href, token, containerRect, iframes, failed};
}
"""


async def run(url: str) -> None:
    async with browser_session(headless=False) as page:
        logger.info("Camoufox opened — navigating to {}", url)
        await page.goto(url, wait_until="domcontentloaded")
        await human_sleep(2, 4)

        # Click landing-page Apply if present (Rippling has a separate Apply
        # button on the job description page that opens the form modal).
        try:
            apply_btn = page.get_by_role("button", name="Apply", exact=False).first
            if await apply_btn.is_visible(timeout=3000):
                logger.info("clicked landing-page Apply")
                await apply_btn.click()
                await human_sleep(2, 4)
        except Exception as e:
            logger.debug("no landing Apply button: {}", e)

        # Switch to the new tab if Apply opened one
        await asyncio.sleep(1.5)
        ctx = page.context
        if len(ctx.pages) > 1:
            page = ctx.pages[-1]
            logger.info("switched to new tab: {}", page.url)
            await page.wait_for_load_state("domcontentloaded")
            await human_sleep(2, 3)

        logger.warning("=" * 70)
        logger.warning("🤖→🧍 Manually click the Turnstile checkbox in the open browser.")
        logger.warning("    Polling Turnstile state every 2s for 180s.")
        logger.warning("=" * 70)

        deadline = asyncio.get_event_loop().time() + 180.0
        last_state_str = ""
        while asyncio.get_event_loop().time() < deadline:
            try:
                state = await page.evaluate(TURNSTILE_PROBE)
            except Exception as e:
                logger.warning("probe failed: {}", e)
                await asyncio.sleep(2)
                continue
            iframes = [
                f"{i['src'][:50]} {i['w']:.0f}×{i['h']:.0f}@({i['x']:.0f},{i['y']:.0f})"
                for i in state["iframes"]
            ]
            state_str = (
                f"url={state['url'][:60]} "
                f"token={'YES (len=' + str(len(state['token'])) + ')' if state['token'] else 'no'} "
                f"container={state['containerRect']} "
                f"iframes={iframes} "
                f"failed_text={state['failed']}"
            )
            if state_str != last_state_str:
                logger.info(state_str)
                last_state_str = state_str
            if state["token"]:
                logger.success(
                    "✅ TURNSTILE ACCEPTED us — Camoufox bypasses fingerprint detection. "
                    "Migration to Camoufox unblocks ATS auto-fill on hardened sites."
                )
                await asyncio.sleep(8)
                return
            if state["failed"]:
                logger.error(
                    "❌ Turnstile failed text detected — even Camoufox got flagged. "
                    "Need to escalate to patchright/nodriver."
                )
                await asyncio.sleep(8)
                return
            await asyncio.sleep(2)
        logger.warning("⌛ Timeout — no token, no explicit fail. Inconclusive.")
        await asyncio.sleep(5)


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    asyncio.run(run(url))
