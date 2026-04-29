"""Diagnostic: list apply-related buttons on a sample of LinkedIn job pages.

Goal: prove or disprove the hypothesis that LinkedIn's search returns mostly
"I'm interested" jobs, not real Easy Apply jobs, in 2026.
"""
from __future__ import annotations

import time
from urllib.parse import quote_plus

from loguru import logger

from src.automation.selenium_bot import human_sleep, selenium_firefox

KEYWORDS = ["rust senior remote", "python backend remote senior"]


def main():
    with selenium_firefox(headless=False) as driver:
        all_cards = []
        for kw in KEYWORDS:
            url = (
                "https://www.linkedin.com/jobs/search/"
                f"?keywords={quote_plus(kw)}&f_AL=true&f_TPR=r604800&f_WT=2&sortBy=DD"
            )
            logger.info("search: {}", kw)
            driver.get(url)
            time.sleep(5)
            for _ in range(3):
                driver.execute_script("window.scrollBy(0, 1000);")
                time.sleep(1)

            cards = driver.execute_script("""
                const out = [];
                const cards = document.querySelectorAll(
                    'li[data-occludable-job-id], li.jobs-search-results__list-item, div.job-card-container'
                );
                for (const c of cards) {
                    const a = c.querySelector('a[href*="/jobs/view/"]');
                    if (!a) continue;
                    const href = a.href.split('?')[0];
                    const titleEl = c.querySelector(
                        'a[href*="/jobs/view/"] strong, a[href*="/jobs/view/"] span[aria-hidden="true"]'
                    );
                    const title = (titleEl ? titleEl.textContent : a.textContent).trim();
                    const text = (c.innerText || '').toLowerCase();
                    out.push({
                        href, title: title.substring(0, 60),
                        hasEasyApplyText: /easy apply/.test(text),
                        hasInterestedText: /i.?m interested/.test(text),
                        hasAppliedText: /\\bapplied\\b/.test(text),
                    });
                }
                return out;
            """)
            logger.info("kw={!r} cards={} ea={} interest={} applied={}",
                        kw, len(cards),
                        sum(1 for c in cards if c["hasEasyApplyText"]),
                        sum(1 for c in cards if c["hasInterestedText"]),
                        sum(1 for c in cards if c["hasAppliedText"]))
            all_cards.extend(cards)

        # Visit each job page, list all clickable buttons
        seen = set()
        unique = [c for c in all_cards if not (c["href"] in seen or seen.add(c["href"]))]
        logger.info("Total unique cards: {}", len(unique))
        for i, c in enumerate(unique[:6], 1):
            logger.info("--- [{}/{}] {} ---", i, min(6, len(unique)), c["title"])
            logger.info("  card-text: ea={} interested={} applied={}",
                        c["hasEasyApplyText"], c["hasInterestedText"], c["hasAppliedText"])
            try:
                driver.get(c["href"])
            except Exception as e:
                logger.warning("nav failed: {}", e)
                continue
            time.sleep(4)
            # List all visible buttons + anchors with role=button
            btns = driver.execute_script("""
                const out = [];
                const sel = "button, a[role='button']";
                for (const b of document.querySelectorAll(sel)) {
                    if (b.offsetParent === null) continue;
                    if (b.disabled) continue;
                    const t = (b.textContent || '').trim().substring(0, 50);
                    const a = (b.getAttribute('aria-label') || '').substring(0, 80);
                    if (!t && !a) continue;
                    if (/easy apply|interested|apply|submit/i.test(t + ' ' + a)) {
                        out.push({tag: b.tagName, text: t, aria: a});
                    }
                }
                return out;
            """)
            for b in btns:
                logger.info("  btn: {} text={!r} aria={!r}", b["tag"], b["text"], b["aria"])
            if not btns:
                logger.warning("  NO apply-related buttons found!")
            human_sleep(2, 4)


if __name__ == "__main__":
    main()
