"""Page-snapshot LLM form filler.

When the field-by-field heuristic + per-field LLM pipeline can't finish
a form (some custom combobox, weird widget, missed label), fall back to:
  1. Snapshot the rendered form HTML.
  2. Hand it to Claude Sonnet with the user profile.
  3. Get back a JSON action list: [{action, selector, value}, ...].
  4. Execute each action via Selenium.

This is the "let the model see the whole page" approach — bypasses all
per-ATS heuristics, works on any form including ones we've never seen.
"""
from __future__ import annotations

import asyncio
import json
import re
import time

from loguru import logger
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys


def snapshot_form_html(driver, max_chars: int = 60000) -> str:
    """Capture cleaned form HTML for the LLM. Strips scripts/styles, big
    base64 data, and elements with no fillable controls. Trims to fit the
    LLM context budget."""
    raw = driver.execute_script(
        """
        const root = document.querySelector('form') || document.body;
        return root ? root.outerHTML : '';
        """
    ) or ""
    # Drop script/style/svg blocks
    raw = re.sub(r"<script\b[\s\S]*?</script>", "", raw, flags=re.I)
    raw = re.sub(r"<style\b[\s\S]*?</style>", "", raw, flags=re.I)
    raw = re.sub(r"<svg\b[\s\S]*?</svg>", "<svg/>", raw, flags=re.I)
    # Strip class attributes (CSS-in-JS dumps massive css-xxxx names)
    raw = re.sub(r'\sclass="[^"]{60,}"', "", raw)
    # Truncate base64 data: URLs
    raw = re.sub(r'(data:[a-z+/-]+;base64,)[A-Za-z0-9+/=]{100,}',
                 r'\1<truncated>', raw)
    # Collapse whitespace runs
    raw = re.sub(r"\s+", " ", raw)
    if len(raw) > max_chars:
        raw = raw[: max_chars // 2] + "\n...[truncated]...\n" + raw[-max_chars // 2 :]
    return raw


async def ask_claude_for_fill_plan(
    form_html: str, profile_text: str, prior_attempt_errors: list[str] | None = None,
) -> list[dict]:
    """Ask Claude Sonnet for a JSON action list to fill the form."""
    from app.modules.applies.adapters.llm.cli import ClaudeCLIPool

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
        "executed in order — will fill EVERY required (aria-required=true or marked '*') "
        "field correctly. Do NOT include actions for already-filled fields."
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
- Skip optional fields (no asterisk, no aria-required) unless very simple.
{error_block}

CANDIDATE PROFILE:
{profile_text}

FORM HTML:
{form_html}
"""
    pool = ClaudeCLIPool(workers=1, model="claude-sonnet-4-6", timeout_s=180)
    results = await pool.batch_generate([(system, user)])
    if not results or not results[0].ok:
        logger.warning("page-filler: Claude call failed: {}",
                       results[0].error if results else "no result")
        return []
    text = results[0].text.strip()
    # Strip code fences if present
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    # Find the first JSON array
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


def execute_actions(driver, actions: list[dict]) -> int:
    """Execute the action list against the live form. Returns count succeeded."""
    done = 0
    for i, act in enumerate(actions):
        action = (act.get("action") or "").lower()
        sel = act.get("selector") or ""
        value = act.get("value") or ""
        if not action or not sel:
            continue
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
        except Exception as e:
            logger.debug("page-filler: selector {} not found: {}", sel[:60], e)
            continue
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.15)
            if action == "fill":
                el.click()
                try:
                    el.clear()
                except Exception:
                    pass
                el.send_keys(value)
                done += 1
            elif action == "click":
                try:
                    ActionChains(driver).move_to_element(el).pause(0.05).click().perform()
                except Exception:
                    el.click()
                done += 1
            elif action == "select_combobox":
                try:
                    ActionChains(driver).move_to_element(el).pause(0.05).click().perform()
                except Exception:
                    el.click()
                time.sleep(0.5)
                # Try typing to filter (for searchable combos).
                try:
                    inner = None
                    try:
                        inner = el.find_element(By.CSS_SELECTOR,
                            "input[role='combobox'], input[data-input*='search']")
                    except Exception:
                        pass
                    typer = inner if inner else el
                    if typer.tag_name.lower() == "input":
                        typer.click()
                        typer.send_keys(Keys.CONTROL, "a")
                        typer.send_keys(Keys.DELETE)
                        typer.send_keys(value)
                        time.sleep(0.6)
                except Exception:
                    pass
                # Find and click the option whose visible text matches `value`.
                option = driver.execute_script(
                    """
                    function* dn(r){const s=[r];while(s.length){const n=s.pop();if(!n)continue;
                        if(n.nodeType===1)yield n;if(n.shadowRoot)s.push(n.shadowRoot);
                        const k=n.children||n.childNodes||[];for(let i=k.length-1;i>=0;i--)s.push(k[i]);}}
                    const want = arguments[0].toLowerCase();
                    let contains = null;
                    for (const el of dn(document)) {
                        if (el.getAttribute && el.getAttribute('role') === 'option') {
                            const r = el.getBoundingClientRect();
                            if (r.width < 1 || r.height < 1) continue;
                            const t = (el.textContent || '').trim().toLowerCase();
                            if (!t) continue;
                            if (t === want) return el;
                            if (!contains && t.includes(want)) contains = el;
                        }
                    }
                    return contains;
                    """,
                    value,
                )
                if option:
                    option.click()
                    done += 1
                else:
                    logger.debug("page-filler: option '{}' not found for {}", value, sel[:60])
                    try:
                        el.send_keys(Keys.ESCAPE)
                    except Exception:
                        pass
            else:
                logger.debug("page-filler: unknown action {}", action)
        except Exception as e:
            logger.debug("page-filler: action {} on {} failed: {}", action, sel[:60], e)
        time.sleep(0.25)
    return done


def fill_form_via_page_snapshot(
    driver, profile_text: str, prior_errors: list[str] | None = None,
) -> int:
    """End-to-end: snapshot form, ask Claude, execute. Returns count of
    actions successfully executed (0 = no progress)."""
    html = snapshot_form_html(driver)
    if not html:
        return 0
    actions = asyncio.run(ask_claude_for_fill_plan(html, profile_text, prior_errors))
    if not actions:
        return 0
    return execute_actions(driver, actions)
