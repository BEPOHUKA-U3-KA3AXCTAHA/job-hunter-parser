"""Common helpers + base contract for per-ATS handlers.

Each external ATS (Greenhouse / Lever / Ashby / Workday / unknown) has its
own form layout but the same overall flow:
    1. fill basic identity (name, email, phone, location, linkedin)
    2. upload resume (file input)
    3. fill custom required questions via LLM
    4. click submit
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from loguru import logger

# Resume PDF lives at the project root next to README.md.
RESUME_PATH = Path(__file__).resolve().parents[5] / "resume_en.pdf"


@dataclass(slots=True)
class AtsContext:
    """Per-job context handed to each ATS handler.

    Profile fields are populated by `load_ats_context()` from the `users.info`
    free-form text in the DB — single source of truth, never hardcode here.
    """

    company: str
    job_title: str
    job_url: str        # the LinkedIn job URL we came from (for logs)
    ats_url: str        # the external URL the Apply button took us to
    profile_first_name: str = ""
    profile_last_name: str = ""
    profile_email: str = ""
    profile_phone: str = ""
    profile_location: str = ""
    profile_linkedin: str = ""
    resume_path: Path = RESUME_PATH


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
    from app.shared.candidate_profile import _load_user_info  # avoid cycle
    info = _load_user_info()
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
    )


@dataclass(slots=True)
class AtsResult:
    """Outcome of one external ATS apply attempt."""

    success: bool
    detail: str = ""
    pages: int = 0
    fields_filled: int = 0
    ats_name: str = ""


@runtime_checkable
class AtsHandler(Protocol):
    """One concrete handler per ATS family."""

    name: str  # 'greenhouse' / 'lever' / 'ashby' / 'workday' / 'generic'

    def can_handle(self, url: str) -> bool: ...

    def apply(self, driver, ctx: AtsContext) -> AtsResult: ...


# --- Common DOM helpers reused across handlers ---


def find_visible(driver, selector: str, timeout: float = 4.0):
    """Return first visible element matching CSS selector across shadow DOM,
    or None on timeout."""
    js = """
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
        const sel = arguments[0];
        for (const root of [document, ...Array.from(deepNodes(document))
                .filter(n => n.shadowRoot).map(n => n.shadowRoot)]) {
            try {
                for (const el of root.querySelectorAll(sel)) {
                    const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                    if (!r) continue;
                    if (r.width < 4 || r.height < 4) continue;
                    return el;
                }
            } catch (e) {}
        }
        return null;
    """
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            el = driver.execute_script(js, selector)
            if el is not None:
                return el
        except Exception:
            pass
        time.sleep(0.2)
    return None


def fill_input(driver, el, value: str) -> bool:
    """Set value on a text input via native setter so React/Vue see it."""
    if not el or not value:
        return False
    js = """
        const el = arguments[0];
        const val = arguments[1];
        const proto = Object.getPrototypeOf(el);
        const desc = Object.getOwnPropertyDescriptor(proto, 'value');
        if (desc && desc.set) desc.set.call(el, val);
        else el.value = val;
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
    """
    try:
        el.click()
    except Exception:
        pass
    try:
        driver.execute_script(js, el, value)
        return True
    except Exception as e:
        logger.debug("fill_input failed: {}", e)
        return False


def upload_resume(driver, file_input_selector: str, resume_path: Path) -> bool:
    """Set a file input's value by sending the keys (Selenium standard trick).
    Returns True if the input was found and the path absolute-resolved file
    exists."""
    if not resume_path.exists():
        logger.warning("resume not found: {}", resume_path)
        return False
    el = find_visible(driver, file_input_selector, timeout=2)
    if not el:
        # File inputs are often hidden — try without visibility check
        try:
            from selenium.webdriver.common.by import By
            el = driver.find_element(By.CSS_SELECTOR, file_input_selector)
        except Exception:
            logger.warning("file input not found: {}", file_input_selector)
            return False
    try:
        el.send_keys(str(resume_path.resolve()))
        time.sleep(1.0)  # give the form time to recognize the upload
        return True
    except Exception as e:
        logger.warning("upload_resume failed: {}", e)
        return False


def click_submit(driver, candidates: list[str]) -> bool:
    """Try each CSS selector in order, click the first visible one."""
    for sel in candidates:
        el = find_visible(driver, sel, timeout=1)
        if el:
            try:
                el.scrollIntoView = None  # noop placeholder
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                time.sleep(0.3)
                el.click()
                logger.info("clicked submit: {}", sel)
                return True
            except Exception as e:
                logger.debug("submit click failed for {}: {}", sel, e)
    return False


def click_button_by_text(driver, regex: str, timeout: float = 4.0) -> bool:
    """Click first visible <button> whose text or aria-label matches regex.
    Walks shadow DOM."""
    js = """
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
        const re = new RegExp(arguments[0], 'i');
        for (const el of deepNodes(document)) {
            if (el.tagName !== 'BUTTON' && el.tagName !== 'A' &&
                (!el.getAttribute || el.getAttribute('role') !== 'button')) continue;
            if (el.disabled) continue;
            const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
            if (!r || r.width < 4 || r.height < 4) continue;
            const t = (el.textContent || '').trim();
            const a = (el.getAttribute && el.getAttribute('aria-label')) || '';
            if (re.test(t) || re.test(a)) return el;
        }
        return null;
    """
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            el = driver.execute_script(js, regex)
            if el is not None:
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                    time.sleep(0.2)
                    el.click()
                    return True
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(0.2)
    return False


def normalize_phone(raw: str) -> str:
    """Some ATS don't like '+' prefix; also strip spaces / dashes."""
    return re.sub(r"[^\d+]", "", raw or "")


def detect_form_errors(driver) -> list[str]:
    """After clicking submit, check for client-side validation errors.

    Returns a list of error strings — empty list means submit looks OK. We
    look for visible error banners, "required field missing" markers, and
    Ashby/Greenhouse-style red error blocks.
    """
    js = """
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
        const errors = [];
        const reError = /missing entry|required|please (enter|select|fill|complete)|invalid|errors? on the form|needs corrections/i;
        for (const el of deepNodes(document)) {
            if (!el.getBoundingClientRect) continue;
            const role = el.getAttribute && el.getAttribute('role');
            const aria = el.getAttribute && el.getAttribute('aria-invalid');
            const cls = (el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className) || '';
            const looksError = role === 'alert' || aria === 'true' ||
                /\\b(error|invalid|required-warning|form-error|errorBanner)\\b/i.test(typeof cls === 'string' ? cls : '');
            if (!looksError) continue;
            const r = el.getBoundingClientRect();
            if (r.width < 1 || r.height < 1) continue;
            const t = (el.textContent || '').trim();
            if (t && reError.test(t)) errors.push(t.slice(0, 200));
        }
        return errors;
    """
    try:
        out = driver.execute_script(js) or []
        # Dedupe while preserving order
        seen, unique = set(), []
        for e in out:
            key = e.lower()[:80]
            if key not in seen:
                seen.add(key); unique.append(e)
        return unique
    except Exception as e:
        logger.debug("detect_form_errors failed: {}", e)
        return []
