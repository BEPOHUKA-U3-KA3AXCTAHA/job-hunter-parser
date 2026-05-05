"""End-to-end ATS apply via Camoufox (the new path).

Drops you straight into the Camoufox runner — same code path the LinkedIn
external-Apply branch uses, just with a CLI URL arg. Selenium isn't
involved; Cloudflare Turnstile auto-clears.

Usage:
  DISPLAY=:1 PYTHONPATH=. .venv/bin/python scripts/test_camoufox_apply.py [<url>]

Default URL: Aalyria/Rippling backend SWE.
"""
from __future__ import annotations

import asyncio
import sys

from loguru import logger

from app.modules.automation.adapters.external_apply.runner import run_external_apply
from app.modules.automation.ports.external_apply import AtsContext
from app.modules.automation.adapters.external_apply.base import load_ats_context

DEFAULT_URL = (
    "https://ats.rippling.com/en-GB/aalyria-careers/jobs/"
    "d8ac14cd-efdc-4688-8c7c-821685da9b2c?source=LinkedIn"
)


async def main(url: str) -> None:
    ctx: AtsContext = load_ats_context(
        company="Aalyria",
        job_title="Backend Software Engineer",
        job_url=url,
        ats_url=url,
    )
    logger.info("ctx: company={} title={} resume={}",
                ctx.company, ctx.job_title, ctx.resume_path)
    result = await run_external_apply(url, ctx, headless=False)
    logger.info("RESULT: success={} ats={} fields={} detail={}",
                result.success, result.ats_name, result.fields_filled, result.detail)
    await asyncio.sleep(15)


# Convenience: also expose a 'list what's still missing' helper that the
# user can call from the REPL or via a separate driver. Saved here so it
# stays alongside the test for now.


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    asyncio.run(main(url))
