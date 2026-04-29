"""Full DOM diagnostic of a single LinkedIn job page.

Saves screenshot + all clickables + login proof + apply-section HTML.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from loguru import logger

from src.automation.selenium_bot import selenium_firefox

OUT = Path("/tmp/jhp_diag")
OUT.mkdir(parents=True, exist_ok=True)


def main(job_id: str = "4408058876"):
    url = f"https://www.linkedin.com/jobs/view/{job_id}/"
    with selenium_firefox(headless=False) as driver:
        # Login proof first — open feed
        driver.get("https://www.linkedin.com/feed/")
        time.sleep(4)
        feed_url = driver.current_url
        logged_in = "/feed" in feed_url
        avatar = driver.execute_script(
            "return !!document.querySelector('img.global-nav__me-photo, "
            "button[data-view-name=\"nav-bar-feed\"], a[data-view-name=\"nav-bar-me\"]');"
        )
        logger.info("Feed URL: {} | logged_in={} | avatar={}", feed_url, logged_in, avatar)
        driver.save_screenshot(str(OUT / "00_feed.png"))

        # Open job
        driver.get(url)
        time.sleep(5)
        # Scroll a bit so all content paints
        driver.execute_script("window.scrollBy(0, 200);"); time.sleep(0.5)
        driver.execute_script("window.scrollTo(0, 0);"); time.sleep(0.5)

        driver.save_screenshot(str(OUT / f"01_job_{job_id}.png"))
        (OUT / f"01_job_{job_id}.html").write_text(driver.page_source[:500_000])

        # Scroll the apply card region into view + click "More actions" dropdown
        # First: dump ALL visible clickables (button, a, [role=button], div[role=button])
        all_clicks = driver.execute_script("""
            const sel = "button, a, [role='button']";
            const out = [];
            for (const el of document.querySelectorAll(sel)) {
                if (el.offsetParent === null) continue;
                if (el.disabled) continue;
                const rect = el.getBoundingClientRect();
                if (rect.width < 8 || rect.height < 8) continue;
                const text = (el.textContent || '').trim().substring(0, 60);
                const aria = (el.getAttribute('aria-label') || '').substring(0, 100);
                const cls = (el.className || '').toString().substring(0, 100);
                const href = (el.getAttribute('href') || '').substring(0, 80);
                if (!text && !aria) continue;
                out.push({tag: el.tagName, text, aria, cls, href, top: Math.round(rect.top)});
            }
            return out;
        """)
        # Save full list
        (OUT / f"02_all_clickables_{job_id}.json").write_text(json.dumps(all_clicks, indent=2))
        logger.info("Total visible clickables: {}", len(all_clicks))

        # Top-of-page (within first 800px) clickables — that's where Apply lives
        top = [c for c in all_clicks if c["top"] < 800]
        logger.info("Top-area clickables ({}):", len(top))
        for c in top:
            logger.info("  [{}px] <{}> text={!r} aria={!r} cls={!r}",
                        c["top"], c["tag"], c["text"], c["aria"], c["cls"][:60])

        # Look for ANY element whose innerText contains 'apply' (not just buttons)
        apply_hits = driver.execute_script("""
            const out = [];
            const all = document.querySelectorAll('*');
            for (const el of all) {
                if (el.children.length > 0) continue;  // leaf only
                if (el.offsetParent === null) continue;
                const t = (el.textContent || '').trim();
                if (!t || t.length > 80) continue;
                if (/easy apply|^apply$|apply now|apply on /i.test(t)) {
                    out.push({tag: el.tagName, text: t, parent: el.parentElement?.tagName, parentRole: el.parentElement?.getAttribute('role')});
                }
            }
            return out;
        """)
        logger.info("Elements with 'apply' text: {}", len(apply_hits))
        for h in apply_hits[:20]:
            logger.info("  <{}> {!r} (parent={} role={})", h["tag"], h["text"], h["parent"], h["parentRole"])

        # Save apply-section HTML by finding it via apply-text element's ancestor
        section = driver.execute_script("""
            const cards = document.querySelectorAll(
                ".jobs-unified-top-card, .job-details-jobs-unified-top-card, .jobs-apply-button--top-card, .jobs-s-apply"
            );
            return Array.from(cards).map(c => c.outerHTML.substring(0, 30000));
        """)
        (OUT / f"03_apply_section_{job_id}.html").write_text("\n\n<!--SEP-->\n\n".join(section))
        logger.info("Apply section dumps: {}", len(section))

        logger.info("ALL DUMPS in {}", OUT)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "4408058876")
