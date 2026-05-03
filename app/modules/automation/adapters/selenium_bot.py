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

MAX_MODAL_PAGES = 12
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
    # Make sure user.js doesn't disable cookies. Plus a pile of anti-bot-
    # detection prefs so Cloudflare Turnstile / hCaptcha don't flag us as
    # webdriver-controlled (they look at navigator.webdriver, missing
    # plugins, marionette markers, language list shape, etc.).
    user_js = PROFILE_COPY_DIR / "user.js"
    user_js.write_text(
        'user_pref("datareporting.healthreport.uploadEnabled", false);\n'
        'user_pref("dom.disable_open_during_load", false);\n'
        # Hide all automation markers from the JS-visible navigator surface
        'user_pref("dom.webdriver.enabled", false);\n'
        'user_pref("useAutomationExtension", false);\n'
        'user_pref("marionette.contentListener", false);\n'
        # Cloudflare reads these — make them look like a normal user profile
        'user_pref("privacy.resistFingerprinting", false);\n'
        'user_pref("privacy.trackingprotection.enabled", false);\n'
        'user_pref("network.http.sendRefererHeader", 2);\n'
        'user_pref("media.peerconnection.enabled", true);\n'
        'user_pref("media.navigator.enabled", true);\n'
        'user_pref("webgl.disabled", false);\n'
        # Skip the first-run / what's-new pages that confuse session checks
        'user_pref("browser.startup.homepage_override.mstone", "ignore");\n'
        'user_pref("browser.startup.page", 0);\n'
        'user_pref("browser.newtabpage.enabled", false);\n'
        'user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);\n'
    )
    return PROFILE_COPY_DIR


# JS injected via execute_script right after every navigate. Cloudflare's
# bot-detector looks at these specific properties; setting them to mimic a
# vanilla Firefox install gets us through Turnstile's invisible challenge.
STEALTH_JS = """
try {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
} catch (e) {}
try {
    Object.defineProperty(navigator, 'plugins', {
        get: () => [{name: 'PDF Viewer'}, {name: 'Chrome PDF Viewer'}, {name: 'Native Client'}],
    });
} catch (e) {}
try {
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
} catch (e) {}
try {
    delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
    delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
    delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
} catch (e) {}
"""


def stealth_navigate(driver, url: str) -> None:
    """driver.get(url) but injects bot-detection countermeasures both before
    AND after the load so the page's JS sees a vanilla browser fingerprint."""
    try:
        driver.execute_script(STEALTH_JS)
    except Exception:
        pass
    driver.get(url)
    try:
        driver.execute_script(STEALTH_JS)
    except Exception:
        pass


def has_cloudflare_challenge(driver) -> bool:
    """Detect Turnstile / Cloudflare 'verify you are human' / hCaptcha
    overlays that block submit. Returns True if any is visibly active.

    Detection signals (any one fires):
      - <input name="cf-turnstile-response"> with empty value (the form has
        a Turnstile field but it hasn't been solved)
      - <input data-testid="input-turnstile-required"> still empty
      - <div id="turnstile-container"> / <div class="cf-turnstile">
      - <iframe src*=turnstile|challenges.cloudflare|hcaptcha|recaptcha>
      - 'verify you are human' / 'checking your browser' text
    """
    js = """
        function* dn(r){const s=[r];while(s.length){const n=s.pop();if(!n)continue;
            if(n.nodeType===1)yield n;if(n.shadowRoot)s.push(n.shadowRoot);
            const k=n.children||n.childNodes||[];for(let i=k.length-1;i>=0;i--)s.push(k[i]);}}
        for (const el of dn(document)) {
            // 1. Hidden Turnstile response input — set when Turnstile is
            //    solved. Empty value = challenge still active.
            if (el.tagName === 'INPUT') {
                const name = (el.name || '').toLowerCase();
                const tid = (el.getAttribute && el.getAttribute('data-testid') || '').toLowerCase();
                if (name === 'cf-turnstile-response' || tid === 'input-turnstile-required') {
                    if (!(el.value || '').trim()) return true;
                }
            }
            // 2. Iframe-based widgets (loaded by Turnstile JS into container)
            if (el.tagName === 'IFRAME') {
                const src = (el.src || '').toLowerCase();
                if (/turnstile|challenges\\.cloudflare|hcaptcha\\.com|recaptcha/i.test(src)) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 1 && r.height > 1) return true;
                }
            }
            // 3. Turnstile / hCaptcha container by id or class
            const id = (el.id || '').toLowerCase();
            if (id === 'turnstile-container' || id.startsWith('cf-chl-widget')) {
                const r = el.getBoundingClientRect();
                if (r.width > 1 && r.height > 1) return true;
            }
            const cls = (el.className && el.className.baseVal !== undefined) ?
                el.className.baseVal : (typeof el.className === 'string' ? el.className : '');
            if (cls && /\\bcf-turnstile\\b|\\bh-captcha\\b|\\bg-recaptcha\\b/i.test(cls)) {
                const r = el.getBoundingClientRect();
                if (r.width > 1 && r.height > 1) return true;
            }
            // 4. Inline Cloudflare interstitial text
            const txt = (el.textContent || '').slice(0, 200);
            if (/checking your browser|verify (you are|that you are) human|just a moment|please complete the security check|i'?m not a robot/i.test(txt)) {
                const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                if (r && r.width > 50 && r.height > 20) return true;
            }
        }
        return false;
    """
    try:
        return bool(driver.execute_script(js))
    except Exception:
        return False


def solve_cloudflare_checkbox(driver, max_wait_s: int = 30) -> bool:
    """Click the Turnstile checkbox via real OS-level mouse events.

    Strategy:
      1. Wait for the Turnstile iframe to load inside #turnstile-container
         (Turnstile JS injects it asynchronously after page load).
      2. Scroll the iframe into view.
      3. Use ActionChains with offset-from-top-left to click the checkbox
         (~30px in from the left edge of the iframe).
      4. Re-check has_cloudflare_challenge — Turnstile sets the response
         input value when solved, so the detection drops to False.

    Returns True if challenge cleared, False if still present after the
    wait window.
    """
    from selenium.webdriver.common.action_chains import ActionChains
    import random

    deadline = time.monotonic() + max_wait_s
    last_click = 0.0
    while time.monotonic() < deadline:
        if not has_cloudflare_challenge(driver):
            return True

        # Find ANY visible Turnstile/Cloudflare iframe — including ones with
        # blank src (Turnstile sometimes renders into a sandboxed iframe with
        # only a `data-cf-*` attribute on the parent).
        iframe = driver.execute_script(
            """
            // Prefer iframe inside the known Turnstile container.
            const cont = document.querySelector('#turnstile-container');
            if (cont) {
                const ifr = cont.querySelector('iframe');
                if (ifr) {
                    const r = ifr.getBoundingClientRect();
                    if (r.width > 1 && r.height > 1) return ifr;
                }
            }
            // Fallback: any iframe whose src points at a challenge widget.
            for (const ifr of document.querySelectorAll('iframe')) {
                const src = (ifr.src || '').toLowerCase();
                if (/turnstile|challenges\\.cloudflare|hcaptcha/i.test(src)) {
                    const r = ifr.getBoundingClientRect();
                    if (r.width > 1 && r.height > 1) return ifr;
                }
            }
            return null;
            """
        )
        if iframe and (time.monotonic() - last_click) > 3.0:
            try:
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", iframe,
                )
                time.sleep(0.4)
                # Use Selenium's iframe-aware offset click. The checkbox is
                # ~30px from the iframe's left edge, vertically centered (~32
                # for the standard 65px-tall Turnstile widget). Adding a small
                # randomized jitter so the click point isn't perfectly
                # pixel-perfect every time (humanlike).
                offset_x = 30 + int(random.uniform(-3, 3))
                offset_y = 32 + int(random.uniform(-3, 3))
                actions = ActionChains(driver)
                # move_to_element starts at element CENTER; we offset to
                # checkbox position relative to that center.
                size = iframe.size
                cx_offset = -(size.get("width", 300) // 2) + offset_x
                cy_offset = -(size.get("height", 65) // 2) + offset_y
                actions.move_to_element_with_offset(iframe, cx_offset, cy_offset)
                actions.pause(random.uniform(0.15, 0.35))
                actions.click()
                actions.perform()
                last_click = time.monotonic()
                logger.info(
                    "cloudflare: clicked Turnstile checkbox via offset ({},{})",
                    cx_offset, cy_offset,
                )
                time.sleep(3.0)
                continue
            except Exception as e:
                logger.debug("cloudflare click attempt failed: {}", e)
        time.sleep(0.6)
    return not has_cloudflare_challenge(driver)


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

        // Only restrict to a dialog if there IS one on the page (LinkedIn
        // Easy Apply, etc.). External ATS forms (Ashby, Greenhouse, Lever,
        // standalone Workday) render the form on a full page with no
        // role=dialog wrapper — gating on isInDialog() drops every field.
        let dialogPresent = false;
        for (const el of deepNodes(document)) {
            if (el.getAttribute && el.getAttribute('role') === 'dialog') {
                dialogPresent = true; break;
            }
        }

        const out = [];
        const seenRadioNames = new Set();
        const seenCheckboxes = new Set();
        for (const el of deepNodes(document)) {
            if (dialogPresent && !isInDialog(el)) continue;
            const tag = el.tagName;

            if (tag === 'INPUT') {
                const t = (el.type || 'text').toLowerCase();
                if (['hidden', 'submit', 'button', 'file'].includes(t)) continue;
                if (t === 'checkbox') {
                    if (seenCheckboxes.has(el)) continue;
                    // Skip Ashby's hidden state-holder checkboxes that live
                    // inside <div class="..._yesno...">, where the user-facing
                    // controls are actual <button>Yes/No</button> elements.
                    // The yesno extractor below handles those properly.
                    let inYesNo = false;
                    let walker = el.parentElement;
                    for (let i = 0; i < 4 && walker && !inYesNo; i++) {
                        const cls = walker.className || '';
                        if (typeof cls === 'string' && /yesno/i.test(cls)) inYesNo = true;
                        walker = walker.parentElement;
                    }
                    if (inYesNo) { seenCheckboxes.add(el); continue; }
                    const grp = checkboxGroup(el);
                    if (grp.length >= 2) {
                        // single-choice checkbox group → treat like radio.
                        for (const cb of grp) seenCheckboxes.add(cb);
                        if (grp.some(cb => cb.checked)) continue;
                        const groupRequired = grp.some(cb => isRequired(cb)) || true;
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
                    out.push({
                        label: findLabel(el), type: 'checkbox', options: [],
                        name: el.name || '', placeholder: '', required: isRequired(el),
                        _selector: cssPath(el),
                    });
                    continue;
                }
                if (t === 'radio') {
                    if (seenRadioNames.has(el.name)) continue;
                    const grp = radioGroup(el);
                    if (grp.some(r => r.checked)) { seenRadioNames.add(el.name); continue; }
                    seenRadioNames.add(el.name);
                    const options = grp.map(r => {
                        const lbl = findLabel(r);
                        return lbl || r.value || '';
                    });
                    // Radio question label resolution — order from strongest
                    // signal to weakest:
                    //   1) Wrapper [aria-labelledby] / <fieldset><legend>.
                    //   2) A nearby <p>/<label>/<legend>/<h*> with '?' or '*'
                    //      that doesn't itself match the per-radio options
                    //      (e.g. Rippling renders the question in a sibling
                    //      <p>, OUTSIDE the radiogroup div).
                    //   3) Strip the per-radio label's trailing word.
                    let groupLabel = '';
                    const root = el.getRootNode ? el.getRootNode() : document;
                    let p = el.parentElement;
                    for (let i = 0; i < 6 && p && !groupLabel; i++) {
                        const lbId = p.getAttribute && p.getAttribute('aria-labelledby');
                        if (lbId && root.getElementById) {
                            const n = root.getElementById(lbId);
                            if (n) groupLabel = (n.textContent || '').trim();
                        }
                        if (!groupLabel && p.tagName === 'FIELDSET') {
                            const lg = p.querySelector && p.querySelector('legend');
                            if (lg) groupLabel = (lg.textContent || '').trim();
                        }
                        p = p.parentElement;
                    }
                    if (!groupLabel) {
                        // Walk up looking for a question-text node nearby —
                        // try each ancestor's children (not just descendants).
                        let q = el.parentElement;
                        for (let i = 0; i < 8 && q && !groupLabel; i++) {
                            for (const sib of (q.children || [])) {
                                if (sib === el) continue;
                                if (!/^(P|LABEL|LEGEND|SPAN|DIV|H[1-6])$/.test(sib.tagName)) continue;
                                const txt = (sib.textContent || '').trim();
                                if (!txt || txt.length > 400) continue;
                                if (!/[?:*]/.test(txt)) continue;
                                // Skip if it's just the option labels stitched together.
                                if (options.some(o => o && txt === o)) continue;
                                groupLabel = txt;
                                break;
                            }
                            q = q.parentElement;
                        }
                    }
                    out.push({
                        label: groupLabel
                            || findLabel(grp[0]).replace(/\\s+\\S+\\s*$/, '').trim()
                            || el.name,
                        type: 'radio', options,
                        name: el.name || '', placeholder: '', required: grp.some(r => isRequired(r)),
                        _selector: cssPath(el),
                    });
                    continue;
                }
                // text/number/tel/email — surface optional fields too; the LLM
                // decides whether to fill via confidence threshold.
                if (el.value && el.value.trim()) continue;
                // ARIA combobox / search-as-you-type widgets (react-select,
                // country code pickers, location autocomplete). If REQUIRED,
                // route through the combobox-with-options extractor below
                // (LLM gets real choices and picks one). If OPTIONAL, skip —
                // they tend to pollute the question pool with auto-pickers
                // we don't actually need to set.
                const isCombo = el.getAttribute('role') === 'combobox' ||
                    el.getAttribute('aria-autocomplete') === 'list' ||
                    el.getAttribute('aria-haspopup') === 'listbox';
                if (isCombo) {
                    if (!isRequired(el)) continue;
                    // Find a question label (placeholder/aria-label often
                    // says 'Search' — useless; prefer findLabel which walks
                    // for sibling text).
                    const lbl = findLabel(el) || el.getAttribute('aria-label') || el.placeholder || '';
                    out.push({
                        label: lbl, type: 'select', options: [],
                        name: el.name || el.id || '', placeholder: el.placeholder || '',
                        required: true, _selector: cssPath(el),
                        _div_combobox: true,  // same fill path as div-combobox
                    });
                    continue;
                }
                out.push({
                    label: findLabel(el), type: t || 'text', options: [],
                    name: el.name || el.id || '', placeholder: el.placeholder || '',
                    required: isRequired(el), _selector: cssPath(el),
                });
            } else if (tag === 'TEXTAREA') {
                if (el.value && el.value.trim()) continue;
                // Skip captcha response textareas — they are populated by the
                // captcha widget itself, not by the user.
                const nm = (el.name || '') + ' ' + (el.id || '');
                if (/recaptcha|hcaptcha|h-captcha|g-recaptcha|cf-turnstile/i.test(nm)) continue;
                // Skip non-displayed textareas (captchas often set display:none).
                if (el.offsetParent === null && el.style.display === 'none') continue;
                out.push({
                    label: findLabel(el), type: 'textarea', options: [],
                    name: el.name || el.id || '', placeholder: el.placeholder || '',
                    required: isRequired(el), _selector: cssPath(el),
                });
            } else if (tag === 'SELECT') {
                const cur = (el.value || '').trim();
                const opts = Array.from(el.options || []).map(o => o.text.trim()).filter(Boolean);
                if (cur && !/^select|^choose|^please/i.test(cur)) continue;
                if (opts.length === 0) continue;
                out.push({
                    label: findLabel(el), type: 'select',
                    options: opts.filter(o => !/^select|^choose|^please/i.test(o)),
                    name: el.name || el.id || '', placeholder: '', required: isRequired(el),
                    _selector: cssPath(el),
                });
            }
        }

        // --- Pass 1.5: div-based comboboxes (Rippling, Workday) ---
        // Custom dropdowns rendered as
        //   <div role="combobox" aria-haspopup="listbox" aria-required="true">
        //     <p>Select</p>
        //   </div>
        // are not <select> elements so the SELECT branch above misses them.
        // Detect required ones with a clear ancestor question label, and
        // emit them as type='select' with an OPEN marker — fill_answers
        // opens the dropdown, collects options, and clicks the matching one.
        const seenCombo = new Set();
        for (const el of deepNodes(document)) {
            if (dialogPresent && !isInDialog(el)) continue;
            if (el.tagName !== 'DIV') continue;
            const role = el.getAttribute && el.getAttribute('role');
            if (role !== 'combobox') continue;
            const haspopup = el.getAttribute('aria-haspopup');
            if (haspopup !== 'listbox' && haspopup !== 'menu') continue;
            const required = el.getAttribute('aria-required') === 'true';
            // We only want fields the user MUST fill — country code pickers
            // typically don't have aria-required; sponsorship/visa dropdowns
            // do.
            if (!required) continue;
            if (seenCombo.has(el)) continue;
            seenCombo.add(el);
            // Skip if already chosen (label text ≠ 'select'/'choose'/empty).
            const innerTxt = (el.textContent || '').trim();
            if (innerTxt && !/^(select|choose|please)/i.test(innerTxt)) continue;
            // Walk up looking for the question text in a descendant <p> /
            // <label> / <h*> of an ancestor (Rippling nests the question in
            // a sibling DIV at the field-section level, not as a direct
            // sibling of the combobox).
            let qLabel = '';
            let p = el.parentElement;
            for (let i = 0; i < 6 && p && !qLabel; i++) {
                const cands = p.querySelectorAll
                    ? p.querySelectorAll(':scope p, :scope label, :scope legend, :scope h1, :scope h2, :scope h3, :scope h4, :scope h5, :scope h6')
                    : [];
                for (const cand of cands) {
                    if (cand === el || el.contains(cand)) continue;
                    const txt = (cand.textContent || '').trim();
                    if (!txt || txt.length > 400) continue;
                    if (!/[?:*]/.test(txt) && !/required/i.test(txt)) continue;
                    if (/^(select|choose|please)/i.test(txt)) continue;
                    qLabel = txt;
                    break;
                }
                p = p.parentElement;
            }
            out.push({
                label: qLabel || 'select',
                type: 'select',
                options: [],   // populated at fill time after opening dropdown
                name: el.id || '', placeholder: '', required: true,
                _selector: cssPath(el),
                _div_combobox: true,
            });
        }

        // --- Second pass: button-style Yes/No toggle groups (Ashby) ---
        // <div class="..._yesno..."><button>Yes</button><button>No</button>
        //   <input type="checkbox" hidden></div>
        // Re-finding by cssPath is fragile (Ashby reflows after each click)
        // and label-text matching can mismatch when labels share words.
        // Store the document-order INDEX of the yesno container — fill_answers
        // re-walks containers fresh and clicks the Nth one.
        const seenYesNo = new Set();
        let yesnoIdx = 0;
        for (const el of deepNodes(document)) {
            if (dialogPresent && !isInDialog(el)) continue;
            if (el.tagName !== 'DIV') continue;
            const cls = el.className || '';
            if (typeof cls !== 'string' || !/yesno/i.test(cls)) continue;
            if (seenYesNo.has(el)) continue;
            seenYesNo.add(el);
            const btns = Array.from(el.querySelectorAll('button')).filter(b => {
                const t = (b.textContent || '').trim();
                return /^(yes|no)$/i.test(t);
            });
            if (btns.length < 2) continue;
            const already = btns.some(b => /selected|active|checked|_chosen/i.test(b.className || ''));
            const myIdx = yesnoIdx++;
            if (already) continue;
            // Find the question label — only inside the immediate field-entry
            // wrapper (don't walk past it to avoid grabbing a sibling field's
            // label). Fall back to '' if no wrapper found.
            let groupLabel = '';
            let p = el.parentElement, fieldEntry = null;
            for (let i = 0; i < 4 && p && !fieldEntry; i++) {
                const c = p.className || '';
                if (typeof c === 'string' && /fieldEntry|field-entry/i.test(c)) fieldEntry = p;
                p = p.parentElement;
            }
            if (fieldEntry) {
                const lbl = fieldEntry.querySelector('label');
                if (lbl) groupLabel = (lbl.textContent || '').trim();
            }
            const options = btns.map(b => (b.textContent || '').trim());
            out.push({
                label: groupLabel || 'yes/no question',
                type: 'radio',
                options,
                name: '', placeholder: '', required: true,
                _selector: cssPath(btns[0]),
                _button_group: true,
                _yesno_idx: myIdx,
            });
        }
        return out;
    """
    try:
        raw = driver.execute_script(js) or []
    except Exception as e:
        logger.warning("extract_unfilled_questions failed: {}", e)
        return []

    # Post-process: div-based comboboxes need their options harvested
    # from the listbox that appears only when the dropdown is opened.
    # Click each, scrape role=option texts, then close.
    for q in raw:
        if not q.get("_div_combobox"):
            continue
        if q.get("options"):
            continue
        sel = q.get("_selector") or ""
        try:
            opts = driver.execute_script(
                """
                function* dn(r){const s=[r];while(s.length){const n=s.pop();if(!n)continue;
                    if(n.nodeType===1)yield n;if(n.shadowRoot)s.push(n.shadowRoot);
                    const k=n.children||n.childNodes||[];for(let i=k.length-1;i>=0;i--)s.push(k[i]);}}
                const sel = arguments[0];
                let combo = null;
                for (const r of [document, ...Array.from(dn(document))
                        .filter(n => n.shadowRoot).map(n => n.shadowRoot)]) {
                    try { const x = r.querySelector(sel); if (x) { combo = x; break; } } catch(e) {}
                }
                if (!combo) return [];
                combo.click();
                return new Promise((resolve) => {
                    setTimeout(() => {
                        const out = [];
                        for (const el of dn(document)) {
                            if (el.getAttribute && el.getAttribute('role') === 'option') {
                                const r = el.getBoundingClientRect();
                                if (r.width < 1 || r.height < 1) continue;
                                const t = (el.textContent || '').trim();
                                if (t) out.push(t);
                            }
                        }
                        // Close the dropdown by clicking the combobox again
                        // (or pressing Escape — Rippling handles both).
                        try { combo.click(); } catch(e) {}
                        resolve(out);
                    }, 600);
                });
                """,
                sel,
            )
            if opts:
                q["options"] = opts
                logger.info("div-combobox '{}' opts: {}", q["label"][:60], opts[:6])
        except Exception as e:
            logger.debug("combobox option harvest failed for {}: {}", sel[:50], e)

    return raw


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
            // React's onChange listener subscribes via the canonical prototype
            // setter — bypassing it (instance.value=...) doesn't trigger
            // re-render. Look up the setter on HTMLInputElement.prototype /
            // HTMLTextAreaElement.prototype directly, not the element's own
            // prototype (which React may have monkey-patched).
            const ctor = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement
                       : el.tagName === 'SELECT' ? window.HTMLSelectElement
                       : window.HTMLInputElement;
            const desc = ctor && Object.getOwnPropertyDescriptor(ctor.prototype, 'value');
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
                    // Some React forms (Ashby) reset the controlled state on
                    // setValue alone — drive a real text-insertion event so
                    // the framework sees keyboard-like input.
                    if ((el.value || '') !== answer) {
                        try {
                            el.value = '';
                            el.focus();
                            document.execCommand('insertText', false, answer);
                        } catch (e) {}
                    }
                    if ((el.value || '') !== answer) {
                        // Last resort: re-apply setValue + bubble events.
                        setValue(el, answer);
                    }
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
                } else if (tag === 'BUTTON' && question._button_group) {
                    // Ashby-style yes/no buttons ignore JS-dispatched clicks
                    // (isTrusted=false). Skip here — the Python-side fallback
                    // below clicks them via real Selenium events with a
                    // settle pause between each so Ashby's reflow doesn't
                    // strand stale element references.
                } else if (tag === 'DIV' && question._div_combobox) {
                    // Div-based combobox (Rippling sponsorship dropdown):
                    // click to open, find option whose visible text equals
                    // the LLM answer, click it. Skip here too — Python-side
                    // fallback handles it via Selenium so React sees real
                    // pointer events.
                }
            } catch (e) { console.warn('[JHP] fill failed', e); }
        }
        return filled;
    """
    try:
        n = driver.execute_script(js, [
            {"question": q, "answer": a} for q, a in qa_pairs
        ]) or 0
    except Exception as e:
        logger.warning("fill_answers failed: {}", e)
        return 0

    # Python-side fallback — some React forms (Ashby) reject pure JS for
    # both textareas (setValue/execCommand) and button-style toggles
    # (.click() with isTrusted=false). Re-do these via real Selenium calls
    # so the framework sees genuine keyboard/pointer events.
    from selenium.webdriver.common.by import By

    # 1) Textareas: send_keys.
    for q, a in qa_pairs:
        if not a or q.get("type") != "textarea":
            continue
        sel = q.get("_selector") or ""
        if not sel:
            continue
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            cur = (el.get_attribute("value") or "").strip()
            if cur == a.strip():
                continue  # JS path worked
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            el.click()
            try:
                el.clear()
            except Exception:
                pass
            el.send_keys(a)
        except Exception as e:
            logger.debug("textarea send_keys fallback failed for {}: {}", sel[:50], e)

    # 2) Button-style yes/no toggles: re-walk via JS to find the right
    #    container by index, then click via Selenium so the button receives
    #    a trusted event (Ashby state listeners ignore JS-dispatched clicks).
    # 3) Div / input-based comboboxes: open dropdown, find option matching
    #    the LLM answer; if not visible and the combobox is searchable,
    #    type the answer to filter, then look again. Universal across
    #    react-select, Headless UI, Rippling's etc2niq pickers, etc.
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.common.keys import Keys

    def _find_option(answer: str):
        return driver.execute_script(
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
            answer,
        )

    combo_qs = [(q, a) for q, a in qa_pairs if a and q.get("_div_combobox")]
    for q, a in combo_qs:
        sel = q.get("_selector") or ""
        try:
            combo = driver.find_element(By.CSS_SELECTOR, sel)
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", combo)
            # Real mouse-click — synthesized .click() doesn't open all React
            # comboboxes (e.g. Rippling listens for true pointer events).
            try:
                ActionChains(driver).move_to_element(combo).pause(0.1).click().perform()
            except Exception:
                combo.click()
            time.sleep(0.5)
            target = _find_option(a)
            if not target:
                # Try typing into the combobox to filter (only useful for
                # search-as-you-type widgets — large country lists, location
                # autocompletes, etc.). Find the inner search input if combo
                # itself isn't typeable.
                try:
                    typer = combo
                    if combo.tag_name.lower() != "input":
                        typer = combo.find_element(By.CSS_SELECTOR,
                            "input[role='combobox'], input[data-input*='search']")
                except Exception:
                    typer = combo
                try:
                    typer.click()
                    typer.send_keys(Keys.CONTROL, "a")
                    typer.send_keys(Keys.DELETE)
                    typer.send_keys(a)
                    time.sleep(0.8)
                except Exception:
                    try:
                        ActionChains(driver).send_keys(a).perform()
                        time.sleep(0.8)
                    except Exception:
                        pass
                target = _find_option(a)
            if target:
                target.click()
                time.sleep(0.4)
            else:
                logger.debug("combobox option '{}' not found in dropdown", a)
                try:
                    combo.send_keys(Keys.ESCAPE)
                except Exception:
                    pass
        except Exception as e:
            logger.debug("div-combobox fill failed for {}: {}", sel[:50], e)

    yesno_qs = [(q, a) for q, a in qa_pairs if a and q.get("_button_group")]
    if yesno_qs:
        # Sort by index — click in document order so re-renders don't confuse
        # subsequent index lookups.
        yesno_qs.sort(key=lambda p: p[0].get("_yesno_idx", 0))
        for q, a in yesno_qs:
            idx = q.get("_yesno_idx", -1)
            want = (a or "").strip().lower()
            want = "Yes" if want.startswith("y") else ("No" if want.startswith("n") else a)
            clicked = False
            for attempt in range(2):  # one retry on stale-element after reflow
                try:
                    btn = driver.execute_script(
                        """
                        function* dn(r){const s=[r];while(s.length){const n=s.pop();if(!n)continue;
                            if(n.nodeType===1)yield n;if(n.shadowRoot)s.push(n.shadowRoot);
                            const k=n.children||n.childNodes||[];for(let i=k.length-1;i>=0;i--)s.push(k[i]);}}
                        const targetIdx = arguments[0], wantText = arguments[1];
                        let i = 0;
                        for (const d of dn(document)) {
                            if (d.tagName !== 'DIV') continue;
                            const c = d.className || '';
                            if (typeof c !== 'string' || !/yesno/i.test(c)) continue;
                            if (i === targetIdx) {
                                for (const b of d.querySelectorAll('button')) {
                                    if ((b.textContent||'').trim().toLowerCase() === wantText.toLowerCase())
                                        return b;
                                }
                                return null;
                            }
                            i++;
                        }
                        return null;
                        """,
                        idx, want,
                    )
                    if not btn:
                        logger.debug("yesno container/button missing for idx={}", idx)
                        break
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                    btn.click()
                    clicked = True
                    break
                except Exception as e:
                    if attempt == 0:
                        logger.debug("yesno click stale (idx={}), retrying after settle: {}", idx, e)
                        time.sleep(0.4)
                    else:
                        logger.warning("yesno click failed (idx={}): {}", idx, e)
            if clicked:
                # Pause so Ashby finishes the state update + re-render before
                # we move on to the next yesno toggle.
                time.sleep(0.5)

    # Some text inputs are autocomplete fields (LinkedIn city / location):
    # typing alone isn't enough — a <ul> dropdown with role="option" appears
    # and the user is expected to click one. After the bulk fill, give the
    # dropdown a moment to render, then click the option that best matches
    # each answer we typed.
    text_pairs = [
        (q.get("_selector"), a) for q, a in qa_pairs
        if a and q.get("type") in ("text", "tel", "email", "number", "")
    ]
    if text_pairs:
        time.sleep(0.6)
        _click_autocomplete_options(driver, text_pairs)
    return n


def _click_autocomplete_options(driver, text_pairs: list[tuple[str, str]]) -> int:
    """For each (input selector, typed answer) pair, look for a visible
    [role='option'] dropdown that appeared after typing and click the one
    whose text best matches the answer.
    """
    js = JS_WALK_PROLOG + """
        const pairs = arguments[0];
        function findInput(sel) {
            for (const r of [document, ...Array.from(deepNodes(document))
                    .filter(n => n.shadowRoot).map(n => n.shadowRoot)]) {
                try { const x = r.querySelector(sel); if (x) return x; } catch (e) {}
            }
            return null;
        }
        let clicked = 0;
        // Collect all currently visible role=option elements across shadow DOM
        const allOpts = [];
        for (const o of deepNodes(document)) {
            if (!o.getAttribute) continue;
            if (o.getAttribute('role') !== 'option' && o.tagName !== 'LI') continue;
            if (!isVisible(o)) continue;
            const t = (o.textContent || '').trim();
            if (!t || t.length > 200) continue;
            allOpts.push(o);
        }
        if (!allOpts.length) return 0;

        for (const {selector, answer} of pairs) {
            const inp = selector ? findInput(selector) : null;
            const inpRect = inp && inp.getBoundingClientRect ? inp.getBoundingClientRect() : null;
            const ans = (answer || '').toLowerCase().trim();
            if (!ans) continue;
            // Score each option: must be near the input (within ~400 px below)
            // AND text starts with / contains the answer.
            let best = null;
            let bestScore = -1;
            for (const o of allOpts) {
                const t = (o.textContent || '').toLowerCase().trim();
                let score = 0;
                if (t === ans) score += 100;
                else if (t.startsWith(ans)) score += 60;
                else if (t.includes(ans)) score += 30;
                else continue;
                if (inpRect) {
                    const oRect = o.getBoundingClientRect();
                    const dy = oRect.top - inpRect.bottom;
                    if (dy > -50 && dy < 500) score += 20;  // near and below
                    else score -= 30;
                }
                if (score > bestScore) { best = o; bestScore = score; }
            }
            if (best && bestScore > 0) {
                best.click();
                clicked++;
            }
        }
        return clicked;
    """
    try:
        n = driver.execute_script(js, [
            {"selector": sel, "answer": ans} for sel, ans in text_pairs
        ]) or 0
        if n:
            logger.info("autocomplete: clicked {} dropdown option(s)", n)
        return n
    except Exception as e:
        logger.debug("autocomplete click pass failed: {}", e)
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

    # External Apply — LinkedIn shows "Apply" (no Easy badge) that opens the
    # employer's ATS in a new tab. Match the bare "Apply" button by aria-label
    # "Apply to *" (NOT "Easy Apply to *", filtered above).
    ext_btn = _find_external_apply_button(driver)
    if ext_btn:
        result = _try_external_apply(driver, ext_btn, company_name="", job_title="", job_url=job_url, job_tag=job_tag)
        if result:
            return result

    # Fallback: "I'm interested" — LinkedIn's soft-signal alternative
    interested = find_button_by_text(driver, r"i.?m interested", timeout=2)
    if interested:
        human_sleep(0.8, 1.8)
        if not robust_click(driver, interested, "interested"):
            _diag_save(driver, f"{job_tag}_interested_click_failed")
            return ApplyResult(ApplyOutcome.FAILED, "I'm interested click failed")
        logger.info("clicked I'm interested (soft-signal)")
        human_sleep(1.5, 3)
        confirm = find_button_by_text(driver, r"^(?:submit|confirm|done|got it)$", timeout=2)
        if confirm:
            robust_click(driver, confirm, "interested_confirm")
            human_sleep(1, 2)
        return ApplyResult(ApplyOutcome.INTEREST_SIGNALED, detail="clicked I'm interested")

    # Neither button → really no apply path
    dump = dump_buttons(driver)
    logger.info("No Apply / Easy Apply / I'm interested. Buttons: {}", dump)
    _diag_save(driver, f"{job_tag}_no_apply")
    return ApplyResult(ApplyOutcome.NO_EASY_APPLY, detail=f"buttons={dump}")


def _find_external_apply_button(driver):
    """Find the LinkedIn 'Apply' button (NOT Easy Apply) that opens an
    external ATS in a new tab. Matches `aria-label='Apply to <company>'`
    or button text 'Apply' (no 'Easy' prefix)."""
    js = JS_WALK_PROLOG + """
        for (const el of deepNodes(document)) {
            if (el.tagName !== 'BUTTON' && el.tagName !== 'A') continue;
            if (el.disabled) continue;
            if (!isVisible(el)) continue;
            const text = (el.textContent || '').trim().toLowerCase();
            const aria = ((el.getAttribute && el.getAttribute('aria-label')) || '').toLowerCase();
            // Skip Easy Apply — that's handled separately
            if (text.includes('easy apply') || aria.includes('easy apply')) continue;
            // Match 'Apply' (button/anchor) or aria-label 'Apply to ...'
            if (/^apply$/.test(text) || /^apply\\b/.test(aria) || /apply on company website/i.test(text + ' ' + aria)) {
                return el;
            }
        }
        return null;
    """
    try:
        return driver.execute_script(js)
    except Exception:
        return None


def _try_external_apply(driver, ext_btn, company_name: str, job_title: str, job_url: str, job_tag: str):
    """Click the external Apply button → capture the ATS URL → close the
    new Selenium tab → hand off to a Camoufox session for the actual form
    work. Selenium gets fingerprint-flagged by Cloudflare on hardened ATSes
    (Rippling/Aalyria) before the form even renders; Camoufox passes
    silently. The Selenium driver here is JUST the LinkedIn session — it
    captures the URL and bows out."""
    from app.modules.automation.adapters.external_apply import (
        channel_for_handler, load_ats_context,
    )
    from app.modules.automation.adapters.external_apply.runner import (
        run_external_apply_sync,
    )

    main_handles = driver.window_handles[:]
    human_sleep(0.6, 1.2)
    try:
        ext_btn.click()
    except Exception as e:
        logger.warning("external Apply click failed: {}", e)
        return None

    # Wait for new tab
    end = time.monotonic() + 6
    new_handle = None
    while time.monotonic() < end:
        time.sleep(0.3)
        diff = [h for h in driver.window_handles if h not in main_handles]
        if diff:
            new_handle = diff[0]
            break
    if not new_handle:
        logger.info("external Apply click did not open a new tab")
        return None

    driver.switch_to.window(new_handle)
    time.sleep(2)
    ats_url = driver.current_url
    # Hand the URL to Camoufox — close this Selenium tab so we don't waste
    # a flagged session.
    try:
        driver.close()
    except Exception:
        pass
    driver.switch_to.window(main_handles[0])

    ctx = load_ats_context(
        company=company_name or "(unknown)",
        job_title=job_title,
        job_url=job_url,
        ats_url=ats_url,
    )
    result = run_external_apply_sync(ats_url, ctx, headless=False)
    _diag_save(driver, f"{job_tag}_ext_{result.ats_name}_done")

    if result.success:
        return ApplyResult(
            ApplyOutcome.APPLIED,
            detail=f"external/{result.ats_name}: {result.detail}",
            pages=result.pages,
        )
    return ApplyResult(
        ApplyOutcome.FAILED,
        detail=f"external/{result.ats_name}: {result.detail}",
        pages=result.pages,
    )


async def _autofill_via_llm(driver, page_idx: int, job_tag: str) -> int:
    """Extract unfilled required fields, ask Claude for answers, fill them.
    Returns count of successfully filled fields."""
    from app.modules.applies import (
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
    # For optional fields, require higher confidence (≥0.6) before we touch them.
    # Required fields always filled with whatever the LLM returned.
    qa_pairs = []
    skipped_optional = 0
    for i, (q_raw, q, a) in enumerate(zip(raw_qs, questions, answers)):
        if not q.required and a.confidence < 0.6:
            skipped_optional += 1
            continue
        qa_pairs.append((q_raw, a.answer))
    n = fill_answers(driver, qa_pairs)
    logger.info(
        "[p{}] LLM filled {}/{} fields (skipped {} low-conf optional)",
        page_idx + 1, n, len(qa_pairs), skipped_optional,
    )
    if n:
        _diag_save(driver, f"{job_tag}_after_autofill_p{page_idx}")
    return n


def _run_async(coro):
    """Run an async coroutine from sync code, even if a loop is already running.

    apply_to_job/_walk_modal are sync (Selenium driver is sync) but they need
    to call the async LLM autofill. When invoked from a CLI command that
    itself is wrapped in asyncio.run(), a plain asyncio.run() inside would
    raise 'cannot be called from a running event loop'. Detect and run in a
    thread.
    """
    import asyncio
    import concurrent.futures
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


def _walk_modal(driver, profile_phone: str, job_tag: str = "nojid") -> ApplyResult:
    last_was_review = False
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

        # Review? — same flow as Continue: proactive autofill if unfilled,
        # then click. If Review was clicked on the previous iteration AND the
        # button is still here, the modal is stuck — bail with diag.
        review = find_button_by_text(driver, r"^review( your application)?$", timeout=1)
        if review:
            if last_was_review:
                logger.warning(
                    "Review button still visible after previous click at page {} — modal stuck, bailing",
                    page_idx + 1,
                )
                _diag_save(driver, f"{job_tag}_review_loop_p{page_idx}")
                _close_modal(driver)
                return ApplyResult(
                    ApplyOutcome.FAILED,
                    detail=f"Review loop at page {page_idx + 1} — Submit never appeared",
                    pages=page_idx + 1,
                )
            # Proactive autofill BEFORE clicking Review (the page may have
            # required fields with no red errors yet).
            pre_qs = extract_unfilled_questions(driver)
            if pre_qs:
                logger.info(
                    "[p{}] {} unfilled required field(s) detected pre-Review — autofilling",
                    page_idx + 1, len(pre_qs),
                )
                _run_async(_autofill_via_llm(driver, page_idx, job_tag))
                human_sleep(0.5, 1.0)
            logger.info("Review at page {}", page_idx + 1)
            _diag_save(driver, f"{job_tag}_review_p{page_idx}")
            robust_click(driver, review, "review")
            human_sleep(2, 3)
            last_was_review = True
            continue
        last_was_review = False

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
                    _run_async(_autofill_via_llm(driver, page_idx, job_tag))
                    human_sleep(0.5, 1.0)
                logger.info("Continue/Next at page {}", page_idx + 1)
                robust_click(driver, cont, "continue")
                human_sleep(1, 2)
                continue
            # Errors visible → autofill and retry once
            logger.info("Required fields at page {} — invoking LLM autofill", page_idx + 1)
            _diag_save(driver, f"{job_tag}_required_fields_p{page_idx}")
            n_filled = _run_async(_autofill_via_llm(driver, page_idx, job_tag))
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
