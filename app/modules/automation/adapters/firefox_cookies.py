"""Read cookies from the user's real Firefox profile without locking it.

Firefox holds an exclusive lock on its `cookies.sqlite` while running, so we
copy the file to /tmp first and read the snapshot. This means we get cookies
as of the last Firefox flush — usually within seconds. For long-lived auth
cookies (LinkedIn `liap`, `bcookie`) this is fine.

Returns a list of cookie dicts ready to be injected into a Playwright /
Camoufox browser context.
"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
from configparser import ConfigParser
from pathlib import Path

from loguru import logger

FIREFOX_DIR = Path.home() / ".mozilla" / "firefox"


def find_default_profile() -> Path:
    """Read profiles.ini and return the path to the active profile.

    Resolution order matches Firefox's own logic:
      1. [Install*] section's Default= key (this is what Firefox actually launches)
      2. [Profile*] section with Default=1
      3. Fallback: prefer *.default-esr (ESR build), then *.default

    Returns Path with cookies.sqlite present — falls through if the resolved
    profile is missing the file (e.g. brand-new profile never opened).
    """
    ini = FIREFOX_DIR / "profiles.ini"
    if not ini.exists():
        raise FileNotFoundError(f"Firefox profiles.ini not found at {ini}")

    cp = ConfigParser()
    cp.read(ini)

    candidates: list[Path] = []

    # 1. [Install*] sections — Firefox's per-install default
    for section in cp.sections():
        if not section.startswith("Install"):
            continue
        path = cp.get(section, "Default", fallback=None)
        if path:
            candidates.append(FIREFOX_DIR / path)

    # 2. [Profile*] with Default=1
    for section in cp.sections():
        if not section.startswith("Profile"):
            continue
        if cp.get(section, "Default", fallback="0") == "1":
            path = cp.get(section, "Path")
            is_relative = cp.get(section, "IsRelative", fallback="1") == "1"
            candidates.append(FIREFOX_DIR / path if is_relative else Path(path))

    # 3. Fallback: scan directories
    for suffix in (".default-esr", ".default"):
        for d in FIREFOX_DIR.iterdir():
            if d.is_dir() and d.name.endswith(suffix):
                candidates.append(d)

    # Pick the first candidate that has cookies.sqlite
    for c in candidates:
        if (c / "cookies.sqlite").exists():
            return c

    raise FileNotFoundError(
        f"No Firefox profile with cookies.sqlite found. Checked: {[str(c) for c in candidates]}"
    )


def export_cookies_for_domain(
    domain_substr: str,
    profile_dir: Path | None = None,
) -> list[dict]:
    """Pull cookies whose host contains `domain_substr` (e.g. "linkedin").

    Returns Playwright-shaped cookie dicts:
        {name, value, domain, path, expires, httpOnly, secure, sameSite}
    """
    profile = profile_dir or find_default_profile()
    src = profile / "cookies.sqlite"
    if not src.exists():
        raise FileNotFoundError(f"cookies.sqlite missing under {profile}")

    # Copy to /tmp to bypass Firefox's exclusive lock
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tf:
        snapshot = Path(tf.name)
    shutil.copy2(src, snapshot)

    try:
        c = sqlite3.connect(snapshot)
        rows = c.execute(
            """SELECT host, name, value, path, expiry, isHttpOnly, isSecure, sameSite
               FROM moz_cookies
               WHERE host LIKE ?""",
            (f"%{domain_substr}%",),
        ).fetchall()
    finally:
        snapshot.unlink(missing_ok=True)

    out: list[dict] = []
    for host, name, value, path, expiry, is_http_only, is_secure, same_site in rows:
        out.append({
            "name": name,
            "value": value,
            "domain": host,                # Firefox already prefixes with . for cross-subdomain
            "path": path or "/",
            "expires": int(expiry) if expiry else -1,
            "httpOnly": bool(is_http_only),
            "secure": bool(is_secure),
            # Firefox sameSite: 0=None, 1=Lax, 2=Strict; Playwright wants string
            "sameSite": {0: "None", 1: "Lax", 2: "Strict"}.get(same_site, "Lax"),
        })

    logger.info("Firefox cookies: exported {} for domain~{!r} from {}", len(out), domain_substr, profile.name)
    return out


if __name__ == "__main__":
    # Quick smoke test
    cookies = export_cookies_for_domain("linkedin")
    print(f"got {len(cookies)} LinkedIn cookies")
    for c in cookies[:5]:
        print(f"  {c['domain']:30} {c['name']:30} {c['value'][:20]}…")
