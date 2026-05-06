"""Camoufox-based browser session for LinkedIn / ATS automation.

Strategy that actually works (after several iterations):

NO cookie copying from user's Firefox. Sharing one li_at between Firefox and
Camoufox triggers LinkedIn's anti-bot — server-side invalidates the token for
both browsers, killing both sessions.

Instead:
- User logs into LinkedIn ONCE inside Camoufox (via scripts/login_camoufox.py)
- Persistent profile preserves the session across runs
- LinkedIn sees one stable device (Camoufox), no detection
- User's real Firefox stays untouched

Pinned for stability:
- os=linux (consistent across runs)
- locale=en-US (geoip was picking Serbian on Montenegro IP)
- humanize=True (bezier mouse curves + reading-speed pauses)
"""
from __future__ import annotations

import asyncio
import random
from contextlib import asynccontextmanager
from pathlib import Path

from camoufox.async_api import AsyncCamoufox
from loguru import logger

# Camoufox profile dir — preserves cookies + fingerprint across runs.
# Login happens ONCE here (via scripts/login_camoufox.py); from then on
# every launch reuses this profile's session. Don't `rm -rf` this dir!
PROFILE_DIR = Path.home() / ".jhp" / "camoufox-profile"


def _ensure_profile_dir() -> None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)


async def human_sleep(min_s: float = 0.6, max_s: float = 2.4) -> None:
    """Random delay drawn from a log-normal-ish distribution.

    Avoids the 'exactly 1.0s every time' pattern bot detectors flag.
    """
    midpoint = (min_s + max_s) / 2
    sigma = (max_s - min_s) / 4
    delay = random.lognormvariate(0, 0.4) * midpoint
    delay = max(min_s, min(max_s, delay))
    delay += random.uniform(-sigma * 0.2, sigma * 0.2)
    await asyncio.sleep(max(0.05, delay))


async def _periodic_screenshots(page, out_dir: Path, interval_s: float, stop_evt: asyncio.Event) -> None:
    """Background task: snapshot the page every `interval_s` seconds into
    `out_dir/frame_<N>.png` until stop_evt is set. Used for self-diag of
    the form-fill flow when video recording isn't supported (Camoufox
    runs Firefox-Juggler, which Playwright's record_video_dir doesn't
    support — `launch_persistent_context` crashes with 'Connection
    closed while reading from the driver').
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    while not stop_evt.is_set():
        try:
            await page.screenshot(path=str(out_dir / f"frame_{n:04d}.png"))
            n += 1
        except Exception as e:
            logger.debug("frame screenshot failed: {}", e)
        try:
            await asyncio.wait_for(stop_evt.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass
    logger.info("periodic screenshots: wrote {} frames to {}", n, out_dir)


@asynccontextmanager
async def browser_session(
    headless: bool = False,
    geoip: bool = True,
    screenshot_dir: str | None = None,
    screenshot_interval_s: float = 2.0,
):
    """Yield a Camoufox Page with fresh LinkedIn auth each launch.

    Args:
        headless: True for background runs, False to watch the browser
        geoip: True spoofs timezone to match IP; locale stays en-US regardless
        screenshot_dir: optional directory for periodic page screenshots
            (PNG frames every `screenshot_interval_s` seconds). Used for
            self-diagnosis when we want to replay what happened on the
            form without bothering the user.
    """
    _ensure_profile_dir()

    async with AsyncCamoufox(
        headless=headless,
        geoip=geoip,
        os="linux",
        locale=["en-US", "en"],
        humanize=True,
        i_know_what_im_doing=True,
        persistent_context=True,
        user_data_dir=str(PROFILE_DIR),
    ) as browser:
        context = browser
        page = await context.new_page()
        snap_task: asyncio.Task | None = None
        snap_stop = asyncio.Event()
        if screenshot_dir:
            snap_task = asyncio.create_task(
                _periodic_screenshots(
                    page, Path(screenshot_dir), screenshot_interval_s, snap_stop,
                )
            )
        try:
            yield page
        finally:
            if snap_task:
                snap_stop.set()
                try:
                    await asyncio.wait_for(snap_task, timeout=5)
                except (asyncio.TimeoutError, Exception):
                    pass
            await page.close()


if __name__ == "__main__":
    async def main():
        async with browser_session(headless=False) as page:
            logger.info("Opening LinkedIn feed…")
            await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
            await human_sleep(2, 5)
            url = page.url
            title = await page.title()
            logger.info("URL: {} — Title: {!r}", url, title)
            if "/feed/" in url and "login" not in url.lower():
                logger.success("✅ Logged in via injected cookies — Camoufox session works")
            else:
                logger.warning("Not logged in (URL: {})", url)

    asyncio.run(main())
