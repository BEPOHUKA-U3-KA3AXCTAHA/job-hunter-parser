"""Fallback handler for unknown ATS — Camoufox/Playwright async.

Best-effort: heuristic name/email/linkedin/location fill from CSS-substring
matches, resume upload, then page-snapshot Claude pass for everything else
(custom comboboxes, EEO dropdowns, weird radio groups, country pickers).
Up to 3 attempts because cross-field validation often only surfaces post-
submit.

Camoufox-backed = passes Cloudflare Turnstile fingerprint check silently;
Selenium-flavored ancestor of this file kept failing on Rippling/Aalyria.
"""
from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from loguru import logger

from app.modules.automation.adapters.external_apply.base import (
    AtsContext,
    AtsResult,
    click_button_by_text,
    detect_form_errors,
    fill_input,
    upload_resume,
)
from app.modules.automation.ports.external_apply import AtsHandler


class GenericHandler(AtsHandler):
    name = "generic"

    def can_handle(self, url: str) -> bool:
        return True   # last-resort fallback

    async def _fill_phone_with_country(self, page, profile_phone: str) -> bool:
        """Fill the phone field, handling country-code combobox if present.

        Many ATSes (Rippling, YC, Greenhouse Boards) render phone as
        `[country combobox] [digits input]` and reject values that
        include a `+XXX` prefix mismatching the combobox selection.
        Strategy:
          1. Find the empty phone input (placeholder/id/name/inputmode).
          2. Look up to 5 ancestors for a sibling [role=combobox] —
             that's the country picker.
          3. If found, type the country (best-effort by phone prefix
             mapping) into combobox, click matching option, drop the
             +XXX prefix from the digits.
          4. Fill the bare digits into the phone input.
        """
        import re
        # Step 1 — locate empty phone input
        phone_loc = None
        for sel in [
            "input[type=tel]",
            "input[inputmode=tel]",
            "input[name*='phone' i]",
            "input[id*='phone' i]",
            "input[placeholder*='phone' i]",
        ]:
            try:
                cand = page.locator(sel).first
                if await cand.count() == 0:
                    continue
                val = (await cand.input_value()).strip() if await cand.is_editable() else ""
                if not val:
                    phone_loc = cand
                    logger.info("phone: found empty field via {}", sel)
                    break
            except Exception:
                continue
        if phone_loc is None:
            return False

        # Step 2 — find sibling country combobox via JS walking ancestors
        combo_id = await page.evaluate("""(sel) => {
            const phone = document.querySelector(sel);
            if (!phone) return null;
            let p = phone.parentElement;
            for (let i = 0; i < 5 && p; i++) {
                const cands = p.querySelectorAll('[role=combobox], input[aria-haspopup=listbox]');
                for (const c of cands) {
                    if (c === phone) continue;
                    if (c.id) return c.id;
                }
                p = p.parentElement;
            }
            return null;
        }""", "input[type=tel], input[inputmode=tel], input[name*='phone' i]")
        logger.info("phone: country combobox sibling = {}", combo_id or "none")

        digits = profile_phone
        if combo_id and digits.startswith("+"):
            # Strip +XXX prefix — the digits input + country picker are
            # validated separately on most ATSes.
            digits = re.sub(r"^\+\d{1,3}\s*", "", digits)

        # Step 3 — pick country in combobox if present
        if combo_id and profile_phone.startswith("+"):
            try:
                combo = page.locator(f"#{combo_id}").first
                await combo.scroll_into_view_if_needed(timeout=1500)
                await combo.click(force=True, timeout=2000)
                await asyncio.sleep(0.4)
                # Type the digits prefix (e.g. "382" for Montenegro) —
                # works on Headless-UI substring filter without needing
                # to know the country name.
                m = re.match(r"^\+(\d{1,4})", profile_phone)
                if m:
                    prefix = m.group(1)[:4]
                    try:
                        await combo.press("Control+A")
                        await combo.press("Delete")
                        await combo.press_sequentially(prefix, delay=80)
                    except Exception:
                        # DIV combobox: focus + page-keyboard
                        await combo.focus()
                        await page.keyboard.type(prefix, delay=80)
                    await asyncio.sleep(0.7)
                    # Click first matching option whose text contains
                    # the prefix (e.g. "+382 ME - ..." for "382").
                    opt = page.get_by_role("option", name=re.compile(prefix), exact=False).first
                    if await opt.count() > 0:
                        await opt.click(force=True, timeout=2000)
                        await asyncio.sleep(0.4)
                        logger.info("phone: country set via prefix {!r}", prefix)
                    else:
                        # Esc to clear — don't leave dropdown open
                        try: await page.keyboard.press("Escape")
                        except Exception: pass
            except Exception as e:
                logger.debug("phone country picker setup failed: {}", e)

        # Step 4 — fill the bare digits. Re-find input (React may have
        # rebuilt the DOM after country change).
        await asyncio.sleep(0.3)
        for sel in [
            "input[inputmode=tel]",
            "input[type=tel]",
            "input[placeholder*='phone' i]",
        ]:
            try:
                cand = page.locator(sel).first
                if await cand.count() == 0:
                    continue
                cur = (await cand.input_value()).strip()
                if cur:
                    continue  # already filled — skip
                await cand.fill(digits, timeout=2000)
                logger.info("phone: filled digits {!r} via {}", digits, sel)
                return True
            except Exception:
                continue
        return False

    async def apply(self, page, ctx: AtsContext) -> AtsResult:
        await asyncio.sleep(2.5)
        host = urlparse(page.url).netloc

        # ATS landing pages often show only a job description + an "Apply…"
        # button that takes you to the actual form. Match any leading "Apply"
        # word — covers "Apply", "Apply now", "Apply for this job", etc.
        if await click_button_by_text(page, r"^\s*apply\b", timeout=2):
            logger.info("generic[{}]: clicked landing-page Apply", host)
            await asyncio.sleep(2.5)
            ctx_pages = page.context.pages
            if len(ctx_pages) > 1:
                page = ctx_pages[-1]
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(1.5)

        filled = 0

        # Heuristic name/email/linkedin/location fill.
        for substr, value in [
            ("first", ctx.profile_first_name),
            ("last", ctx.profile_last_name),
            ("email", ctx.profile_email),
            ("linkedin", ctx.profile_linkedin),
            ("city", ctx.profile_location),
            ("location", ctx.profile_location),
            ("address", ctx.profile_location),
        ]:
            if not value:
                continue
            for sel in [
                f"input[name*='{substr}' i]",
                f"input[id*='{substr}' i]",
                f"input[placeholder*='{substr}' i]",
            ]:
                if await fill_input(page, sel, value):
                    filled += 1
                    break

        # Phone — special-case heuristic for sibling-combobox-country +
        # bare-digits-input shape (Rippling, YC, some Greenhouse Boards).
        # We do this BEFORE the LLM pass because:
        #   1. LLM's snapshot doesn't show the visual relationship between
        #      country combo and phone input — it sees them as unrelated.
        #   2. A wrong action plan (e.g. "+382" filled into the digits
        #      input) gets validation-rejected and locks the field for
        #      the rest of the loop.
        # If this heuristic doesn't apply (e.g. Greenhouse-style single
        # phone input with optional +XX prefix), it bails fast and the LLM
        # picks up the field via the standard path.
        if ctx.profile_phone:
            for _ in range(8):
                if await self._fill_phone_with_country(page, ctx.profile_phone):
                    filled += 1
                    break
                await asyncio.sleep(0.4)

        # Resume upload — try multiple selectors.
        for sel in [
            "input[type=file][name*='resume' i]",
            "input[type=file][id*='resume' i]",
            "input[type=file][name*='cv' i]",
            "input[type=file]",
        ]:
            if await upload_resume(page, sel, ctx.resume_path):
                filled += 1
                logger.info("generic: resume uploaded via {}", sel)
                break

        # LLM autofill loop. Strategy:
        #   attempt 1 — full form snapshot + Sonnet, fills everything
        #     visible. Slow (~80s) but covers all fields in one shot.
        #   attempt 2-N — INCREMENTAL: detect blockers via JS, hand
        #     Claude (Haiku) only those fields with their cached options.
        #     Fast (~5-15s) and avoids re-opening already-filled
        #     comboboxes (which clobbers their state in Headless-UI).
        from app.modules.automation.adapters.page_filler import (
            fill_form_via_page_snapshot, fill_blockers_incrementally,
            detect_required_blockers,
        )
        from app.modules.users import CandidateProfile
        profile = CandidateProfile()

        last_errors: list[str] = []
        MAX_ATTEMPTS = 2  # 1 full Sonnet pass + 1 incremental Haiku retry.
        # Conditional/hidden-on-load fields might survive but the user
        # would rather get a fast "fill 90%" + manual finish than wait
        # for 5 LLM cycles.
        options_cache: dict[str, dict] = {}
        last_blocker_ids: set[str] = set()

        for attempt in range(MAX_ATTEMPTS):
            if attempt == 0:
                logger.info("generic[{}] attempt 1/{}: full snapshot + Sonnet",
                            host, MAX_ATTEMPTS)
                done = await fill_form_via_page_snapshot(
                    page, profile.user_info or "",
                    prior_errors=last_errors,
                    options_cache=options_cache,
                )
            else:
                logger.info("generic[{}] attempt {}/{}: incremental blocker fill (Haiku)",
                            host, attempt + 1, MAX_ATTEMPTS)
                done = await fill_blockers_incrementally(
                    page, profile.user_info or "", options_cache,
                )
            logger.info("generic[{}] attempt {}: filled {} action(s)",
                        host, attempt + 1, done)
            filled += done

            await asyncio.sleep(1)
            submitted = await click_button_by_text(
                page,
                r"^\s*(submit application|submit|send message|send|apply)\s*$",
                timeout=3,
            )
            if not submitted:
                last_errors = await detect_form_errors(page) or [
                    "submit button still disabled — required fields missing"
                ]
                logger.warning("generic[{}] attempt {}/{}: submit disabled — {}",
                               host, attempt + 1, MAX_ATTEMPTS,
                               " | ".join(last_errors[:2])[:160])
                continue
            await asyncio.sleep(4)
            last_errors = await detect_form_errors(page)
            if not last_errors:
                break
            joined = " | ".join(last_errors[:3])
            logger.warning("generic[{}] attempt {}/{} rejected: {}",
                           host, attempt + 1, MAX_ATTEMPTS, joined[:200])

        if last_errors:
            # Diagnostic dump: what required fields are still empty?
            try:
                blockers = await page.evaluate(
                    """() => {
                        const out = [];
                        const all = document.querySelectorAll(
                            'input, textarea, select, [role=combobox], [role=radiogroup], [role=group]'
                        );
                        for (const el of all) {
                            const req = el.required ||
                                el.getAttribute('aria-required') === 'true';
                            const inv = el.getAttribute('aria-invalid') === 'true';
                            const isCombo = el.getAttribute('role') === 'combobox';
                            const isRadio = el.getAttribute('role') === 'radiogroup' ||
                                            el.getAttribute('role') === 'group';
                            // For DIV-based comboboxes there's no .value —
                            // selected text replaces the placeholder <p>
                            // inside. Compare against aria-label / 'Select'
                            // to detect "still placeholder = empty".
                            let val = '';
                            let empty = false;
                            if (isCombo) {
                                const text = (el.textContent || '').trim();
                                const ph = el.getAttribute('aria-label') || 'Select';
                                const t = text.toLowerCase();
                                // Placeholder is LITERAL — don't regex
                                // /^choose/ or we'll misclassify legit
                                // values like 'Choose not to disclose'.
                                empty = !text || t === ph.toLowerCase()
                                    || t === 'select' || t === 'select...';
                                val = text;
                            } else if (isRadio) {
                                // Radio group: empty if no descendant input is checked
                                const checked = el.querySelector('input:checked');
                                empty = !checked;
                                val = checked ? (checked.value || 'checked') : '';
                            } else {
                                val = (el.value || '').trim();
                                empty = !val && el.tagName !== 'BUTTON';
                            }
                            if ((req && empty) || inv) {
                                let lbl = '';
                                const id = el.id;
                                if (id) {
                                    const ll = document.querySelector(
                                        'label[for="' + id + '"], #' + id + '-label'
                                    );
                                    if (ll) lbl = (ll.textContent || '').trim().slice(0, 80);
                                }
                                if (!lbl) lbl = el.getAttribute('aria-label') ||
                                    el.getAttribute('placeholder') || '';
                                // Also walk up for context label
                                if (!lbl || lbl.length < 5 || /^select$/i.test(lbl)) {
                                    let p = el.previousElementSibling;
                                    if (p) {
                                        const t = (p.textContent || '').trim();
                                        if (t && t.length < 200) lbl = t.slice(0, 100);
                                    }
                                }
                                out.push({
                                    id: id, tag: el.tagName, req, inv,
                                    val: val.slice(0, 40), label: lbl.slice(0, 100),
                                });
                            }
                        }
                        return out;
                    }"""
                )
                if blockers:
                    logger.warning(
                        "generic[{}] STILL-BLOCKING fields ({}):", host, len(blockers),
                    )
                    for b in blockers[:15]:
                        logger.warning("  #{} {} req={} inv={} val={!r} label={!r}",
                                       b["id"], b["tag"], b["req"], b["inv"],
                                       b["val"], b["label"])
                # Save final screenshot for visual inspection
                await page.screenshot(path=f"/tmp/jhp_diag/aalyria_final.png", full_page=True)
                logger.info("saved /tmp/jhp_diag/aalyria_final.png")
            except Exception as e:
                logger.debug("blocker-dump failed: {}", e)
            joined = " | ".join(last_errors[:3])
            return AtsResult(
                success=False,
                detail=f"generic[{host}]: validation after {MAX_ATTEMPTS} attempts: {joined[:200]}",
                ats_name=self.name,
                fields_filled=filled,
            )
        return AtsResult(
            success=True,
            detail=f"generic[{host}]: clicked submit ({filled} fields)",
            ats_name=self.name,
            pages=1,
            fields_filled=filled,
        )
