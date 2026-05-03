"""Common helpers + base contract for per-ATS handlers (Camoufox/Playwright).

Each external ATS (Greenhouse / Lever / Ashby / Workday / unknown) has its
own form layout but the same overall flow:
    1. fill basic identity (name, email, phone, location, linkedin)
    2. upload resume (file input)
    3. fill custom required questions via LLM
    4. click submit

All helpers operate on a Playwright async `Page` (Camoufox-backed). Selenium
is gone from this path — Cloudflare Turnstile detects Selenium at the
fingerprint layer regardless of click style; Camoufox passes silently.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from loguru import logger

# Resume PDF lives at the project root next to README.md.
RESUME_PATH = Path(__file__).resolve().parents[5] / "resume_en.pdf"


# AtsContext / AtsResult / AtsHandler now live in the port module.
from app.modules.automation.ports.external_apply import (  # noqa: E402,F401
    AtsContext as _PortAtsContext,
    AtsResult,
    AtsHandler,
)


def _make_ats_context(**kwargs) -> _PortAtsContext:
    """Factory that injects the project-wide RESUME_PATH default."""
    kwargs.setdefault("resume_path", RESUME_PATH)
    return _PortAtsContext(**kwargs)


# Re-export the port's AtsContext under the original name so existing
# `from .base import AtsContext` callers keep working.
AtsContext = _PortAtsContext


def _parse_profile(info: str) -> dict[str, str]:
    """Pull canonical profile fields out of free-form `users.info` text.

    Recognized lines (case-insensitive):
        Name: Sergey Sergeev
        Email: ...
        Phone: ...
        Location: Bar, Montenegro (...)         -> 'Bar, Montenegro' (strip paren tail)
        LinkedIn: handle  (URL: https://...)    -> the URL inside parens
    Returns dict with keys: first_name, last_name, email, phone, location, linkedin.
    """
    out: dict[str, str] = {}
    if not info:
        return out
    # First-match-wins: the structured header in users.info comes BEFORE the
    # verbatim CV dump, and the structured values are authoritative (the CV
    # may carry stale or less specific data, e.g. 'Montenegro' vs 'Bar, MNE').
    for raw in info.splitlines():
        line = raw.strip()
        low = line.lower()
        if low.startswith("name:") and "first_name" not in out:
            full = line.split(":", 1)[1].strip()
            parts = full.split(maxsplit=1)
            if parts:
                out["first_name"] = parts[0]
                out["last_name"] = parts[1] if len(parts) > 1 else ""
        elif low.startswith("email:") and "email" not in out:
            out["email"] = line.split(":", 1)[1].strip()
        elif low.startswith("phone:") and "phone" not in out:
            out["phone"] = line.split(":", 1)[1].strip()
        elif low.startswith("location:") and "location" not in out:
            val = line.split(":", 1)[1].strip()
            # 'Bar, Montenegro (UTC+2). Open to remote ...' -> 'Bar, Montenegro'
            val = re.split(r"[(.]", val, maxsplit=1)[0].strip().rstrip(",")
            out["location"] = val
        elif low.startswith("linkedin:") and "linkedin" not in out:
            m = re.search(r"URL:\s*(https?://\S+?)\s*\)", line, re.IGNORECASE)
            if m:
                out["linkedin"] = m.group(1).strip()
            else:
                handle = line.split(":", 1)[1].strip().lstrip("@/ ")
                if handle and not handle.startswith("http"):
                    out["linkedin"] = f"https://www.linkedin.com/in/{handle}/"
                elif handle:
                    out["linkedin"] = handle
    return out


def load_ats_context(
    company: str, job_title: str, job_url: str, ats_url: str,
) -> AtsContext:
    """Build an `AtsContext` populated from the default user's `users.info`.

    All profile fields come from the DB — the only ground truth. If the user
    record is missing a field, it stays empty and the handler skips it.
    """
    from app.modules.users import load_user_info
    info = load_user_info()
    p = _parse_profile(info)
    if not p:
        logger.warning("load_ats_context: users.info empty or unparseable")
    return AtsContext(
        company=company, job_title=job_title, job_url=job_url, ats_url=ats_url,
        profile_first_name=p.get("first_name", ""),
        profile_last_name=p.get("last_name", ""),
        profile_email=p.get("email", ""),
        profile_phone=p.get("phone", ""),
        profile_location=p.get("location", ""),
        profile_linkedin=p.get("linkedin", ""),
        resume_path=RESUME_PATH,
    )


# --- Common DOM helpers (Playwright async) ---


# Walks light + shadow DOM, yielding every element node — same primitive used
# by every helper below. Keeping it as a string lets us inject from
# `page.evaluate(JS)` without the round-trip cost of multiple eval calls.
_DEEP_WALK = """
function* deepNodes(root) {
    if (!root) return;
    const stack = [root];
    while (stack.length) {
        const n = stack.pop();
        if (!n) continue;
        if (n.nodeType === 1) yield n;
        if (n.shadowRoot) stack.push(n.shadowRoot);
        const k = n.children || n.childNodes || [];
        for (let i = k.length - 1; i >= 0; i--) stack.push(k[i]);
    }
}
"""


async def find_visible(page, selector: str, timeout: float = 4.0) -> dict | None:
    """Return a serializable rect+identifier for the first visible element
    matching `selector` across light + shadow DOM, or None on timeout.

    Returns a dict like {x, y, w, h, tag, id, name} for downstream click
    coords; for Playwright-native interaction, prefer `page.locator(selector)
    .first` directly when the element isn't behind a shadow boundary.
    """
    js = (
        "(_sel) => {"
        + _DEEP_WALK
        + """
        for (const root of [document, ...Array.from(deepNodes(document))
                .filter(n => n.shadowRoot).map(n => n.shadowRoot)]) {
            try {
                for (const el of root.querySelectorAll(_sel)) {
                    const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                    if (!r || r.width < 4 || r.height < 4) continue;
                    return {
                        x: r.x, y: r.y, w: r.width, h: r.height,
                        tag: el.tagName, id: el.id || '',
                        name: (el.getAttribute && el.getAttribute('name')) || '',
                    };
                }
            } catch (e) {}
        }
        return null;
        }"""
    )
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        try:
            hit = await page.evaluate(js, selector)
            if hit:
                return hit
        except Exception as e:
            logger.debug("find_visible eval failed: {}", e)
        await asyncio.sleep(0.2)
    return None


async def fill_input(page, selector: str, value: str) -> bool:
    """Find an input by CSS selector (in light or shadow DOM), set its value
    via the native setter so React/Vue see it, then dispatch input + change."""
    if not value:
        return False
    js = (
        "(args) => { const _sel = args[0]; const _val = args[1];"
        + _DEEP_WALK
        + """
        for (const root of [document, ...Array.from(deepNodes(document))
                .filter(n => n.shadowRoot).map(n => n.shadowRoot)]) {
            const el = root.querySelector(_sel);
            if (!el) continue;
            const proto = Object.getPrototypeOf(el);
            const desc = Object.getOwnPropertyDescriptor(proto, 'value');
            if (desc && desc.set) desc.set.call(el, _val);
            else el.value = _val;
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            return true;
        }
        return false;
        }"""
    )
    try:
        return bool(await page.evaluate(js, [selector, value]))
    except Exception as e:
        logger.debug("fill_input failed: {}", e)
        return False


async def upload_resume(page, file_input_selector: str, resume_path: Path) -> bool:
    """Set a Playwright file input. Tries native locator.set_input_files first
    (works on hidden inputs too); falls back to a JS-driven file injection
    via DataTransfer (for inputs that ATSes wrap in a custom element)."""
    if not resume_path.exists():
        logger.warning("resume not found: {}", resume_path)
        return False
    abs_path = str(resume_path.resolve())
    try:
        loc = page.locator(file_input_selector).first
        # Bypass visibility — many ATSes hide the file input behind a button
        await loc.set_input_files(abs_path, timeout=2000)
        await asyncio.sleep(1.0)
        return True
    except Exception as e:
        logger.debug("locator.set_input_files failed for {}: {}", file_input_selector, e)
    logger.warning("file input not found / not settable: {}", file_input_selector)
    return False


async def click_submit(page, candidates: list[str]) -> bool:
    """Try each CSS selector in order, click the first visible one."""
    for sel in candidates:
        hit = await find_visible(page, sel, timeout=1)
        if not hit:
            continue
        try:
            await page.evaluate(
                "(sel) => { const e = document.querySelector(sel); "
                "if (e) e.scrollIntoView({block:'center'}); }",
                sel,
            )
            await asyncio.sleep(0.3)
            await page.locator(sel).first.click(timeout=2000)
            logger.info("clicked submit: {}", sel)
            return True
        except Exception as e:
            logger.debug("submit click failed for {}: {}", sel, e)
    return False


async def click_button_by_text(page, regex: str, timeout: float = 4.0) -> bool:
    """Click first visible enabled <button>/<a>/role=button whose text or
    aria-label matches. Walks light + shadow DOM. Skips disabled buttons
    (incl. aria-disabled / pointer-events:none) so a disabled submit isn't
    reported as a successful click.

    Returns the click via real Playwright `page.mouse.click()` at the
    element's center — Camoufox humanize wraps it with bezier+jitter.
    """
    js = (
        "(_pat) => { const _re = new RegExp(_pat, 'i');"
        + _DEEP_WALK
        + """
        for (const el of deepNodes(document)) {
            if (el.tagName !== 'BUTTON' && el.tagName !== 'A' &&
                (!el.getAttribute || el.getAttribute('role') !== 'button')) continue;
            if (el.disabled) continue;
            if (el.getAttribute && el.getAttribute('aria-disabled') === 'true') continue;
            const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
            if (!r || r.width < 4 || r.height < 4) continue;
            try {
                const style = window.getComputedStyle(el);
                if (style && style.pointerEvents === 'none') continue;
            } catch (e) {}
            const t = (el.textContent || '').trim();
            const a = (el.getAttribute && el.getAttribute('aria-label')) || '';
            if (_re.test(t) || _re.test(a)) {
                el.scrollIntoView({block: 'center'});
                const r2 = el.getBoundingClientRect();
                return {x: r2.x + r2.width / 2, y: r2.y + r2.height / 2};
            }
        }
        return null;
        }"""
    )
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        try:
            hit = await page.evaluate(js, regex)
            if hit:
                await asyncio.sleep(0.2)
                await page.mouse.click(hit["x"], hit["y"])
                return True
        except Exception as e:
            logger.debug("click_button_by_text eval failed: {}", e)
        await asyncio.sleep(0.2)
    return False


def normalize_phone(raw: str) -> str:
    """Some ATS don't like '+' prefix; also strip spaces / dashes."""
    return re.sub(r"[^\d+]", "", raw or "")


async def detect_form_errors(page) -> list[str]:
    """After clicking submit, check for client-side validation errors.

    Returns a list of error strings — empty list means submit looks OK. We
    look for visible error banners, "required field missing" markers, and
    Ashby/Greenhouse-style red error blocks.
    """
    js = (
        "() => {"
        + _DEEP_WALK
        + r"""
        const errors = [];
        const reError = /missing entry|this field is required|^required\b|please (enter|select|fill|complete)|errors? on the form|needs corrections/i;
        for (const el of deepNodes(document)) {
            if (!el.getBoundingClientRect) continue;
            const role = el.getAttribute && el.getAttribute('role');
            const cls = (el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className) || '';
            const isAlertLike = role === 'alert' ||
                /\b(error|invalid|required-warning|form-error|errorBanner)\b/i.test(typeof cls === 'string' ? cls : '');
            if (!isAlertLike) continue;
            const r = el.getBoundingClientRect();
            if (r.width < 1 || r.height < 1) continue;
            const t = (el.textContent || '').trim();
            if (t && reError.test(t)) errors.push(t.slice(0, 200));
        }
        for (const el of deepNodes(document)) {
            if (el.tagName !== 'INPUT' && el.tagName !== 'TEXTAREA' &&
                el.tagName !== 'SELECT') continue;
            if (el.getAttribute && el.getAttribute('aria-invalid') === 'true') {
                let p = el.parentElement;
                for (let i = 0; i < 4 && p; i++) {
                    const txt = (p.textContent || '').trim();
                    if (reError.test(txt)) {
                        errors.push(txt.slice(0, 200));
                        break;
                    }
                    p = p.parentElement;
                }
            }
        }
        return errors;
        }"""
    )
    try:
        out = await page.evaluate(js) or []
        seen, unique = set(), []
        for e in out:
            key = e.lower()[:80]
            if key not in seen:
                seen.add(key)
                unique.append(e)
        return unique
    except Exception as e:
        logger.debug("detect_form_errors failed: {}", e)
        return []
