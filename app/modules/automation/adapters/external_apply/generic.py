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

        # Phone — let the page-snapshot Claude pass handle it. Country pickers
        # vary so much across ATSes (Rippling combobox, YC <select>, plain
        # +XX prefix in the same input) that the LLM can do a better job
        # of seeing the structure than a hand-coded heuristic.

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

        # LLM autofill loop — page-snapshot Claude sees the WHOLE form +
        # whatever errors the prior submit raised, returns an action plan.
        from app.modules.automation.adapters.page_filler import (
            fill_form_via_page_snapshot,
        )
        from app.modules.users import CandidateProfile
        profile = CandidateProfile()

        last_errors: list[str] = []
        MAX_ATTEMPTS = 5  # forms with conditional fields (sponsorship→work
                          # permit country etc.) reveal new required inputs
                          # after each fill; need a few iterations to settle
        for attempt in range(MAX_ATTEMPTS):
            logger.info("generic[{}] attempt {}/{}: page-snapshot Claude pass",
                        host, attempt + 1, MAX_ATTEMPTS)
            done = await fill_form_via_page_snapshot(
                page, profile.user_info or "", prior_errors=last_errors,
            )
            logger.info("generic[{}] attempt {}: page-filler executed {} action(s)",
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
                                empty = !text || text.toLowerCase() === ph.toLowerCase()
                                    || /^(select|choose)\\b/i.test(text);
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
