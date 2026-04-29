"""After clicking Easy Apply, recursively walk Shadow DOM to find the modal."""
from __future__ import annotations
import sys, time, json
from pathlib import Path
from loguru import logger
from src.automation.selenium_bot import find_button_by_text, robust_click, selenium_firefox

OUT = Path("/tmp/jhp_diag")


def main(job_id="4408058876"):
    url = f"https://www.linkedin.com/jobs/view/{job_id}/"
    with selenium_firefox(headless=False) as driver:
        driver.get(url); time.sleep(5)
        ea = find_button_by_text(driver, r"easy apply\b", timeout=5)
        if not ea:
            logger.error("no EA"); return
        robust_click(driver, ea, "ea"); time.sleep(5)

        # Recursive walker that descends shadow roots
        info = driver.execute_script("""
            function walk(root, results, depth) {
                if (depth > 6) return;
                const all = root.querySelectorAll ? root.querySelectorAll('*') : [];
                for (const el of all) {
                    // Note: offsetParent doesn't work inside shadow roots reliably
                    const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                    if (rect && rect.width > 8 && rect.height > 8) {
                        const t = (el.textContent || '').substring(0, 80).trim();
                        const role = el.getAttribute && el.getAttribute('role');
                        const aria = el.getAttribute && el.getAttribute('aria-label');
                        if (role === 'dialog' || (aria || '').toLowerCase().includes('apply')) {
                            results.dialogs.push({tag: el.tagName, role, aria, text: t});
                        }
                        if (el.tagName === 'BUTTON' && /^next$/i.test(t)) {
                            results.nextBtns.push({text: t, depth});
                        }
                        if (el.tagName === 'INPUT') {
                            results.inputs.push({
                                type: el.type, name: el.name, placeholder: el.placeholder, depth,
                            });
                        }
                    }
                    if (el.shadowRoot) {
                        results.shadowRoots.push({tag: el.tagName, id: el.id, depth});
                        walk(el.shadowRoot, results, depth + 1);
                    }
                }
            }
            const r = {dialogs: [], nextBtns: [], inputs: [], shadowRoots: []};
            walk(document, r, 0);
            return r;
        """)
        logger.info("Shadow roots found: {}", len(info["shadowRoots"]))
        for sr in info["shadowRoots"][:10]:
            logger.info("  shadow on <{}> id={!r} depth={}", sr["tag"], sr["id"], sr["depth"])
        logger.info("Dialogs found (any depth): {}", len(info["dialogs"]))
        for d in info["dialogs"][:5]:
            logger.info("  dialog: {}", d)
        logger.info("Next buttons (any depth): {}", len(info["nextBtns"]))
        for n in info["nextBtns"][:3]:
            logger.info("  next: {}", n)
        logger.info("Inputs (any depth): {}", len(info["inputs"]))
        for i in info["inputs"][:8]:
            logger.info("  input: {}", i)

        # Also: dump the body innerHTML length and count Lever-specific markers
        markers = driver.execute_script("""
            const html = document.documentElement.outerHTML;
            return {
                len: html.length,
                hasLever: html.includes('lever') || html.includes('Lever'),
                hasApplyTo: html.includes('Apply to '),
                hasContactInfo: html.includes('Contact info'),
                hasNextLiteral: html.includes('>Next<'),
                hasFirstName: html.includes('First name'),
            };
        """)
        logger.info("page markers: {}", markers)

        (OUT / "shadow_dump.json").write_text(json.dumps(info, indent=2))
        time.sleep(2)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "4408058876")
