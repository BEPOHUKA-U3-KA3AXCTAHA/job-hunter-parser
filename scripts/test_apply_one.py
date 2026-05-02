"""Run apply_to_job on a single LinkedIn job URL.

Tries Easy Apply first; falls back to external ATS (Greenhouse/Lever/Ashby/
Workday/generic) → "I'm interested" soft-signal. Reports the final
ApplyResult.
"""
from __future__ import annotations

import sys
import time

from loguru import logger

from app.modules.automation.adapters.selenium_bot import apply_to_job, selenium_firefox


def main(job_id: str = "4408058876"):
    url = f"https://www.linkedin.com/jobs/view/{job_id}/"
    logger.warning("⏰ Browser will open in 8s — switch to its workspace if needed")
    time.sleep(8)
    with selenium_firefox(headless=False) as driver:
        result = apply_to_job(driver, url, profile_phone="")
        logger.info("Result: {}", result)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "4408058876")
