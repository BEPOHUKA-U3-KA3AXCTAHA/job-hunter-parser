"""Diagnostic for Rippling API-typeahead comboboxes (city/country).

Flow:
  1. Open Aalyria landing → click Apply → wait for form.
  2. List all combobox-like elements with their labels.
  3. For city/country/location combobox: try several typing methods,
     dump listbox after each, see which technique surfaces options.

Run: DISPLAY=:1 PYTHONPATH=. .venv/bin/python scripts/diag_rippling_typeahead.py
"""
from __future__ import annotations

import asyncio

from loguru import logger

from app.modules.automation.adapters.camoufox import browser_session

URL = (
    "https://ats.rippling.com/en-GB/aalyria-careers/jobs/"
    "d8ac14cd-efdc-4688-8c7c-821685da9b2c?source=LinkedIn"
)


DEEP_WALK = """
function* dn(r){const s=[r];while(s.length){const n=s.pop();if(!n)continue;
    if(n.nodeType===1)yield n;if(n.shadowRoot)s.push(n.shadowRoot);
    const k=n.children||n.childNodes||[];for(let i=k.length-1;i>=0;i--)s.push(k[i]);}}
"""


async def list_comboboxes(page) -> list[dict]:
    return await page.evaluate(
        "() => {"
        + DEEP_WALK
        + """
        const out = [];
        for (const el of dn(document)) {
            if (!el.getAttribute) continue;
            if (el.getAttribute('role') !== 'combobox') continue;
            const r = el.getBoundingClientRect();
            if (r.width < 4 || r.height < 4) continue;
            // Find associated label
            const labelledby = el.getAttribute('aria-labelledby');
            let labelText = '';
            if (labelledby) {
                const lbl = document.getElementById(labelledby);
                if (lbl) labelText = (lbl.textContent || '').trim();
            }
            if (!labelText) {
                labelText = el.getAttribute('aria-label') || '';
            }
            // Walk up to find nearby legend/label for hidden cases
            if (!labelText) {
                let p = el.parentElement;
                for (let i = 0; i < 4 && p; i++) {
                    const lbl = p.querySelector('label');
                    if (lbl) { labelText = (lbl.textContent || '').trim(); break; }
                    p = p.parentElement;
                }
            }
            out.push({
                id: el.id,
                tag: el.tagName,
                aria_autocomplete: el.getAttribute('aria-autocomplete') || '',
                aria_controls: el.getAttribute('aria-controls') || '',
                aria_haspopup: el.getAttribute('aria-haspopup') || '',
                label: labelText.slice(0, 80),
                rect: {x: r.x, y: r.y, w: r.width, h: r.height},
                inputs_in: el.querySelectorAll('input').length,
            });
        }
        return out;
        }"""
    )


async def dump_listbox(page, combo_id: str) -> dict:
    return await page.evaluate(
        "(cid) => {"
        + DEEP_WALK
        + """
        const el = document.getElementById(cid);
        const lb_id = el ? el.getAttribute('aria-controls') : null;
        const lb = lb_id ? document.getElementById(lb_id) : null;
        const lb_options = [];
        if (lb) {
            for (const o of lb.querySelectorAll('[role=option], li, .option')) {
                const t = (o.textContent || '').trim();
                if (t && t.length < 100) lb_options.push(t);
                if (lb_options.length >= 30) break;
            }
        }
        const global_options = [];
        for (const o of dn(document)) {
            if (o.getAttribute && o.getAttribute('role') === 'option') {
                const r = o.getBoundingClientRect();
                if (r.width < 1 || r.height < 1) continue;
                const t = (o.textContent || '').trim();
                if (t) global_options.push(t.slice(0, 80));
                if (global_options.length >= 30) break;
            }
        }
        return {
            aria_expanded: el ? el.getAttribute('aria-expanded') : null,
            listbox_id: lb_id, listbox_present: !!lb,
            listbox_options: lb_options,
            global_options: global_options,
        };
        }""",
        combo_id,
    )


async def js_inject_value(page, combo_id: str, value: str) -> bool:
    return await page.evaluate(
        "(args) => { const cid = args[0]; const val = args[1];"
        + DEEP_WALK
        + """
        const el = document.getElementById(cid);
        if (!el) return false;
        let input = el.querySelector('input');
        if (!input) {
            const sibs = [el.previousElementSibling, el.nextElementSibling];
            for (const s of sibs) {
                if (!s) continue;
                input = s.querySelector('input');
                if (input) break;
            }
        }
        if (!input) return false;
        const proto = Object.getPrototypeOf(input);
        const desc = Object.getOwnPropertyDescriptor(proto, 'value');
        if (desc && desc.set) desc.set.call(input, val);
        else input.value = val;
        input.dispatchEvent(new Event('input', {bubbles: true}));
        input.dispatchEvent(new Event('change', {bubbles: true}));
        return true;
        }""",
        [combo_id, value],
    )


async def main() -> None:
    async with browser_session(headless=False) as page:
        logger.info("opening landing → {}", URL)
        await page.goto(URL, wait_until="domcontentloaded")
        await asyncio.sleep(3)

        # Click landing-page Apply
        try:
            apply = page.get_by_role("button", name="Apply", exact=False).first
            if await apply.count() > 0:
                await apply.click()
                logger.info("clicked landing Apply")
        except Exception as e:
            logger.warning("apply click: {}", e)
        await asyncio.sleep(3)
        # Maybe a new tab opened
        if len(page.context.pages) > 1:
            page = page.context.pages[-1]
            await page.wait_for_load_state("domcontentloaded")
            logger.info("switched to new tab: {}", page.url)
        await asyncio.sleep(5)

        # List all comboboxes
        combos = await list_comboboxes(page)
        logger.warning("found {} combobox elements:", len(combos))
        for c in combos:
            logger.info("  #{} label={!r} autocomp={} controls={} popup={} inputs={}",
                        c["id"], c["label"], c["aria_autocomplete"],
                        c["aria_controls"], c["aria_haspopup"], c["inputs_in"])

        # Find ones likely to be city/country/location
        targets = [c for c in combos if any(
            kw in c["label"].lower() for kw in
            ["city", "country", "location", "place", "where", "live", "based"]
        )]
        logger.warning("targets (city/country/location): {} found", len(targets))
        for c in targets:
            logger.info("  TARGET #{} label={!r}", c["id"], c["label"])

        if not targets:
            # Fall back to ones with aria-autocomplete=list (typeahead)
            targets = [c for c in combos if c["aria_autocomplete"] == "list"][:2]
            logger.warning("falling back to autocomp=list combos: {}", len(targets))

        for combo in targets:
            cid = combo["id"]
            value = "Montenegro" if "country" in combo["label"].lower() else (
                "Bar" if any(w in combo["label"].lower() for w in ["city", "place", "where"])
                else "Montenegro"
            )
            logger.warning("=" * 70)
            logger.warning("=== Testing #{} '{}' with value '{}' ===",
                           cid, combo["label"], value)

            trigger = page.locator(f"#{cid}").first
            try:
                await trigger.scroll_into_view_if_needed(timeout=2000)
            except Exception:
                pass

            methods = [
                ("page.keyboard.type slow",
                 lambda: page.keyboard.type(value, delay=120)),
                ("trigger.press_sequentially",
                 lambda: trigger.press_sequentially(value, delay=140)),
                ("js_inject_value",
                 lambda: js_inject_value(page, cid, value)),
            ]

            for name, action in methods:
                logger.warning(f"--- METHOD: {name} ---")
                # Open dropdown freshly
                try:
                    await trigger.click(timeout=2000)
                except Exception as e:
                    logger.warning("trigger.click: {}", e)
                await asyncio.sleep(0.7)
                pre = await dump_listbox(page, cid)
                logger.info("after click: aria_expanded={} listbox={} opts_lb={} opts_global={}",
                            pre["aria_expanded"], pre["listbox_id"],
                            len(pre["listbox_options"]), len(pre["global_options"]))

                # Apply method
                try:
                    res = await action()
                    logger.info("action returned: {}", res)
                except Exception as e:
                    logger.warning("action raised: {}", e)
                await asyncio.sleep(2.5)

                post = await dump_listbox(page, cid)
                logger.info("after type: opts_lb={} opts_global={}",
                            len(post["listbox_options"]), len(post["global_options"]))
                if post["listbox_options"]:
                    logger.info("  lb sample: {}", post["listbox_options"][:8])
                if post["global_options"]:
                    logger.info("  global sample: {}", post["global_options"][:8])

                # Reset
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.4)

        logger.info("done. Browser stays open 30s for inspection.")
        await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(main())
