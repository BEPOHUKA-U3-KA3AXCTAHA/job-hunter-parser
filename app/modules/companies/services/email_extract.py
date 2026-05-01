"""Extract a real apply-to email from a job-posting blob (description / detail HTML).

Returns the single best email when one is present, None otherwise. The filter
is conservative on purpose: we only want addresses we can actually mail.
Wrong matches (sidebar recommendations, tracking pixels, third-party SDK
domains) are far more harmful than missing one.
"""
from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# domains that show up in scraped HTML but are never apply emails
_DOMAIN_BLACKLIST = {
    "example.com", "example.org", "sentry.io", "intercom.io", "intercom.com",
    "google.com", "googleapis.com", "wixstatic.com", "newrelic.com",
    "cloudflare.com", "schema.org", "users.noreply.github.com",
    "noreply.github.com", "remoteok.com", "remote-ok.com", "web3.career",
    "linkedin.com", "rustjobs.dev", "amazonaws.com",
}

# usernames that mean "automated bounce, do not reply"
_LOCAL_BLACKLIST_PREFIX = ("noreply", "no-reply", "donotreply", "do-not-reply",
                           "mailer-daemon", "postmaster", "abuse", "bounces")

# username prefixes that strongly indicate this IS an apply address
_GOOD_LOCAL_PREFIX = ("careers", "career", "jobs", "job", "hire", "hiring",
                      "recruit", "recruiting", "recruiter", "hr", "people",
                      "talent", "join", "apply", "work", "team")

# image / asset extensions that match the regex but are obviously not emails
_BAD_TLDS = (".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp", ".ico", ".css", ".js")


def extract_apply_email(text: str, company_name: str | None = None) -> str | None:
    """Pull the most-likely apply-to email from arbitrary text.

    Preference order:
      1. local-part is careers@/jobs@/hr@/... AND domain matches the company
      2. ANY @<companydomain> address (e.g. john@kraken.com from a description)
      3. None — we deliberately don't return cross-company addresses, since
         posting pages often leak emails from sidebar recommendations.
    """
    if not text:
        return None

    candidates = _EMAIL_RE.findall(text)
    if not candidates:
        return None

    company_slug = (company_name or "").lower().replace(" ", "").replace("-", "").replace(".", "")

    cleaned: list[str] = []
    for raw in candidates:
        e = raw.strip().lower().rstrip(".,;:)")
        if any(e.endswith(ext) for ext in _BAD_TLDS):
            continue
        local, _, domain = e.partition("@")
        if not domain or domain in _DOMAIN_BLACKLIST:
            continue
        if any(local.startswith(p) for p in _LOCAL_BLACKLIST_PREFIX):
            continue
        cleaned.append(e)
    if not cleaned:
        return None

    def domain_matches(addr: str) -> bool:
        if not company_slug:
            return False
        d = addr.partition("@")[2]
        # strip TLD-like suffix and compare loose substring
        d_core = d.split(".")[0].replace("-", "")
        return company_slug in d_core or d_core in company_slug

    def good_local(addr: str) -> bool:
        return addr.partition("@")[0].startswith(_GOOD_LOCAL_PREFIX)

    # Tier 1: good local + domain matches the company
    for e in cleaned:
        if good_local(e) and domain_matches(e):
            return e
    # Tier 2: any address on the company's own domain
    for e in cleaned:
        if domain_matches(e):
            return e
    return None
