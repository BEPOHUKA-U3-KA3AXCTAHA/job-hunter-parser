"""Click EA, list iframes, switch into each, dump its content."""
from __future__ import annotations
import sys, time
from loguru import logger
from app.modules.automation.adapters.selenium_bot import find_button_by_text, robust_click, selenium_firefox


def main(job_id="4408058876"):
    url = f"https://www.linkedin.com/jobs/view/{job_id}/"
    with selenium_firefox(headless=False) as driver:
        driver.get(url); time.sleep(5)
        ea = find_button_by_text(driver, r"easy apply\b", timeout=5)
        if not ea:
            logger.error("no EA"); return
        robust_click(driver, ea, "ea"); time.sleep(4)

        iframes = driver.execute_script("""
            return Array.from(document.querySelectorAll('iframe')).map(f => ({
                src: (f.src || '').substring(0, 150),
                id: f.id || '',
                name: f.name || '',
                title: f.title || '',
                visible: f.offsetParent !== null,
                w: f.offsetWidth, h: f.offsetHeight,
            }));
        """)
        logger.info("iframes total: {}", len(iframes))
        for i, f in enumerate(iframes):
            logger.info("  iframe[{}] visible={} {}x{} title={!r} src={!r}",
                        i, f["visible"], f["w"], f["h"], f["title"], f["src"])

        # Try switching into each visible iframe
        from selenium.webdriver.common.by import By
        for i, f in enumerate(iframes):
            if not f["visible"] or f["w"] < 200: continue
            try:
                driver.switch_to.default_content()
                el = driver.find_elements(By.TAG_NAME, "iframe")[i]
                driver.switch_to.frame(el)
            except Exception as e:
                logger.warning("switch[{}] failed: {}", i, e); continue
            try:
                inner = driver.execute_script("""
                    return {
                        title: document.title,
                        bodyText: (document.body?.innerText || '').substring(0, 200),
                        formCount: document.querySelectorAll('form').length,
                        inputCount: document.querySelectorAll('input').length,
                        nextBtns: Array.from(document.querySelectorAll('button')).filter(b =>
                            /^next$/i.test((b.textContent||'').trim()) && b.offsetParent !== null
                        ).length,
                    };
                """)
                logger.info("  iframe[{}] INSIDE: {}", i, inner)
            except Exception as e:
                logger.warning("inner script failed: {}", e)
        driver.switch_to.default_content()
        time.sleep(2)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "4408058876")
