"""Test apply_to_job on a SINGLE known Easy Apply job to verify selector fix."""
from __future__ import annotations

import sys
import time

from loguru import logger

from app.modules.automation.adapters.selenium_bot import (
    apply_to_job,
    find_button_by_text,
    selenium_firefox,
)


def main(job_id: str = "4408058876"):
    url = f"https://www.linkedin.com/jobs/view/{job_id}/"
    logger.warning("⏰ Browser will open in 8s — switch to its workspace if needed")
    time.sleep(8)
    with selenium_firefox(headless=False) as driver:
        # First just go to the job and probe
        driver.get(url)
        time.sleep(5)

        # Probe: find Easy Apply button
        ea = find_button_by_text(driver, r"easy apply\b", timeout=5)
        if ea:
            tag = driver.execute_script("return arguments[0].tagName;", ea)
            text = driver.execute_script("return arguments[0].textContent.trim();", ea)
            aria = driver.execute_script("return arguments[0].getAttribute('aria-label');", ea)
            logger.success("✅ FOUND Easy Apply: <{}> text={!r} aria={!r}", tag, text, aria)
        else:
            logger.error("❌ Still cannot find Easy Apply")
            return

        # Now run full apply flow (will click + walk modal, but DON'T submit)
        logger.info("Running full apply_to_job flow...")
        result = apply_to_job(driver, url, profile_phone="")
        logger.info("Result: {}", result)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "4408058876")
