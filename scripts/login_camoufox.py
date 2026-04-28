"""One-shot helper: open Camoufox visible, wait for user to log into LinkedIn,
then close cleanly. Profile persists for future automated runs.

Usage:
    .venv/bin/python3 scripts/login_camoufox.py

Window stays open up to 10 minutes — log in, solve any captcha, then it auto-detects
/feed/ and closes. After that, all `jhp send-outreach` runs will use this session.
"""
import asyncio
import sys

from src.automation.browser import browser_session


async def main() -> int:
    async with browser_session(headless=False) as page:
        print("Opening LinkedIn login page in Camoufox window...")
        await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=20000)

        print("\n" + "=" * 60)
        print("LOG IN WITH YOUR EMAIL + PASSWORD IN THE OPENED WINDOW.")
        print("Solve any captcha LinkedIn shows.")
        print("Window stays open 10 minutes. Auto-closes once /feed/ is detected.")
        print("=" * 60 + "\n")

        for i in range(120):  # 120 * 5s = 10 min
            await asyncio.sleep(5)
            url = page.url
            if "/feed/" in url:
                print(f"\n✅ Login detected. URL: {url}")
                print("Profile saved to ~/.jhp/camoufox-profile/")
                print("All future `jhp send-outreach` runs will reuse this session.")
                # give cookies a moment to fully persist to disk
                await asyncio.sleep(3)
                return 0
            if i % 12 == 0 and i > 0:  # every minute
                print(f"  ...still waiting (current URL: {url[:60]})")

        print("\n⚠️ 10 min timeout — no /feed/ detected. Closing without confirming login.")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
