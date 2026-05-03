"""End-to-end test of the Firefox extension's external_fill.js pipeline.

Boots the API server in-process, opens a Selenium-driven Firefox with the
extension loaded as a temporary add-on, navigates to the target URL with
the auto-trigger hash, and polls sessionStorage for the result.

Selenium IS still in the loop here for orchestration, but the actual
form-fill executes inside the EXTENSION'S content-script context — same
JS environment as a manual click would use, no automation flag visible
to the page. The Cloudflare-detection problem we hit with pure Selenium
should not reappear.

Usage: test_extension_fill.py [<url>]
"""
from __future__ import annotations

import json
import sys
import time
import threading
from pathlib import Path
from urllib.parse import urlparse

from loguru import logger
from selenium import webdriver
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.firefox.service import Service as FirefoxService

from app.modules.automation.adapters.selenium_bot import _prepare_profile_copy


EXT_DIR = Path(__file__).resolve().parents[1] / "firefox-extension"


def start_api_server():
    """Run uvicorn in a daemon thread so we can keep the test in-process."""
    import uvicorn
    from app.entrypoints.api.server import app

    config = uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="warning")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    # wait for /healthz
    import httpx
    for _ in range(30):
        try:
            r = httpx.get("http://127.0.0.1:8765/healthz", timeout=1.0)
            if r.status_code == 200:
                logger.info("API server up on :8765")
                return server
        except Exception:
            pass
        time.sleep(0.3)
    raise RuntimeError("API server failed to start")


def open_firefox_with_extension():
    """Launch Firefox with the user's profile + load extension as temp add-on."""
    options = FirefoxOptions()
    profile_path = str(_prepare_profile_copy())
    options.add_argument("-profile")
    options.add_argument(profile_path)
    options.set_preference("dom.webdriver.enabled", False)
    options.set_preference("useAutomationExtension", False)

    service = FirefoxService(log_output="/tmp/jhp_geckodriver.log")
    driver = webdriver.Firefox(options=options, service=service)
    driver.implicitly_wait(0)
    driver.set_window_size(1280, 900)

    # Install extension as temporary add-on (Selenium 4.x for Firefox)
    driver.install_addon(str(EXT_DIR), temporary=True)
    logger.info("Extension loaded from {}", EXT_DIR)
    return driver


def run(url: str):
    server = start_api_server()
    driver = open_firefox_with_extension()
    try:
        # Add the auto-trigger hash so external_fill.js fires on document_idle
        target = url + ("&" if "?" in url else "?") + "_=auto" + "#jhp-autofill"
        # Above param prevents URL caching shenanigans
        logger.warning("Navigating to {}", target)
        driver.get(target)

        # Poll sessionStorage for the autofill result
        deadline = time.monotonic() + 480  # 8 min budget — Claude calls are 70-90s each
        result = None
        while time.monotonic() < deadline:
            try:
                raw = driver.execute_script(
                    "return sessionStorage.getItem('jhp-autofill-result');"
                )
                if raw:
                    result = json.loads(raw)
                    logger.info("Got result from extension: {}", result)
                    break
            except Exception:
                pass
            time.sleep(2)
        if not result:
            logger.error("Timed out waiting for extension result (240s)")
        # Save final screenshot + HTML + extension action dumps for post-mortem
        try:
            driver.save_screenshot("/tmp/jhp_diag/extension_final.png")
            html = driver.execute_script("return document.documentElement.outerHTML")
            with open("/tmp/jhp_diag/extension_final.html", "w") as f:
                f.write(html or "")
            for k in ("actions", "results"):
                for n in (1, 2, 3):
                    try:
                        v = driver.execute_script(
                            f"return sessionStorage.getItem('jhp-{k}-{n}');"
                        )
                        if v:
                            with open(f"/tmp/jhp_diag/jhp_{k}_{n}.json", "w") as f:
                                f.write(v)
                    except Exception:
                        pass
            logger.info("artifacts /tmp/jhp_diag/extension_final.{png,html} + jhp_{actions,results}_*.json")
        except Exception as e:
            logger.warning("artifact save failed: {}", e)
        logger.info("Leaving browser open 30s for inspection")
        time.sleep(30)
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else (
        "https://ats.rippling.com/en-GB/aalyria-careers/jobs/"
        "d8ac14cd-efdc-4688-8c7c-821685da9b2c?source=LinkedIn"
    )
    run(url)
