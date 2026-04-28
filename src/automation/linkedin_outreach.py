"""LinkedIn outreach automator — Flank 2 of the two-flank apply system.

Given a (linkedin_profile_url, note_text), opens the profile in Camoufox and
sends the right kind of outreach based on what's available:

  - "Message" button visible (1st-degree connection OR Open Profile)
        → open chat → paste note as a message → send
  - "Connect" button visible (2nd/3rd degree)
        → click Connect → "Add a note" → paste → Send invitation
  - Neither visible (rare; behind More dropdown OR InMail-only)
        → try More menu → if still no Connect, return Skipped

Returns OutreachResult so the orchestrator can update the applies row.

Anti-ban guardrails:
  - human_sleep() between every action (log-normal 0.6-2.4s by default)
  - Detects rate-limit / CAPTCHA / "verify identity" pages and aborts
  - Caller (orchestrator) enforces daily caps + multi-minute pauses between batches
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from loguru import logger
from playwright.async_api import Page, TimeoutError as PWTimeoutError

from src.automation.browser import human_sleep

# LinkedIn DOM selectors as of 2026. Wrapped in lists so we try each in order
# (LinkedIn A/B-tests these; new variants get prepended).

_CONNECT_BUTTON_SELECTORS = [
    # Main profile hero — scoped tightly so we don't click sidebar
    # "People you may know" Connect buttons
    'section.pv-top-card button[aria-label*="Invite"][aria-label*="connect" i]',
    'section.pv-top-card button:has-text("Connect")',
    '.ph5 button[aria-label*="Invite"][aria-label*="connect" i]',
    '.ph5 button:has-text("Connect")',
    # Fallback: ANY "Invite X to connect" — but ONLY first one in main area
    'main section button[aria-label*="Invite"][aria-label*="connect" i]',
]

_MESSAGE_BUTTON_SELECTORS = [
    'section.pv-top-card button[aria-label^="Message "]',
    'section.pv-top-card button:has-text("Message")',
    '.ph5 button[aria-label^="Message "]',
    '.ph5 button:has-text("Message")',
    'main section button[aria-label^="Message "]',
]

_MORE_BUTTON_SELECTORS = [
    'section.pv-top-card button[aria-label="More actions"]',
    '.ph5 button[aria-label="More actions"]',
    'main section button[aria-label="More actions"]',
]

# Inside the "Send invitation" modal
_ADD_NOTE_BUTTON_SELECTORS = [
    'button[aria-label="Add a note"]',
    'button:has-text("Add a note")',
]
_INVITE_NOTE_TEXTAREA = 'textarea#custom-message, textarea[name="message"]'
_SEND_INVITE_BUTTON_SELECTORS = [
    'button[aria-label="Send invitation"]',
    'button[aria-label="Send without a note"]',
    'button:has-text("Send invitation")',
    'button:has-text("Send")',
]

# Inside the message overlay (1st-degree chat)
_MESSAGE_TEXTAREA = 'div.msg-form__contenteditable[contenteditable="true"]'
_SEND_MESSAGE_BUTTON_SELECTORS = [
    'button.msg-form__send-button',
    'button[aria-label="Send"]',
]

# Pages that mean LinkedIn is unhappy with us
_RATE_LIMIT_TEXT_MARKERS = [
    "let's confirm it's really you",
    "we've restricted your account",
    "unusual activity",
    "verification",
    "complete this puzzle",
    "i'm not a robot",
]


class OutreachOutcome(StrEnum):
    SENT_VIA_MESSAGE = "sent_via_message"   # 1st-degree or Open Profile direct message
    SENT_VIA_INVITE = "sent_via_invite"     # Connect + note (becomes message after accept)
    SKIPPED_NO_BUTTON = "skipped_no_button"  # neither Connect nor Message available
    SKIPPED_PROFILE_GONE = "skipped_profile_gone"  # 404 / removed
    BLOCKED_RATE_LIMIT = "blocked_rate_limit"  # CAPTCHA / verify / restricted
    FAILED = "failed"


@dataclass
class OutreachResult:
    outcome: OutreachOutcome
    detail: str = ""           # extra diagnostic, optional
    note_truncated: bool = False
    note_chars_sent: int = 0


# LinkedIn Premium-Career invite-note cap is 300 chars; without Premium it's
# also 300 since 2024. We trim to be safe.
INVITE_NOTE_MAX_CHARS = 300


async def send_outreach(page: Page, profile_url: str, note: str, body: str) -> OutreachResult:
    """Open a LinkedIn profile and send outreach. Picks Message > Connect > Skip.

    Args:
        page: Camoufox page (already with cookies, logged in)
        profile_url: full URL like https://www.linkedin.com/in/<slug>
        note: short version (≤300 chars), used as Connect-invite note
        body: full version (~600-1000 chars), used in Message channel

    Both `note` and `body` are typed character-by-character via Playwright
    `type()` (which fires keydown/keyup events) — looks more like real input
    than `fill()`.
    """
    # 1. Navigate
    logger.info("Opening profile: {}", profile_url)
    try:
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=20000)
    except PWTimeoutError:
        logger.warning("goto timeout for {}", profile_url)
        return OutreachResult(OutreachOutcome.FAILED, "goto timeout")
    await human_sleep(2, 5)  # let page settle

    # 2. Bail-out check: rate-limit / verify page
    if await _is_blocked(page):
        return OutreachResult(OutreachOutcome.BLOCKED_RATE_LIMIT,
                              "LinkedIn shows verification/restriction page")

    # 3. Profile gone (404)
    if "/in/" not in page.url or "linkedin.com" not in page.url:
        return OutreachResult(OutreachOutcome.SKIPPED_PROFILE_GONE,
                              f"redirected to {page.url}")

    # 4. Try Message first (best — 1st-degree or Open Profile)
    msg_btn = await _first_visible(page, _MESSAGE_BUTTON_SELECTORS)
    if msg_btn:
        logger.info("Found Message button — using direct chat channel")
        return await _send_via_message(page, msg_btn, body)

    # 5. Try Connect (2nd/3rd degree)
    connect_btn = await _first_visible(page, _CONNECT_BUTTON_SELECTORS)
    if not connect_btn:
        # Try expanding the More dropdown
        more = await _first_visible(page, _MORE_BUTTON_SELECTORS)
        if more:
            logger.debug("Connect not visible — expanding More menu")
            await more.click()
            await human_sleep(0.6, 1.4)
            connect_btn = await _first_visible(page, _CONNECT_BUTTON_SELECTORS)

    if connect_btn:
        logger.info("Found Connect button — sending invite with note")
        return await _send_via_invite(page, connect_btn, note)

    # 6. Nothing available
    return OutreachResult(OutreachOutcome.SKIPPED_NO_BUTTON,
                          "neither Message nor Connect button found on profile")


async def _is_blocked(page: Page) -> bool:
    """Detect LinkedIn anti-bot pages. Conservative — false positives are fine."""
    text = (await page.evaluate("() => document.body.innerText")).lower()[:5000]
    for marker in _RATE_LIMIT_TEXT_MARKERS:
        if marker in text:
            logger.warning("Rate-limit/verify page detected: {!r}", marker)
            return True
    # Also check for /checkpoint/ in URL
    if "/checkpoint/" in page.url or "/uas/login" in page.url:
        return True
    return False


async def _first_visible(page: Page, selectors: list[str]):
    """Return the first selector that resolves to a visible element, else None."""
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=1500):
                return el
        except Exception:
            continue
    return None


async def _send_via_message(page: Page, msg_btn, body: str) -> OutreachResult:
    """Click Message → chat overlay → paste body → Send."""
    try:
        await msg_btn.click()
        await human_sleep(0.8, 1.8)
        textarea = page.locator(_MESSAGE_TEXTAREA).first
        await textarea.wait_for(state="visible", timeout=8000)
        await textarea.click()
        await human_sleep(0.3, 0.8)
        # type human-paced (~300 chars/min ≈ 200 ms/char)
        await textarea.type(body, delay=70)
        await human_sleep(0.6, 1.4)

        send_btn = await _first_visible(page, _SEND_MESSAGE_BUTTON_SELECTORS)
        if not send_btn:
            return OutreachResult(OutreachOutcome.FAILED, "send button not found in chat")
        await send_btn.click()
        await human_sleep(1.5, 3.0)
        return OutreachResult(
            OutreachOutcome.SENT_VIA_MESSAGE,
            note_chars_sent=len(body),
        )
    except PWTimeoutError as e:
        return OutreachResult(OutreachOutcome.FAILED, f"timeout in message flow: {e}")
    except Exception as e:
        return OutreachResult(OutreachOutcome.FAILED, f"message flow error: {e}")


async def _send_via_invite(page: Page, connect_btn, note: str) -> OutreachResult:
    """Click Connect → Add a note → paste → Send invitation.

    LinkedIn imposes a 300-char invite-note cap. We trim if longer.
    """
    truncated = False
    if len(note) > INVITE_NOTE_MAX_CHARS:
        note = note[:INVITE_NOTE_MAX_CHARS - 3].rstrip() + "..."
        truncated = True

    try:
        await connect_btn.click()
        await human_sleep(1.0, 2.0)

        # The invite modal opens. Some flows show "Add a note" / "Send without note"
        # buttons; others go straight to a textarea.
        add_note = await _first_visible(page, _ADD_NOTE_BUTTON_SELECTORS)
        if add_note:
            await add_note.click()
            await human_sleep(0.5, 1.2)

        textarea = page.locator(_INVITE_NOTE_TEXTAREA).first
        await textarea.wait_for(state="visible", timeout=8000)
        await textarea.click()
        await human_sleep(0.3, 0.8)
        await textarea.type(note, delay=60)
        await human_sleep(0.6, 1.4)

        send_btn = await _first_visible(page, _SEND_INVITE_BUTTON_SELECTORS)
        if not send_btn:
            return OutreachResult(OutreachOutcome.FAILED, "send-invitation button not found")
        await send_btn.click()
        await human_sleep(2.0, 4.0)

        return OutreachResult(
            OutreachOutcome.SENT_VIA_INVITE,
            note_truncated=truncated,
            note_chars_sent=len(note),
        )
    except PWTimeoutError as e:
        return OutreachResult(OutreachOutcome.FAILED, f"timeout in invite flow: {e}")
    except Exception as e:
        return OutreachResult(OutreachOutcome.FAILED, f"invite flow error: {e}")
