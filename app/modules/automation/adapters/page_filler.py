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


async def snapshot_form_html(page, max_chars: int = 120000) -> str:
    """Capture cleaned form HTML for the LLM. Strips scripts/styles, big
    base64 data, and elements with no fillable controls. Trims to fit the
    LLM context budget."""
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
- For ANY combobox you can see in the HTML, infer the likely option set and emit
  select_combobox even if you can't see the option list inline — the executor
  will type your value to filter and click the matching option.
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
                    """Find an option matching `want` (exact-then-substring)
                    inside the active listbox, click it. Returns success."""
                    # Try the scoped listbox first; fall back to all options.
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
                    for scope in scope_selectors:
                        opts = page.locator(scope)
                        n = await opts.count()
                        if n == 0:
                            continue
                        # Build a list of (idx, text) so we can pick the
                        # best match (exact > prefix > substring).
                        exact_idx = prefix_idx = sub_idx = -1
                        for i in range(min(n, 60)):
                            try:
                                t = (await opts.nth(i).text_content()) or ""
                            except Exception:
                                continue
                            t_low = t.strip().lower()
                            if not t_low:
                                continue
                            if t_low == want_low and exact_idx < 0:
                                exact_idx = i
                            elif t_low.startswith(want_low) and prefix_idx < 0:
                                prefix_idx = i
                            elif want_low in t_low and sub_idx < 0:
                                sub_idx = i
                        pick = exact_idx if exact_idx >= 0 else (
                            prefix_idx if prefix_idx >= 0 else sub_idx
                        )
                        if pick >= 0:
                            try:
                                await opts.nth(pick).scroll_into_view_if_needed(
                                    timeout=1500,
                                )
                                await opts.nth(pick).click(timeout=2000)
                                return True
                            except Exception as e:
                                logger.debug("option click failed: {}", e)
                    return False

                clicked = await _click_option(value)

                # 2) Not found → type to filter (only if there's an inner
                # input — Rippling's pure-DIV combobox accepts keystrokes
                # via .type(), which Playwright sends to the focused element.
                # `trigger` was set to inner-input above when one exists.
                if not clicked:
                    try:
                        # If trigger is an INPUT, fill works; otherwise fall
                        # back to page.keyboard.type AFTER ensuring focus
                        # via trigger.focus().
                        is_input = await trigger.evaluate(
                            "el => el.tagName === 'INPUT' || el.isContentEditable"
                        )
                        if is_input:
                            await trigger.fill(value, timeout=1500)
                        else:
                            # DIV combobox: focus first, then type. Playwright
                            # routes to focused element — and we just clicked
                            # this trigger so it has focus.
                            await trigger.focus()
                            await page.keyboard.type(value, delay=30)
                        await asyncio.sleep(0.7)
                        clicked = await _click_option(value)
                    except Exception as e:
                        logger.debug("page-filler: combobox filter+click failed: {}", e)

                if clicked:
                    done += 1
                    await asyncio.sleep(0.4)
                    continue
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
