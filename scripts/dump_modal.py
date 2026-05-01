"""Click Easy Apply, then dump the modal's actual DOM structure."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from loguru import logger

from app.modules.automation.adapters.selenium_bot import (
    find_button_by_text,
    robust_click,
    selenium_firefox,
)

OUT = Path("/tmp/jhp_diag")


def main(job_id: str = "4408058876"):
    url = f"https://www.linkedin.com/jobs/view/{job_id}/"
    with selenium_firefox(headless=False) as driver:
        driver.get(url)
        time.sleep(5)
        ea = find_button_by_text(driver, r"easy apply\b", timeout=5)
        if not ea:
            logger.error("no EA button"); return
        logger.success("Found EA, clicking")
        robust_click(driver, ea, "easy_apply")
        time.sleep(4)  # let modal animate in

        driver.save_screenshot(str(OUT / "post_click.png"))

        # Find ALL elements with apply-form-like content
        info = driver.execute_script("""
            const out = {};
            // Any element whose text contains "Apply to" or "Contact info"
            const all = document.querySelectorAll('*');
            const matches = [];
            for (const el of all) {
                if (el.children.length > 8) continue;  // wrapper
                if (el.offsetParent === null) continue;
                const t = (el.textContent || '').substring(0, 200);
                if (/^Apply to /.test(t.trim()) || /^Contact info/.test(t.trim())) {
                    matches.push({
                        tag: el.tagName,
                        id: el.id || '',
                        cls: (el.className || '').toString().substring(0, 200),
                        role: el.getAttribute('role') || '',
                        ariaLabel: el.getAttribute('aria-label') || '',
                        ariaModal: el.getAttribute('aria-modal') || '',
                        text: t.substring(0, 80),
                    });
                }
            }
            out.applyToMatches = matches.slice(0, 10);

            // Any [role='dialog']
            out.dialogs = Array.from(document.querySelectorAll("[role='dialog']")).map(d => ({
                cls: (d.className || '').toString().substring(0, 150),
                ariaLabel: d.getAttribute('aria-label') || '',
                visible: d.offsetParent !== null,
                text: (d.textContent || '').substring(0, 100),
            }));

            // Any [aria-modal='true']
            out.modals = Array.from(document.querySelectorAll("[aria-modal='true']")).map(d => ({
                tag: d.tagName,
                cls: (d.className || '').toString().substring(0, 150),
                visible: d.offsetParent !== null,
                text: (d.textContent || '').substring(0, 100),
            }));

            // Form structure
            out.forms = Array.from(document.querySelectorAll('form')).map(f => ({
                cls: (f.className || '').toString().substring(0, 150),
                visible: f.offsetParent !== null,
                inputCount: f.querySelectorAll('input, select, textarea').length,
                text: (f.textContent || '').substring(0, 100),
            }));

            // Find the modal by climbing from a "Next" button if any
            const nextBtns = Array.from(document.querySelectorAll('button')).filter(b => {
                if (b.offsetParent === null) return false;
                return /^next$/i.test((b.textContent || '').trim());
            });
            out.nextButtons = nextBtns.map(b => {
                let p = b.parentElement;
                const ancestors = [];
                while (p && ancestors.length < 8) {
                    ancestors.push({
                        tag: p.tagName,
                        cls: (p.className || '').toString().substring(0, 100),
                        role: p.getAttribute('role') || '',
                        ariaModal: p.getAttribute('aria-modal') || '',
                    });
                    p = p.parentElement;
                }
                return {btnText: (b.textContent || '').trim(), ancestors};
            });

            return out;
        """)

        import json
        (OUT / "modal_structure.json").write_text(json.dumps(info, indent=2))
        logger.info("applyTo matches: {}", len(info.get("applyToMatches", [])))
        for m in info.get("applyToMatches", [])[:5]:
            logger.info("  applyTo: {}", m)
        logger.info("dialogs: {}", info.get("dialogs"))
        logger.info("modals (aria-modal=true): {}", info.get("modals"))
        logger.info("forms: {}", info.get("forms"))
        logger.info("Next buttons found: {}", len(info.get("nextButtons", [])))
        for nb in info.get("nextButtons", [])[:1]:
            for a in nb["ancestors"]:
                logger.info("  ancestor: <{}> role={!r} aria-modal={!r} cls={!r}",
                            a["tag"], a["role"], a["ariaModal"], a["cls"][:80])

        time.sleep(3)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "4408058876")
