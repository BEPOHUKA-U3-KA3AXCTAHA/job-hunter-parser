"""Inspect #field-136 (the post-sponsorship work-permit-country picker)
in detail — outerHTML, all attributes, ancestor chain, sibling inputs,
data-* attrs. We want to find a UNIQUE selector / xpath we haven't been
trying yet.

Run after manually filling form to sponsorship=Yes step, OR let the
script drive it (does same flow as test_camoufox_apply.py up to that
point).
"""
from __future__ import annotations

import asyncio

from loguru import logger

from app.modules.automation.adapters.camoufox import browser_session

URL = (
    "https://ats.rippling.com/en-GB/aalyria-careers/jobs/"
    "d8ac14cd-efdc-4688-8c7c-821685da9b2c?source=LinkedIn"
)


DUMP_JS = r"""(targetId) => {
    function* dn(r){const s=[r];while(s.length){const n=s.pop();if(!n)return;
        if(n.nodeType===1)yield n;if(n.shadowRoot)s.push(n.shadowRoot);
        const k=n.children||n.childNodes||[];
        for(let i=k.length-1;i>=0;i--)s.push(k[i]);}}
    let el = null;
    for (const e of dn(document)) {
        if (e.id === targetId) { el = e; break; }
    }
    if (!el) {
        // Find ALL search/typeahead inputs that don't have values
        const candidates = [];
        for (const e of dn(document)) {
            if (e.tagName !== 'INPUT') continue;
            const role = e.getAttribute && e.getAttribute('role') || '';
            const di = e.getAttribute && e.getAttribute('data-input') || '';
            const al = e.getAttribute && e.getAttribute('aria-label') || '';
            if (role === 'combobox' || di.includes('search') || al === 'Search') {
                candidates.push({
                    id: e.id, name: e.name, val: e.value,
                    aria_label: al, data_input: di,
                    placeholder: e.placeholder,
                    rect: e.getBoundingClientRect(),
                });
            }
        }
        return {error: 'not found', candidates};
    }
    const attrs = {};
    for (const a of el.attributes) attrs[a.name] = a.value;
    let p = el.parentElement;
    const chain = [];
    for (let i = 0; i < 6 && p; i++) {
        const pAttrs = {};
        for (const a of p.attributes) pAttrs[a.name] = a.value;
        chain.push({tag: p.tagName, attrs: pAttrs,
                    text: (p.textContent || '').trim().slice(0, 100)});
        p = p.parentElement;
    }
    // Try a few candidate selectors
    const selectorTrials = [];
    const trySel = (s) => {
        try {
            const m = document.querySelectorAll(s);
            selectorTrials.push({sel: s, count: m.length,
                                 first_id: m[0] ? m[0].id : null});
        } catch (e) {
            selectorTrials.push({sel: s, error: String(e)});
        }
    };
    trySel('#' + targetId);
    if (attrs.name) trySel(`input[name="${attrs.name}"]`);
    if (attrs['data-testid']) trySel(`[data-testid="${attrs['data-testid']}"]`);
    if (attrs['data-input']) trySel(`[data-input="${attrs['data-input']}"]`);
    if (attrs['aria-labelledby']) trySel(`[aria-labelledby="${attrs['aria-labelledby']}"]`);
    return {attrs, ancestor_chain: chain, selectorTrials};
}"""


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
        if len(page.context.pages) > 1:
            page = page.context.pages[-1]
            await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(5)

        # Pick sponsorship=Yes to reveal #field-136 (or whatever id it gets)
        # First we need to find the sponsorship combobox. Let's just dump
        # field-136 directly — it's there if sponsorship was Yes; if not,
        # we'll fall back to scanning all unfilled inputs.
        logger.warning("=" * 70)
        logger.warning("Direct dump of field-136 (assuming sponsorship Yes already set):")
        logger.warning("=" * 70)
        info = await page.evaluate(DUMP_JS, "field-136")
        for k, v in info.items():
            logger.info(f"{k}: {v}")

        logger.warning("=" * 70)
        logger.warning("Now setting sponsorship=Yes manually first, then re-dump...")
        logger.warning("=" * 70)
        # Find sponsorship combobox by label text
        try:
            spons = page.locator(
                '[role="combobox"]'
            ).filter(has_text=lambda t: True).first
            # That probably won't work — locate by surrounding text
        except Exception:
            pass

        # Find sponsorship combo: any [role=combobox] whose dropdown contains
        # exactly "Yes" and "No" options. Open each one in turn, peek options.
        all_combo_ids = await page.evaluate(
            "() => Array.from(document.querySelectorAll('[role=combobox]')).map(e => e.id)"
        )
        logger.info("all combobox ids: {}", all_combo_ids)
        sponsorship_id = None
        for cid in all_combo_ids:
            if not cid:
                continue
            try:
                await page.locator(f"#{cid}").first.click(force=True, timeout=2000)
                await asyncio.sleep(0.7)
                opts = await page.evaluate(
                    "() => Array.from(document.querySelectorAll('[role=option]'))"
                    ".map(o => (o.textContent||'').trim()).filter(t => t)"
                )
                logger.info("  #{} options: {}", cid, opts)
                normalized = sorted(t.lower() for t in opts if t)
                if normalized == ["no", "yes"]:
                    sponsorship_id = cid
                    logger.info("  → SPONSORSHIP COMBO FOUND")
                    # Click Yes
                    yes_opt = page.get_by_role("option", name="Yes", exact=True).first
                    await yes_opt.click(force=True)
                    await asyncio.sleep(2.5)
                    break
                # Close it for the next iteration
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.3)
            except Exception as e:
                logger.debug(f"trying {cid}: {e}")
        if not sponsorship_id:
            logger.warning("sponsorship combo not found via Yes/No probing")

        logger.warning("=" * 70)
        logger.warning("Post-sponsorship dump: looking for newly-revealed inputs")
        logger.warning("=" * 70)
        info = await page.evaluate(DUMP_JS, "field-136")
        if "error" in info:
            logger.warning(f"field-136 not found, trying field-137:")
            info2 = await page.evaluate(DUMP_JS, "field-137")
            for k, v in info2.items():
                logger.info(f"{k}: {v}")
        else:
            for k, v in info.items():
                logger.info(f"{k}: {v}")

        logger.warning("Browser stays open 30s for manual inspection")
        await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(main())
