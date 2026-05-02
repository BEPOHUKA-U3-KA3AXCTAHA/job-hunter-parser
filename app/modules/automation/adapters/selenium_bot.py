"""Selenium-driven Firefox bot for LinkedIn Easy Apply.

Why Selenium and not Camoufox/Playwright:
- Camoufox got soft-blocked on /jobs/view/ pages (LinkedIn served guest wall)
- Selenium with USER'S real Firefox profile = real fingerprint, real cookies,
  real history → LinkedIn sees a normal user

Strategy:
1. Copy user's Firefox profile to /tmp (avoid conflict with running Firefox)
2. Strip Marionette automation markers from preferences
3. Launch headless Firefox via Selenium with that profile
4. Navigate, smart text-based button find, click, walk modal
"""
from __future__ import annotations

import re
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from random import lognormvariate, uniform

from loguru import logger
from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.support.ui import WebDriverWait

from app.modules.automation.adapters.firefox_cookies import find_default_profile

PROFILE_COPY_DIR = Path("/tmp/jhp_ff_profile")
DIAG_DIR = Path("/tmp/jhp_diag")

MAX_MODAL_PAGES = 8
RATE_LIMIT_MARKERS = [
    "let's confirm it's really you",
    "we've restricted your account",
    "unusual activity",
    "complete this puzzle",
]


class ApplyOutcome(StrEnum):
    APPLIED = "applied"                   # full Easy Apply submitted
    INTEREST_SIGNALED = "interest_signaled"  # "I'm interested" clicked (LinkedIn's soft-signal)
    ALREADY_APPLIED = "already_applied"
    NO_EASY_APPLY = "no_easy_apply"       # neither Easy Apply nor I'm interested
    TOO_MANY_QUESTIONS = "too_many_questions"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass
class ApplyResult:
    outcome: ApplyOutcome
    detail: str = ""
    pages: int = 0


def human_sleep(min_s: float = 0.6, max_s: float = 2.4) -> None:
    midpoint = (min_s + max_s) / 2
    sigma = (max_s - min_s) / 4
    delay = lognormvariate(0, 0.4) * midpoint
    delay = max(min_s, min(max_s, delay))
    delay += uniform(-sigma * 0.2, sigma * 0.2)
    time.sleep(max(0.05, delay))


def _prepare_profile_copy() -> Path:
    """Clone user's Firefox profile to /tmp so we don't conflict with their
    running Firefox. Strip Marionette / automation hints from prefs.
    """
    src = find_default_profile()
    if PROFILE_COPY_DIR.exists():
        shutil.rmtree(PROFILE_COPY_DIR)
    logger.info("Copying Firefox profile {} → {}", src.name, PROFILE_COPY_DIR)
    # Use ignore for big lock files / cache
    shutil.copytree(
        src, PROFILE_COPY_DIR,
        ignore=shutil.ignore_patterns(
            "lock", "parent.lock", ".parentlock",
            "cache2", "thumbnails", "startupCache",
            "minidumps", "datareporting", "saved-telemetry-pings",
            "shader-cache", "AlternateServices.bin",
        ),
    )
    # Make sure user.js doesn't disable cookies
    user_js = PROFILE_COPY_DIR / "user.js"
    user_js.write_text(
        # Disable Firefox automation banners and any "this profile is opened by another Firefox" check
        'user_pref("datareporting.healthreport.uploadEnabled", false);\n'
        'user_pref("dom.disable_open_during_load", false);\n'
        'user_pref("dom.webdriver.enabled", false);\n'
        'user_pref("useAutomationExtension", false);\n'
        'user_pref("browser.startup.homepage_override.mstone", "ignore");\n'
        'user_pref("browser.startup.page", 0);\n'
    )
    return PROFILE_COPY_DIR


@contextmanager
def selenium_firefox(headless: bool = False, copy_profile: bool = True):
    """Launch Selenium Firefox with user's profile (copied) for LinkedIn auth.

    Args:
        headless: True to run without window (some sites detect headless,
                  but for LinkedIn it generally works)
        copy_profile: True (recommended) → copy user's profile to /tmp
                      False → use a fresh empty profile (no LinkedIn auth)
    """
    options = FirefoxOptions()
    if headless:
        options.add_argument("--headless")
    if copy_profile:
        profile_path = str(_prepare_profile_copy())
        options.add_argument("-profile")
        options.add_argument(profile_path)
    # Hide automation flag (Selenium sets navigator.webdriver=true by default)
    options.set_preference("dom.webdriver.enabled", False)
    options.set_preference("useAutomationExtension", False)

    service = FirefoxService(log_output=str(Path("/tmp/jhp_geckodriver.log")))
    driver = webdriver.Firefox(options=options, service=service)
    driver.implicitly_wait(0)  # we'll handle waits explicitly
    if not headless:
        # Make the bot's Firefox impossible to miss when running alongside the
        # user's own Firefox instance.
        try:
            driver.set_window_position(0, 0)
            driver.set_window_size(1280, 900)
        except Exception:
            pass
    try:
        yield driver
    finally:
        try:
            driver.quit()
        except Exception:
            pass


# --- Diagnostics ---

def _diag_save(driver, tag: str) -> None:
    """Best-effort screenshot + html dump for failure diagnostics."""
    try:
        DIAG_DIR.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", tag)[:40]
        png = DIAG_DIR / f"{ts}_{safe}.png"
        html = DIAG_DIR / f"{ts}_{safe}.html"
        driver.save_screenshot(str(png))
        try:
            html.write_text(driver.page_source[:300_000])
        except Exception:
            pass
        logger.warning("diag saved: {}", png.name)
    except Exception as e:
        logger.debug("diag save failed: {}", e)


# --- DOM helpers (text-based, not class-based) ---
#
# CRITICAL: LinkedIn 2026 wraps the Easy Apply modal (and other dynamic UI)
# inside a Shadow DOM root attached to <div id="interop-outlet">. Native
# document.querySelector* DOES NOT cross shadow boundaries, so every helper
# below must use the deep walker JS_WALK_PROLOG below.

JS_WALK_PROLOG = """
function* deepNodes(root) {
    if (!root) return;
    const stack = [root];
    while (stack.length) {
        const node = stack.pop();
        if (!node) continue;
        if (node.nodeType === 1) yield node;
        if (node.shadowRoot) stack.push(node.shadowRoot);
        const kids = node.children || node.childNodes || [];
        // push in reverse so DOM order is preserved on pop
        for (let i = kids.length - 1; i >= 0; i--) stack.push(kids[i]);
    }
}
function isVisible(el) {
    // offsetParent doesn't work in shadow roots — use rect+style instead.
    const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
    if (!rect || rect.width < 4 || rect.height < 4) return false;
    const cs = (el.ownerDocument && el.ownerDocument.defaultView)
        ? el.ownerDocument.defaultView.getComputedStyle(el) : null;
    if (cs && (cs.visibility === 'hidden' || cs.display === 'none' || cs.opacity === '0')) return false;
    return true;
}
"""


def find_button_by_text(driver, text_regex: str, timeout: float = 4.0):
    """Find first VISIBLE clickable element whose text or aria-label matches regex.

    LinkedIn 2026: Easy Apply is rendered as <A> (anchor) WITHOUT role='button'
    and WITHOUT the legacy .jobs-apply-button class. The most stable selector
    is aria-label substring "Easy Apply to" (AIHawk-proven).

    text_regex: JS-style regex string like 'easy apply\\b'
    """
    end = time.monotonic() + timeout
    js = JS_WALK_PROLOG + """
        const re = new RegExp(arguments[0], 'i');
        const candidates = [];
        const ALLOW = new Set(['BUTTON', 'A']);
        for (const el of deepNodes(document)) {
            if (!ALLOW.has(el.tagName) && el.getAttribute('role') !== 'button') continue;
            if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
            if (!isVisible(el)) continue;
            const t = (el.textContent || '').trim();
            const a = (el.getAttribute('aria-label') || '').trim();
            if (!re.test(t) && !re.test(a)) continue;
            const rect = el.getBoundingClientRect();
            // Skip top global nav (Jobs/Messaging/etc) — they're at top:0
            if (rect.top < 50 && el.closest && el.closest('nav, header, [role="navigation"]')) continue;
            candidates.push(el);
        }
        if (!candidates.length) return null;
        // Priority 1: exact aria-label match "easy apply to"
        for (const b of candidates) {
            const a = (b.getAttribute('aria-label') || '').toLowerCase();
            if (a.includes('easy apply to')) return b;
        }
        // Priority 2: inside any [role='dialog'] (modal Next/Submit/Continue)
        for (const b of candidates) {
            let p = b;
            while (p) {
                if (p.getAttribute && p.getAttribute('role') === 'dialog') return b;
                p = p.parentNode || (p.host /* shadowRoot */);
            }
        }
        // Priority 3: top-card region
        for (const b of candidates) {
            if (b.closest && b.closest('.jobs-apply-button--top-card, .jobs-s-apply, .jobs-unified-top-card, .job-details-jobs-unified-top-card')) {
                return b;
            }
        }
        return candidates[0];
    """
    while time.monotonic() < end:
        el = driver.execute_script(js, text_regex)
        if el is not None:
            return el
        time.sleep(0.2)
    return None


def robust_click(driver, el, label: str = "btn") -> bool:
    """Click that survives overlays, animations, off-screen elements.
    Tries scrollIntoView → native click → JS click → Actions click.
    Returns True if any strategy didn't raise."""
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center', behavior:'instant'});", el,
        )
        time.sleep(0.3)
    except Exception:
        pass
    # Strategy 1: native
    try:
        el.click()
        logger.debug("{}: native click ok", label)
        return True
    except Exception as e:
        logger.debug("{}: native click failed: {}", label, e)
    # Strategy 2: JS click
    try:
        driver.execute_script("arguments[0].click();", el)
        logger.debug("{}: JS click ok", label)
        return True
    except Exception as e:
        logger.debug("{}: JS click failed: {}", label, e)
    # Strategy 3: Actions
    try:
        from selenium.webdriver.common.action_chains import ActionChains
        ActionChains(driver).move_to_element(el).pause(0.2).click().perform()
        logger.debug("{}: Actions click ok", label)
        return True
    except Exception as e:
        logger.debug("{}: Actions click failed: {}", label, e)
    return False


def wait_for_modal(driver, timeout: float = 8.0) -> bool:
    """Wait for the Easy Apply modal/dialog to appear after clicking Apply.

    LinkedIn 2026: modal lives inside Shadow DOM (#interop-outlet). Must walk
    through shadow roots to find [role='dialog'] with the apply form.
    """
    js = JS_WALK_PROLOG + """
        for (const d of deepNodes(document)) {
            if (d.getAttribute && d.getAttribute('role') === 'dialog' && isVisible(d)) {
                const t = (d.textContent || '').toLowerCase().substring(0, 800);
                if (/apply|contact info|first name|easy apply|submit application|review your application|dialog content/.test(t)) {
                    return true;
                }
            }
        }
        return false;
    """
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if driver.execute_script(js):
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def find_input_by_label(driver, label_regex: str):
    """Find first VISIBLE input whose label/placeholder/aria matches regex.
    Walks Shadow DOM."""
    js = JS_WALK_PROLOG + """
        const re = new RegExp(arguments[0], 'i');
        const TYPES = new Set(['text', 'tel', '', 'email']);
        for (const inp of deepNodes(document)) {
            if (inp.tagName !== 'INPUT') continue;
            const type = (inp.type || '').toLowerCase();
            if (!TYPES.has(type)) continue;
            if (!isVisible(inp)) continue;
            const id = inp.id || '';
            const name = inp.name || '';
            const placeholder = inp.placeholder || '';
            const aria = inp.getAttribute('aria-label') || '';
            // Find label across shadow boundaries — walk up from input
            let labelText = '';
            if (id) {
                const root = inp.getRootNode ? inp.getRootNode() : document;
                const labelEl = root.querySelector ? root.querySelector('label[for="' + id + '"]') : null;
                labelText = labelEl ? labelEl.textContent : '';
            }
            // Also try ancestor label
            if (!labelText) {
                let p = inp.parentNode;
                for (let i = 0; i < 4 && p; i++) {
                    if (p.tagName === 'LABEL') { labelText = p.textContent; break; }
                    p = p.parentNode;
                }
            }
            if (re.test([id, name, placeholder, aria, labelText].join(' '))) return inp;
        }
        return null;
    """
    return driver.execute_script(js, label_regex)


def is_blocked_page(driver) -> bool:
    try:
        text = driver.execute_script(
            "return (document.body.innerText || '').toLowerCase().substring(0, 5000);"
        )
    except Exception:
        return False
    return any(m in text for m in RATE_LIMIT_MARKERS)


def page_url(driver) -> str:
    try:
        return driver.current_url
    except Exception:
        return ""


def dump_buttons(driver, limit: int = 12) -> list[dict]:
    """Dump visible buttons across whole page including Shadow DOM."""
    js = JS_WALK_PROLOG + f"""
        const out = [];
        for (const b of deepNodes(document)) {{
            if (b.tagName !== 'BUTTON' && (b.getAttribute && b.getAttribute('role') !== 'button')) continue;
            if (b.disabled) continue;
            if (!isVisible(b)) continue;
            out.push({{
                text: (b.textContent || '').trim().substring(0, 50),
                aria: (b.getAttribute('aria-label') || '').substring(0, 80),
            }});
            if (out.length >= {limit}) break;
        }}
        return out;
    """
    try:
        return driver.execute_script(js)
    except Exception:
        return []


def extract_unfilled_questions(driver) -> list[dict]:
    """Walk the modal (Shadow-DOM aware) and return a list of unfilled fields.

    Each returned dict matches `FormQuestion` shape:
        {label, type, options, name, placeholder, required, _selector}
    `_selector` is an opaque CSS path the JS side uses to fill the answer back.
    """
    js = JS_WALK_PROLOG + """
        function nthOf(el) {
            const p = el.parentNode; if (!p) return 1;
            let i = 1;
            for (const c of p.children) {
                if (c === el) return i;
                if (c.tagName === el.tagName) i++;
            }
            return 1;
        }
        function cssPath(el) {
            // Best-effort path that a fillAnswers() walker can resolve later.
            // We tag with id when present, otherwise tag+nthOfType chain up to dialog ancestor.
            if (el.id) return '#' + CSS.escape(el.id);
            const parts = [];
            let cur = el;
            while (cur && cur.tagName) {
                if (cur.id) { parts.unshift('#' + CSS.escape(cur.id)); break; }
                parts.unshift(cur.tagName.toLowerCase() + ':nth-of-type(' + nthOf(cur) + ')');
                cur = cur.parentElement;
                if (parts.length > 8) break;
            }
            return parts.join(' > ');
        }
        function findLabel(el) {
            const root = el.getRootNode ? el.getRootNode() : document;
            // Strategy 1: <label for="id">
            if (el.id && root.querySelector) {
                const l = root.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                if (l) return (l.textContent || '').trim();
            }
            // Strategy 2: aria-labelledby
            const labelledBy = el.getAttribute('aria-labelledby');
            if (labelledBy && root.getElementById) {
                const l = root.getElementById(labelledBy);
                if (l) return (l.textContent || '').trim();
            }
            // Strategy 3: ancestor <label>
            let p = el.parentNode;
            for (let i = 0; i < 5 && p; i++) {
                if (p.tagName === 'LABEL') return (p.textContent || '').trim();
                p = p.parentNode;
            }
            // Strategy 4: previous sibling text (common for select/radio groups)
            p = el.parentNode;
            for (let i = 0; i < 4 && p; i++) {
                // Look for a label-like sibling at this level
                for (const sib of (p.children || [])) {
                    if (sib === el) break;
                    if (/^(LABEL|LEGEND|SPAN|DIV|H[1-6]|P)$/.test(sib.tagName)) {
                        const t = (sib.textContent || '').trim();
                        if (t && t.length < 200 && /\\?|:|\\*/.test(t)) return t;
                    }
                }
                p = p.parentNode;
            }
            return el.placeholder || el.name || el.getAttribute('aria-label') || '';
        }
        function isRequired(el) {
            if (el.required) return true;
            if (el.getAttribute('aria-required') === 'true') return true;
            const lbl = findLabel(el);
            return /\\*\\s*$/.test(lbl) || /\\*\\s*\\(/.test(lbl);
        }
        function isInDialog(el) {
            let p = el;
            while (p) {
                if (p.getAttribute && p.getAttribute('role') === 'dialog') return true;
                p = p.parentNode || p.host;
            }
            return false;
        }
        function radioGroup(el) {
            // For radios, gather sibling radios with the same name.
            const root = el.getRootNode ? el.getRootNode() : document;
            const name = el.name;
            if (!name) return [el];
            const all = [];
            for (const r of (root.querySelectorAll ? root.querySelectorAll('input[type="radio"]') : [])) {
                if (r.name === name) all.push(r);
            }
            return all.length ? all : [el];
        }

        function checkboxGroup(el) {
            // Find sibling checkboxes that share a near common ancestor (within 5 levels).
            // LinkedIn renders Yes/No questions as 2+ checkboxes under one fieldset/div.
            const root = el.getRootNode ? el.getRootNode() : document;
            let p = el.parentElement;
            for (let i = 0; i < 5 && p; i++) {
                const sibs = p.querySelectorAll ? p.querySelectorAll('input[type="checkbox"]') : [];
                if (sibs.length >= 2) return Array.from(sibs);
                p = p.parentElement;
            }
            return [el];
        }

        const out = [];
        const seenRadioNames = new Set();
        const seenCheckboxes = new Set();
        for (const el of deepNodes(document)) {
            if (!isInDialog(el)) continue;
            const tag = el.tagName;

            if (tag === 'INPUT') {
                const t = (el.type || 'text').toLowerCase();
                if (['hidden', 'submit', 'button', 'file'].includes(t)) continue;
                if (t === 'checkbox') {
                    if (seenCheckboxes.has(el)) continue;
                    const grp = checkboxGroup(el);
                    if (grp.length >= 2) {
                        // single-choice checkbox group → treat like radio
                        for (const cb of grp) seenCheckboxes.add(cb);
                        if (grp.some(cb => cb.checked)) continue;
                        if (!grp.some(cb => isRequired(cb))) continue;
                        const options = grp.map(cb => findLabel(cb) || cb.value || '');
                        // Group label: walk up from first cb to find the question text
                        let groupLabel = '';
                        let p2 = el.parentElement;
                        for (let i = 0; i < 6 && p2 && !groupLabel; i++) {
                            for (const child of (p2.children || [])) {
                                const txt = (child.textContent || '').trim();
                                if (txt && txt.length < 300 && /\\?|:|\\*/.test(txt)
                                    && !options.some(o => txt.includes(o))) {
                                    groupLabel = txt;
                                    break;
                                }
                            }
                            p2 = p2.parentElement;
                        }
                        out.push({
                            label: groupLabel || 'checkbox group',
                            type: 'radio', options,
                            name: el.name || '', placeholder: '', required: true,
                            _selector: cssPath(el),
                            _group_selectors: grp.map(cb => cssPath(cb)),
                        });
                        continue;
                    }
                    seenCheckboxes.add(el);
                    if (el.checked) continue;
                    if (!isRequired(el)) continue;
                    out.push({
                        label: findLabel(el), type: 'checkbox', options: [],
                        name: el.name || '', placeholder: '', required: true,
                        _selector: cssPath(el),
                    });
                    continue;
                }
                if (t === 'radio') {
                    if (seenRadioNames.has(el.name)) continue;
                    const grp = radioGroup(el);
                    if (grp.some(r => r.checked)) { seenRadioNames.add(el.name); continue; }
                    if (!grp.some(r => isRequired(r))) continue;
                    seenRadioNames.add(el.name);
                    const options = grp.map(r => {
                        const lbl = findLabel(r);
                        return lbl || r.value || '';
                    });
                    out.push({
                        label: findLabel(grp[0]).replace(/\\s+\\S+\\s*$/, '').trim() || el.name,
                        type: 'radio', options,
                        name: el.name || '', placeholder: '', required: true,
                        _selector: cssPath(el),
                    });
                    continue;
                }
                // text/number/tel/email
                if (el.value && el.value.trim()) continue;
                if (!isRequired(el)) continue;
                out.push({
                    label: findLabel(el), type: t || 'text', options: [],
                    name: el.name || el.id || '', placeholder: el.placeholder || '',
                    required: true, _selector: cssPath(el),
                });
            } else if (tag === 'TEXTAREA') {
                if (el.value && el.value.trim()) continue;
                if (!isRequired(el)) continue;
                out.push({
                    label: findLabel(el), type: 'textarea', options: [],
                    name: el.name || el.id || '', placeholder: el.placeholder || '',
                    required: true, _selector: cssPath(el),
                });
            } else if (tag === 'SELECT') {
                const cur = (el.value || '').trim();
                const opts = Array.from(el.options || []).map(o => o.text.trim()).filter(Boolean);
                // 'Select an option' placeholder is unfilled
                if (cur && !/^select|^choose|^please/i.test(cur)) continue;
                if (!isRequired(el) && opts.length === 0) continue;
                out.push({
                    label: findLabel(el), type: 'select',
                    options: opts.filter(o => !/^select|^choose|^please/i.test(o)),
                    name: el.name || el.id || '', placeholder: '', required: isRequired(el),
                    _selector: cssPath(el),
                });
            }
        }
        return out;
    """
    try:
        return driver.execute_script(js) or []
    except Exception as e:
        logger.warning("extract_unfilled_questions failed: {}", e)
        return []


def fill_answers(driver, qa_pairs: list[tuple[dict, str]]) -> int:
    """Fill each (question, answer) pair into the modal via JS. Returns count filled."""
    if not qa_pairs:
        return 0
    js = JS_WALK_PROLOG + """
        const pairs = arguments[0];
        function findBySelector(sel) {
            // Try shadow-aware lookup: walk every shadow root with querySelector
            for (const root of [document, ...Array.from(deepNodes(document))
                    .filter(n => n.shadowRoot).map(n => n.shadowRoot)]) {
                try {
                    const el = root.querySelector(sel);
                    if (el) return el;
                } catch (e) {}
            }
            return null;
        }
        function setValue(el, val) {
            const proto = Object.getPrototypeOf(el);
            const desc = Object.getOwnPropertyDescriptor(proto, 'value');
            if (desc && desc.set) desc.set.call(el, val);
            else el.value = val;
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
        }
        let filled = 0;
        for (const {question, answer} of pairs) {
            if (!answer) continue;
            const el = findBySelector(question._selector);
            if (!el) continue;
            const tag = el.tagName;
            try {
                if (tag === 'SELECT') {
                    let matched = false;
                    for (const o of el.options) {
                        if (o.text.trim() === answer || o.value === answer) {
                            el.value = o.value;
                            matched = true; break;
                        }
                    }
                    if (matched) {
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        filled++;
                    }
                } else if (tag === 'TEXTAREA' || (tag === 'INPUT' && !['checkbox', 'radio'].includes((el.type || '').toLowerCase()))) {
                    el.focus();
                    setValue(el, answer);
                    filled++;
                } else if (tag === 'INPUT' && el.type === 'checkbox') {
                    // Group case: question.options has multiple labels, answer = one of them
                    if (question.options && question.options.length > 1 && question._group_selectors) {
                        const root = el.getRootNode ? el.getRootNode() : document;
                        let matched = false;
                        for (const cbSel of question._group_selectors) {
                            const cb = (function() {
                                for (const r of [document, ...Array.from(deepNodes(document)).filter(n => n.shadowRoot).map(n => n.shadowRoot)]) {
                                    try { const x = r.querySelector(cbSel); if (x) return x; } catch (e) {}
                                }
                                return null;
                            })();
                            if (!cb) continue;
                            let lblText = '';
                            const lbl = (cb.id && root.querySelector) ? root.querySelector('label[for="' + CSS.escape(cb.id) + '"]') : null;
                            if (lbl) lblText = (lbl.textContent || '').trim();
                            if (lblText === answer || cb.value === answer) {
                                if (!cb.checked) cb.click();
                                filled++;
                                matched = true;
                                break;
                            }
                        }
                        if (!matched) console.warn('[JHP] checkbox group answer not matched:', answer, question.options);
                    } else {
                        // Single checkbox: truthy answer = check it
                        if (answer && answer.toLowerCase() !== 'false' && answer !== '0' && answer.toLowerCase() !== 'no') {
                            if (!el.checked) el.click();
                            filled++;
                        }
                    }
                } else if (tag === 'INPUT' && el.type === 'radio') {
                    // Find the radio in the same group whose label matches answer
                    const root = el.getRootNode ? el.getRootNode() : document;
                    const group = root.querySelectorAll ? root.querySelectorAll('input[type="radio"][name="' + CSS.escape(el.name) + '"]') : [el];
                    for (const r of group) {
                        let lblText = '';
                        const lbl = (r.id && root.querySelector) ? root.querySelector('label[for="' + CSS.escape(r.id) + '"]') : null;
                        if (lbl) lblText = (lbl.textContent || '').trim();
                        if (lblText === answer || r.value === answer) {
                            r.click();
                            filled++;
                            break;
                        }
                    }
                }
            } catch (e) { console.warn('[JHP] fill failed', e); }
        }
        return filled;
    """
    try:
        return driver.execute_script(js, [
            {"question": q, "answer": a} for q, a in qa_pairs
        ]) or 0
    except Exception as e:
        logger.warning("fill_answers failed: {}", e)
        return 0


def has_modal_errors(driver) -> bool:
    """Check for visible inline error messages inside modal (red required-field warnings)."""
    js = JS_WALK_PROLOG + """
        for (const e of deepNodes(document)) {
            if (!isVisible(e)) continue;
            const role = e.getAttribute && e.getAttribute('role');
            const cls = (e.className || '').toString();
            if (role === 'alert' || cls.includes('error') || cls.includes('feedback--error')) {
                const t = (e.textContent || '').trim();
                if (t.length > 0 && t.length < 200) return true;
            }
        }
        return false;
    """
    try:
        return bool(driver.execute_script(js))
    except Exception:
        return False


# --- Easy Apply flow ---

def apply_to_job(driver, job_url: str, profile_phone: str = "") -> ApplyResult:
    logger.info("Opening: {}", job_url)
    job_id = re.search(r"/jobs/view/(\d+)", job_url)
    job_tag = job_id.group(1) if job_id else "nojid"
    try:
        driver.get(job_url)
    except WebDriverException as e:
        return ApplyResult(ApplyOutcome.FAILED, f"goto failed: {e}")
    human_sleep(3, 5)

    if is_blocked_page(driver):
        _diag_save(driver, f"{job_tag}_blocked")
        return ApplyResult(ApplyOutcome.BLOCKED, "warning page detected")

    # Verify we landed on the job page (not authwall)
    url_now = page_url(driver)
    if "linkedin.com/jobs/" not in url_now:
        _diag_save(driver, f"{job_tag}_redirect")
        return ApplyResult(ApplyOutcome.FAILED, f"redirected to {url_now}")

    # Some pages need a scroll to wire up the apply button
    try:
        driver.execute_script("window.scrollBy(0, 400);")
        time.sleep(0.5)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.3)
    except Exception:
        pass

    # Already applied? Check for "Applied" / "Submitted resume" indicator
    already_text = driver.execute_script(
        "return (document.body.innerText || '').substring(0, 4000);"
    ).lower()
    if "applied" in already_text and not find_button_by_text(driver, r"^easy apply\b", timeout=1):
        return ApplyResult(ApplyOutcome.ALREADY_APPLIED)

    # Try Easy Apply first (full apply with modal)
    ea_btn = find_button_by_text(driver, r"easy apply\b", timeout=6)
    if ea_btn:
        human_sleep(0.8, 1.8)
        if not robust_click(driver, ea_btn, "easy_apply"):
            _diag_save(driver, f"{job_tag}_ea_click_failed")
            return ApplyResult(ApplyOutcome.FAILED, "all click strategies failed on Easy Apply")
        logger.info("clicked Easy Apply, waiting for modal")
        if not wait_for_modal(driver, timeout=6):
            _diag_save(driver, f"{job_tag}_no_modal")
            # Maybe the click opened an external apply tab — re-check buttons
            return ApplyResult(ApplyOutcome.FAILED, "Easy Apply clicked but modal never appeared")
        logger.info("modal appeared")
        return _walk_modal(driver, profile_phone, job_tag)

    # Fallback: "I'm interested" — LinkedIn's soft-signal alternative
    interested = find_button_by_text(driver, r"i.?m interested", timeout=2)
    if interested:
        human_sleep(0.8, 1.8)
        if not robust_click(driver, interested, "interested"):
            _diag_save(driver, f"{job_tag}_interested_click_failed")
            return ApplyResult(ApplyOutcome.FAILED, "I'm interested click failed")
        logger.info("clicked I'm interested (soft-signal)")
        human_sleep(1.5, 3)
        # Some flows show a confirmation modal with "Submit" or auto-close
        confirm = find_button_by_text(driver, r"^(?:submit|confirm|done|got it)$", timeout=2)
        if confirm:
            robust_click(driver, confirm, "interested_confirm")
            human_sleep(1, 2)
        return ApplyResult(ApplyOutcome.INTEREST_SIGNALED, detail="clicked I'm interested")

    # Neither button → really no apply path
    dump = dump_buttons(driver)
    logger.info("No Apply or I'm interested. Buttons: {}", dump)
    _diag_save(driver, f"{job_tag}_no_apply")
    return ApplyResult(ApplyOutcome.NO_EASY_APPLY, detail=f"buttons={dump}")


async def _autofill_via_llm(driver, page_idx: int, job_tag: str) -> int:
    """Extract unfilled required fields, ask Claude for answers, fill them.
    Returns count of successfully filled fields."""
    from app.modules.applies.services.answer_questions import (
        FormQuestion,
        answer_questions,
    )

    raw_qs = extract_unfilled_questions(driver)
    if not raw_qs:
        return 0
    logger.info("[p{}] {} unfilled required field(s) to autofill via LLM",
                page_idx + 1, len(raw_qs))
    questions = [
        FormQuestion(
            label=q["label"], type=q["type"], options=q.get("options") or [],
            name=q.get("name", ""), placeholder=q.get("placeholder", ""),
            required=q.get("required", True),
        )
        for q in raw_qs
    ]
    answers = await answer_questions(questions)
    if not answers:
        return 0
    qa_pairs = [(raw_qs[i], answers[i].answer) for i in range(len(answers))]
    n = fill_answers(driver, qa_pairs)
    logger.info("[p{}] LLM filled {}/{} fields", page_idx + 1, n, len(answers))
    if n:
        _diag_save(driver, f"{job_tag}_after_autofill_p{page_idx}")
    return n


def _walk_modal(driver, profile_phone: str, job_tag: str = "nojid") -> ApplyResult:
    import asyncio
    for page_idx in range(MAX_MODAL_PAGES):
        human_sleep(0.6, 1.4)
        if is_blocked_page(driver):
            _diag_save(driver, f"{job_tag}_modal_blocked_p{page_idx}")
            return ApplyResult(ApplyOutcome.BLOCKED, f"page {page_idx}")

        # Phone fill
        if profile_phone:
            inp = find_input_by_label(driver, r"phone|mobile|tel")
            if inp:
                current = driver.execute_script("return arguments[0].value;", inp)
                if not current.strip():
                    driver.execute_script(
                        "arguments[0].focus(); arguments[0].value = arguments[1]; "
                        "arguments[0].dispatchEvent(new Event('input', {bubbles:true})); "
                        "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                        inp, profile_phone,
                    )
                    human_sleep(0.3, 0.7)

        # Submit?
        submit = find_button_by_text(driver, r"^submit application$", timeout=1)
        if submit:
            logger.info("Submit at page {}", page_idx + 1)
            _uncheck_follow(driver)
            human_sleep(0.7, 1.4)
            if not robust_click(driver, submit, "submit"):
                _diag_save(driver, f"{job_tag}_submit_click_failed")
                return ApplyResult(ApplyOutcome.FAILED, "submit click failed", pages=page_idx + 1)
            human_sleep(2.5, 4)
            return ApplyResult(ApplyOutcome.APPLIED, pages=page_idx + 1)

        # Review?
        review = find_button_by_text(driver, r"^review( your application)?$", timeout=1)
        if review:
            logger.debug("Review at page {}", page_idx + 1)
            robust_click(driver, review, "review")
            human_sleep(1, 2)
            continue

        # Continue / Next?
        cont = find_button_by_text(driver, r"^(?:continue( to next step)?|next)$", timeout=1)
        if cont:
            # Try optimistic Next first — if no errors, page was already filled
            if not has_modal_errors(driver):
                # Also proactively scan for unfilled required fields BEFORE first Next
                # (LinkedIn doesn't always paint red errors until after a click)
                pre_qs = extract_unfilled_questions(driver)
                if pre_qs:
                    logger.info(
                        "[p{}] {} unfilled required field(s) detected pre-click — autofilling",
                        page_idx + 1, len(pre_qs),
                    )
                    asyncio.run(_autofill_via_llm(driver, page_idx, job_tag))
                    human_sleep(0.5, 1.0)
                logger.info("Continue/Next at page {}", page_idx + 1)
                robust_click(driver, cont, "continue")
                human_sleep(1, 2)
                continue
            # Errors visible → autofill and retry once
            logger.info("Required fields at page {} — invoking LLM autofill", page_idx + 1)
            _diag_save(driver, f"{job_tag}_required_fields_p{page_idx}")
            n_filled = asyncio.run(_autofill_via_llm(driver, page_idx, job_tag))
            human_sleep(0.5, 1.0)
            if n_filled and not has_modal_errors(driver):
                logger.info("Autofill cleared errors, clicking Next at page {}", page_idx + 1)
                robust_click(driver, cont, "continue_after_autofill")
                human_sleep(1, 2)
                continue
            logger.warning(
                "Autofill insufficient (filled={}, errors={}) — bailing", n_filled, has_modal_errors(driver),
            )
            _diag_save(driver, f"{job_tag}_autofill_failed_p{page_idx}")
            _close_modal(driver)
            return ApplyResult(
                ApplyOutcome.TOO_MANY_QUESTIONS,
                detail=f"red errors persisted after LLM autofill at page {page_idx + 1}",
                pages=page_idx + 1,
            )

        # Nothing matches → bail with diagnostic
        dump = dump_buttons(driver, 8)
        _diag_save(driver, f"{job_tag}_no_nav_p{page_idx}")
        return ApplyResult(
            ApplyOutcome.FAILED,
            detail=f"no nav at page {page_idx + 1}: {dump}",
            pages=page_idx + 1,
        )

    _close_modal(driver)
    return ApplyResult(
        ApplyOutcome.TOO_MANY_QUESTIONS,
        detail=f">{MAX_MODAL_PAGES} modal pages",
        pages=MAX_MODAL_PAGES,
    )


def _uncheck_follow(driver):
    """Uncheck the 'Follow company' checkbox in the modal (shadow-DOM aware)."""
    js = JS_WALK_PROLOG + """
        for (const cb of deepNodes(document)) {
            if (cb.tagName !== 'INPUT' || cb.type !== 'checkbox') continue;
            const root = cb.getRootNode ? cb.getRootNode() : document;
            const lbl = cb.closest && cb.closest('label')
                || (cb.id && root.querySelector ? root.querySelector('label[for="' + cb.id + '"]') : null);
            const t = lbl ? lbl.textContent.toLowerCase() : '';
            if (/follow.*compan/i.test(t) && cb.checked) {
                (lbl || cb).click();
                return true;
            }
        }
        return false;
    """
    try:
        driver.execute_script(js)
    except Exception:
        pass


def _close_modal(driver):
    close = find_button_by_text(driver, r"^(?:dismiss|close)$", timeout=1)
    if close:
        try:
            close.click()
            human_sleep(0.6, 1.2)
            discard = find_button_by_text(driver, r"^discard$", timeout=1)
            if discard:
                discard.click()
                human_sleep(0.4, 0.8)
        except Exception:
            pass


# --- main test entry ---

if __name__ == "__main__":
    # Simple smoke test: open feed, then a job, report
    with selenium_firefox(headless=True) as driver:
        logger.info("Opening LinkedIn feed for auth check...")
        driver.get("https://www.linkedin.com/feed/")
        human_sleep(3, 5)
        url = page_url(driver)
        title = driver.title
        logger.info("URL: {} | Title: {!r}", url, title)
        if "/feed/" in url and "login" not in url.lower():
            logger.success("✅ Logged in via copied profile")
        else:
            logger.warning("Not logged in: {}", url)

        # Try a known LinkedIn job
        test_url = "https://www.linkedin.com/jobs/view/4408048368/"
        result = apply_to_job(driver, test_url, profile_phone="")
        logger.info("Apply result on test job: {}", result)
