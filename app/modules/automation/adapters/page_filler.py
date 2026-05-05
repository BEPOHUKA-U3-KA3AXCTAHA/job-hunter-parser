"""Page-snapshot LLM form filler (Camoufox/Playwright async).

When the field-by-field heuristic + per-field LLM pipeline can't finish
a form (some custom combobox, weird widget, missed label), fall back to:
  1. Snapshot the rendered form HTML.
  2. Hand it to Claude Sonnet with the user profile.
  3. Get back a JSON action list: [{action, selector, value}, ...].
  4. Execute each action via Playwright Page (Camoufox-backed).

This is the "let the model see the whole page" approach — bypasses all
per-ATS heuristics, works on any form including ones we've never seen.
"""
from __future__ import annotations

import asyncio
import json
import re

from loguru import logger


async def _collect_combobox_options(page) -> dict[str, list[str]]:
    """Open each [role=combobox] briefly, collect its option texts from the
    associated listbox, close again. Returns {combobox_id: [opt, ...]}.

    Why: Rippling/Headless-UI render listbox options into a portal in <body>
    only when the dropdown is open — they DON'T appear in form.outerHTML.
    Without this, Claude has to guess the option strings (e.g. "Montenegro"
    when the actual option is "+382 ME"), and the picker silently rejects
    non-matching values.
    """
    combos = await page.evaluate(
        """() => {
            const out = [];
            for (const el of document.querySelectorAll('[role=combobox]')) {
                if (!el.id) continue;
                const r = el.getBoundingClientRect();
                if (r.width < 4 || r.height < 4) continue;
                // Find a meaningful label: aria-labelledby > <label for=id> >
                // <legend> in nearest fieldset > nearest preceding label/text
                let label = '';
                const lbId = el.getAttribute('aria-labelledby');
                if (lbId) {
                    const e = document.getElementById(lbId);
                    if (e) label = (e.textContent || '').trim();
                }
                if (!label && el.id) {
                    const e = document.querySelector(
                        'label[for="' + el.id + '"]');
                    if (e) label = (e.textContent || '').trim();
                }
                if (!label) {
                    let p = el.parentElement;
                    for (let i = 0; i < 5 && p; i++) {
                        const fs = p.tagName === 'FIELDSET'
                            ? p.querySelector('legend') : null;
                        if (fs) { label = (fs.textContent || '').trim(); break; }
                        p = p.parentElement;
                    }
                }
                if (!label || label.length < 5 || /^select$/i.test(label)) {
                    // Walk back: prev sibling, parent's prev sibling, etc.
                    // grab visible text — these forms commonly render the
                    // question as a sibling <p> right before the combobox.
                    let p = el;
                    for (let i = 0; i < 6 && p; i++) {
                        const sib = p.previousElementSibling;
                        if (sib) {
                            const t = (sib.textContent || '').trim();
                            if (t && t.length > 5 && t.length < 300) {
                                label = t.slice(0, 200); break;
                            }
                        }
                        p = p.parentElement;
                    }
                }
                if (!label) label = el.getAttribute('aria-label') || '(no label)';
                out.push({id: el.id, label: label.slice(0, 200)});
            }
            return out;
        }"""
    )
    found: dict[str, dict] = {}
    for combo in combos:
        cid = combo["id"]
        label = combo.get("label", "")
        # Close any leftover open dropdown from the previous combo.
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        await asyncio.sleep(0.15)
        loc = page.locator(f"#{cid}").first
        try:
            await loc.scroll_into_view_if_needed(timeout=1500)
        except Exception:
            pass
        # Two attempts — Rippling sometimes needs a focus before the second
        # click registers as "open". Retry if first yields no options.
        opts: list[str] = []
        for attempt in range(2):
            try:
                await loc.click(timeout=2000, force=True)
            except Exception as e:
                logger.debug("combobox-options: click {} failed: {}", cid, e)
                break
            await asyncio.sleep(0.7)
            opts = await page.evaluate(
                """(cid) => {
                    const el = document.getElementById(cid);
                    if (!el) return [];
                    const lbId = el.getAttribute('aria-controls');
                    const lb = lbId ? document.getElementById(lbId) : null;
                    const seen = new Set();
                    const out = [];
                    // Prefer the scoped listbox; fall back to ALL visible
                    // options on the page (Rippling sometimes portals
                    // them to <body> without a stable id).
                    const sources = lb ? [lb] : [document];
                    for (const root of sources) {
                        for (const o of root.querySelectorAll('[role=option]')) {
                            const r = o.getBoundingClientRect();
                            if (r.width < 1 || r.height < 1) continue;
                            const t = (o.textContent || '').trim();
                            if (!t || t.length >= 100 || seen.has(t)) continue;
                            seen.add(t); out.push(t);
                            if (out.length >= 80) break;
                        }
                    }
                    return out;
                }""",
                cid,
            )
            if opts:
                break
            # Close + retry — perhaps the click just closed an
            # already-open dropdown the previous iteration left behind.
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            await asyncio.sleep(0.2)
        # Close so the next iteration starts cleanly.
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        await asyncio.sleep(0.15)
        if opts:
            found[cid] = {"options": opts, "label": label}
        else:
            logger.debug("combobox-options: no options collected for #{}", cid)
    return found


async def snapshot_form_html(page, max_chars: int = 120000) -> str:
    """Capture cleaned form HTML for the LLM, augmented with each combobox's
    real option strings (which live in a portal'd listbox, NOT in form HTML).

    Strips scripts/styles, big base64 data, oversized class names. Trims to
    fit the LLM context budget.
    """
    # Enrich first — get real option strings before we serialize the form.
    try:
        combo_options = await _collect_combobox_options(page)
    except Exception as e:
        logger.debug("combobox enrichment failed: {}", e)
        combo_options = {}

    raw = await page.evaluate(
        "() => { const r = document.querySelector('form') || document.body;"
        "  return r ? r.outerHTML : ''; }"
    ) or ""
    raw = re.sub(r"<script\b[\s\S]*?</script>", "", raw, flags=re.I)
    raw = re.sub(r"<style\b[\s\S]*?</style>", "", raw, flags=re.I)
    raw = re.sub(r"<svg\b[\s\S]*?</svg>", "<svg/>", raw, flags=re.I)
    raw = re.sub(r'\sclass="[^"]{60,}"', "", raw)
    raw = re.sub(r'(data:[a-z+/-]+;base64,)[A-Za-z0-9+/=]{100,}',
                 r'\1<truncated>', raw)
    raw = re.sub(r"\s+", " ", raw)

    # Inject the option strings as a clear preamble — the LLM should pick
    # values from these EXACT strings, not invent its own.
    if combo_options:
        preamble_lines = ["", "COMBOBOX FIELDS (use the EXACT option text as the value):"]
        for cid, payload in combo_options.items():
            opts = payload["options"]
            label = payload["label"]
            opts_short = " | ".join(o[:60] for o in opts[:30])
            preamble_lines.append(
                f"  #{cid}  question/label: {label!r}\n"
                f"             options: {opts_short}"
            )
        raw = "\n".join(preamble_lines) + "\n\n" + raw
        logger.info("snapshot: enriched with {} combobox option lists", len(combo_options))

    if len(raw) > max_chars:
        raw = raw[: max_chars // 2] + "\n...[truncated]...\n" + raw[-max_chars // 2 :]
    return raw


async def ask_claude_for_fill_plan(
    form_html: str, profile_text: str, prior_attempt_errors: list[str] | None = None,
) -> list[dict]:
    """Ask Claude Sonnet for a JSON action list to fill the form."""
    from app.modules.applies import get_claude_cli_pool

    error_block = ""
    if prior_attempt_errors:
        error_block = (
            "\n\nPRIOR SUBMIT ATTEMPT WAS REJECTED WITH THESE ERRORS — "
            "fix the missing/invalid fields:\n- "
            + "\n- ".join(prior_attempt_errors[:5])
        )

    system = (
        "You are a job-application form-filling assistant. Given the rendered HTML of a "
        "form and the candidate's profile, return a JSON array of actions that — when "
        "executed in order — will fill EVERY field needed to make the Apply button "
        "enable, INCLUDING ostensibly-voluntary EEO/demographic dropdowns (gender, "
        "race/ethnicity, veteran status, disability status). Many ATSes mark these "
        "'voluntary' in copy but disable submit until at least 'Decline to answer' is "
        "picked — pick a 'Decline to specify' / 'I don't wish to answer' / 'Prefer not "
        "to say' option for personal demographics. Do NOT include actions for "
        "already-filled fields."
    )
    user = f"""Return ONLY a JSON array, no prose. Each item:
{{
  "action": "fill" | "click" | "select_combobox",
  "selector": "<unique CSS selector that resolves to the target element>",
  "value": "<text to type, or option-text for combobox/select>"
}}

Action types:
- "fill": type into <input>/<textarea>. Selector points to the field.
- "click": click the element (radios, button-style toggles, single Yes/No buttons,
  checkboxes, custom buttons that open something). For radio groups, the selector
  must point to the SPECIFIC option you want chosen (e.g. label[for='us_auth_no']
  or input[value='No'][name='us_auth']).
- "select_combobox": for ARIA combobox / custom dropdown. Selector points to the
  combobox trigger. Value is the visible text of the option to pick.

Rules:
- Use the SHORTEST unique CSS selector. Prefer id (#field-X), name attribute
  ([name="phone"]), or attribute combinations. Avoid CSS-in-JS class names — they
  change on every render.
- For phone with a separate country combobox: emit a select_combobox for the
  country picker first (value = country name from the dropdown options like
  "Montenegro"), THEN a fill for the phone digits (without the +XXX prefix).
- Yes/No questions about US visa/sponsorship: NO to "authorized to work in US",
  YES to "require sponsorship".
- EEO / demographic dropdowns (gender, race, ethnicity, veteran, disability):
  ALWAYS pick a "Decline to specify" / "Prefer not to answer" / "I don't wish to
  answer" option if available. If the only options are concrete categories, pick
  the most accurate (Sergey is male, Russian, no military service, no disability).
- For ANY combobox: the EXACT option strings are listed at the very top of
  the form snippet under "COMBOBOX OPTIONS". Use one of those strings VERBATIM
  as `value` — the executor opens the dropdown and clicks the matching option,
  so your value MUST match exactly. If a phone-country picker shows options
  like "+382 ME - Montenegro", that ENTIRE string is the value, not just
  "Montenegro" or "+382".
{error_block}

CANDIDATE PROFILE:
{profile_text}

FORM HTML:
{form_html}
"""
    pool = get_claude_cli_pool(workers=1, model="claude-sonnet-4-6", timeout_s=180)
    results = await pool.batch_generate([(system, user)])
    if not results or not results[0].ok:
        logger.warning("page-filler: Claude call failed: {}",
                       results[0].error if results else "no result")
        return []
    text = results[0].text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    m = re.search(r"\[[\s\S]*\]", text)
    if not m:
        logger.warning("page-filler: no JSON array in response: {}", text[:300])
        return []
    try:
        actions = json.loads(m.group(0))
    except Exception as e:
        logger.warning("page-filler: JSON parse failed: {} | text={}", e, text[:300])
        return []
    if not isinstance(actions, list):
        return []
    logger.info("page-filler: Claude returned {} action(s)", len(actions))
    return actions


# JS used by execute_actions to walk shadow DOM when querying options.
_DEEP_WALK = """
function* dn(r){const s=[r];while(s.length){const n=s.pop();if(!n)continue;
    if(n.nodeType===1)yield n;if(n.shadowRoot)s.push(n.shadowRoot);
    const k=n.children||n.childNodes||[];for(let i=k.length-1;i>=0;i--)s.push(k[i]);}}
"""


async def execute_actions(page, actions: list[dict]) -> int:
    """Execute the action list against the live form. Returns count succeeded."""
    done = 0
    for act in actions:
        action = (act.get("action") or "").lower()
        sel = act.get("selector") or ""
        value = act.get("value") or ""
        if not action or not sel:
            continue
        try:
            loc = page.locator(sel).first
            try:
                await loc.scroll_into_view_if_needed(timeout=1500)
            except Exception:
                pass
            await asyncio.sleep(0.15)
            if action == "fill":
                try:
                    await loc.click(timeout=2000)
                except Exception:
                    pass
                try:
                    await loc.fill("", timeout=1000)
                except Exception:
                    pass
                try:
                    await loc.fill(value, timeout=2000)
                    done += 1
                except Exception as e:
                    logger.debug("page-filler: fill failed for {}: {}", sel[:60], e)
            elif action == "click":
                clicked = False
                try:
                    await loc.click(timeout=2000)
                    clicked = True
                except Exception:
                    # Hidden radio/checkbox — visible target is sibling label
                    # or ancestor [role=radio] / [role=button]. Find via JS.
                    target = await page.evaluate(
                        "(_sel) => {"
                        + _DEEP_WALK
                        + """
                        let el = null;
                        for (const e of dn(document)) {
                            if (e.matches && e.matches(_sel)) { el = e; break; }
                        }
                        if (!el) return null;
                        if (el.id) {
                            const lbl = document.querySelector(
                                'label[for="' + CSS.escape(el.id) + '"]');
                            if (lbl) {
                                lbl.scrollIntoView({block:'center'});
                                const r = lbl.getBoundingClientRect();
                                return {x: r.x + r.width/2, y: r.y + r.height/2};
                            }
                        }
                        let p = el.parentElement;
                        for (let i = 0; i < 5 && p; i++) {
                            if (p.tagName === 'LABEL') {
                                p.scrollIntoView({block:'center'});
                                const r = p.getBoundingClientRect();
                                return {x: r.x + r.width/2, y: r.y + r.height/2};
                            }
                            const role = p.getAttribute && p.getAttribute('role');
                            if (role === 'radio' || role === 'checkbox' || role === 'button') {
                                p.scrollIntoView({block:'center'});
                                const r = p.getBoundingClientRect();
                                return {x: r.x + r.width/2, y: r.y + r.height/2};
                            }
                            p = p.parentElement;
                        }
                        return null;
                        }""",
                        sel,
                    )
                    if target:
                        try:
                            await page.mouse.click(target["x"], target["y"])
                            clicked = True
                        except Exception as e:
                            logger.debug("page-filler: fallback click failed: {}", e)
                if clicked:
                    done += 1
            elif action == "select_combobox":
                # Rippling/Headless-UI/Ashby comboboxes have a wrapper DIV
                # whose click does NOTHING — the actual click target is an
                # INNER input[role='combobox'] (or [data-input*='search']).
                # Try inner first; fall back to wrapper.
                inner_sel_candidates = [
                    f"{sel} input[role='combobox']",
                    f"{sel} input[data-input*='search']",
                    f"{sel} input",
                ]
                trigger = None
                for inner_sel in inner_sel_candidates:
                    cand = page.locator(inner_sel).first
                    if await cand.count() > 0:
                        trigger = cand
                        break
                if trigger is None:
                    trigger = loc

                # 1) Open dropdown.
                try:
                    await trigger.click(timeout=2000)
                except Exception as e:
                    logger.debug("page-filler: combobox trigger click failed: {}", e)
                await asyncio.sleep(0.8)  # let listbox render (Rippling lazy)

                # 1b) Rippling/Headless-UI: combobox div has aria-controls
                # pointing to the listbox id (often portal'd to <body>).
                # Scope option search to that listbox — get_by_role is too
                # global and matches stale options from other comboboxes.
                listbox_id = None
                try:
                    # Get aria-controls from the combobox we clicked on.
                    listbox_id = await page.locator(sel).first.get_attribute(
                        "aria-controls", timeout=500,
                    )
                except Exception:
                    pass

                clicked = False

                async def _click_option(want: str) -> bool:
                    """Find an option matching `want` and click it. Tries
                    progressively looser matches so a value like
                    "+382 ME - Montenegro" still matches an actual option
                    "+382 ME - United States" (Claude often invents the
                    descriptive suffix because virtual-scroll hides the
                    full option list at snapshot time).

                    Match priority:
                      1. exact text (case-insensitive)
                      2. option starts with `want`
                      3. `want` startswith option (for short option labels)
                      4. distinctive-prefix match — first 2 alphanum tokens
                         of `want` (e.g. "+382 ME") found at start of option
                      5. substring (option contains want)

                    Uses `force=True` on click — sticky submit groups often
                    overlap the listbox bottom; force bypasses that.
                    """
                    if listbox_id:
                        scope_selectors = [
                            f"#{listbox_id} [role='option']",
                            "[role='listbox'] [role='option']",
                            "[role='option']",
                        ]
                    else:
                        scope_selectors = [
                            "[role='listbox'] [role='option']",
                            "[role='option']",
                        ]
                    want_low = want.strip().lower()
                    # Distinctive prefix = first 1-2 non-space tokens
                    # joined. For "+382 ME - Montenegro" → "+382 me".
                    tokens = re.findall(r"\S+", want)
                    distinctive = (" ".join(tokens[:2]).lower()
                                   if len(tokens) >= 2 else
                                   (tokens[0].lower() if tokens else ""))
                    for scope in scope_selectors:
                        opts = page.locator(scope)
                        n = await opts.count()
                        if n == 0:
                            continue
                        exact_idx = pfx_idx = rev_pfx_idx = dist_idx = sub_idx = -1
                        for i in range(min(n, 80)):
                            try:
                                t = (await opts.nth(i).text_content()) or ""
                            except Exception:
                                continue
                            # Normalize whitespace (Rippling uses U+00A0)
                            t_low = re.sub(r"\s+", " ", t.strip()).lower()
                            if not t_low:
                                continue
                            if t_low == want_low and exact_idx < 0:
                                exact_idx = i
                            elif t_low.startswith(want_low) and pfx_idx < 0:
                                pfx_idx = i
                            elif want_low.startswith(t_low) and len(t_low) >= 3 and rev_pfx_idx < 0:
                                rev_pfx_idx = i
                            elif distinctive and t_low.startswith(distinctive) and dist_idx < 0:
                                dist_idx = i
                            elif want_low in t_low and sub_idx < 0:
                                sub_idx = i
                        pick = -1
                        for cand in (exact_idx, pfx_idx, rev_pfx_idx, dist_idx, sub_idx):
                            if cand >= 0:
                                pick = cand
                                break
                        if pick >= 0:
                            try:
                                await opts.nth(pick).scroll_into_view_if_needed(
                                    timeout=1500,
                                )
                            except Exception:
                                pass
                            try:
                                await opts.nth(pick).click(timeout=2000, force=True)
                                return True
                            except Exception as e:
                                logger.debug("option click failed: {}", e)
                    return False

                clicked = await _click_option(value)

                # Not in the (possibly virtual-scrolled) listbox view —
                # filter by typing. Use press_sequentially (real char-by-char
                # keystrokes scoped to the trigger element) instead of
                # locator.fill — Headless-UI / Rippling listens for React
                # onChange driven by KeyboardEvent, not the bare native
                # setter. Pick ONE distinctive prefix instead of looping
                # candidates so the user doesn't see churn:
                #   - if value has digits → use first 3-4 digits
                #     (e.g. "+382 ME - Montenegro" → "382" filters cleanly)
                #   - else use the last word (e.g. "Bar, Montenegro" → "Bar")
                #   - else first 5 chars of trimmed value
                if not clicked:
                    digits = re.sub(r"\D", "", value)
                    if digits:
                        type_str = digits[:4]
                    else:
                        last_word = re.split(r"[\s,]+", value.strip())[-1]
                        type_str = last_word[:8] if last_word else value[:5]
                    try:
                        is_input = await trigger.evaluate(
                            "el => el.tagName === 'INPUT' || el.isContentEditable"
                        )
                        if is_input:
                            # Element-scoped clear + slow type — guaranteed
                            # to land on this input regardless of focus
                            # state, no risk of falling through to the
                            # browser address bar.
                            await trigger.click()
                            await trigger.press("Control+A")
                            await trigger.press("Delete")
                            await trigger.press_sequentially(type_str, delay=80)
                        else:
                            # DIV combobox — click already opened it; type
                            # via page.keyboard since the DIV won't accept
                            # locator.press_sequentially.
                            await trigger.focus()
                            await page.keyboard.type(type_str, delay=80)
                        await asyncio.sleep(0.9)
                        clicked = await _click_option(value)
                    except Exception as e:
                        logger.debug(
                            "page-filler: type-filter prefix={!r} failed: {}",
                            type_str, e,
                        )

                if clicked:
                    logger.info(
                        "select_combobox: {} → {!r} ✓", sel, value,
                    )
                    done += 1
                    await asyncio.sleep(0.4)
                    continue
                logger.warning(
                    "select_combobox: {} → {!r} ✗ (no option matched)",
                    sel, value,
                )
                option_xy = None
                # Find and click the option whose visible text matches `value`.
                option_xy = await page.evaluate(
                    "(_val) => { const want = _val.toLowerCase();"
                    + _DEEP_WALK
                    + """
                    let contains = null;
                    for (const el of dn(document)) {
                        if (el.getAttribute && el.getAttribute('role') === 'option') {
                            const r = el.getBoundingClientRect();
                            if (r.width < 1 || r.height < 1) continue;
                            const t = (el.textContent || '').trim().toLowerCase();
                            if (!t) continue;
                            if (t === want) {
                                el.scrollIntoView({block:'center'});
                                const r2 = el.getBoundingClientRect();
                                return {x: r2.x + r2.width/2, y: r2.y + r2.height/2};
                            }
                            if (!contains && t.includes(want)) contains = el;
                        }
                    }
                    if (contains) {
                        contains.scrollIntoView({block:'center'});
                        const r2 = contains.getBoundingClientRect();
                        return {x: r2.x + r2.width/2, y: r2.y + r2.height/2};
                    }
                    return null;
                    }""",
                    value,
                )
                if not option_xy and re.search(
                    r"decline|prefer not|don'?t (wish|want)|"
                    r"not (specified|specify|identify|disclose|to)|"
                    r"do not wish|choose not", value, re.I,
                ):
                    option_xy = await page.evaluate(
                        "() => {"
                        + _DEEP_WALK
                        + r"""
                        const re1 = /decline|undisclosed|not provided|prefer not/i;
                        const re2 = /\bnot\b[^.]{0,20}\b(disclose|specify|specified|identify|answer|say|tell|share)\b/i;
                        for (const el of dn(document)) {
                            if (el.getAttribute && el.getAttribute('role') === 'option') {
                                const r = el.getBoundingClientRect();
                                if (r.width < 1 || r.height < 1) continue;
                                const t = (el.textContent || '').trim();
                                if (t && (re1.test(t) || re2.test(t))) {
                                    el.scrollIntoView({block:'center'});
                                    const r2 = el.getBoundingClientRect();
                                    return {x: r2.x + r2.width/2, y: r2.y + r2.height/2};
                                }
                            }
                        }
                        return null;
                        }""",
                    )
                if option_xy:
                    await page.mouse.click(option_xy["x"], option_xy["y"])
                    done += 1
                else:
                    visible_opts = await page.evaluate(
                        "() => {"
                        + _DEEP_WALK
                        + """
                        const out = [];
                        for (const el of dn(document)) {
                            if (el.getAttribute && el.getAttribute('role') === 'option') {
                                const r = el.getBoundingClientRect();
                                if (r.width < 1 || r.height < 1) continue;
                                const t = (el.textContent || '').trim();
                                if (t) out.push(t);
                            }
                        }
                        return out.slice(0, 12);
                        }""",
                    ) or []
                    logger.warning("page-filler: option '{}' not in {} — visible: {}",
                                   value, sel[:50], visible_opts)
                    try:
                        await page.keyboard.press("Escape")
                    except Exception:
                        pass
            else:
                logger.debug("page-filler: unknown action {}", action)
        except Exception as e:
            logger.debug("page-filler: action {} on {} failed: {}", action, sel[:60], e)
        await asyncio.sleep(0.25)
    return done


async def fill_form_via_page_snapshot(
    page, profile_text: str, prior_errors: list[str] | None = None,
) -> int:
    """End-to-end: snapshot form, ask Claude, execute. Returns count of
    actions successfully executed (0 = no progress)."""
    html = await snapshot_form_html(page)
    if not html:
        return 0
    actions = await ask_claude_for_fill_plan(html, profile_text, prior_errors)
    if not actions:
        return 0
    return await execute_actions(page, actions)
