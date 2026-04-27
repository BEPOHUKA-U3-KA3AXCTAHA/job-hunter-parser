"""Pattern-based email guesser. Free, no API.

For each DM with a known name + company domain we produce ONE most-likely
address (`contacts["email_guess"]`) plus the alternates kept separately
(`contacts["email_alts"]`) for a verifier to whittle down later.

Why one primary: outreach should hit a single inbox, not blast 5 patterns and
eat the bounce-rate hit on the sender's domain reputation.

Tech-org pattern frequency (Hunter.io industry stats):
  firstname.lastname@   ~45%   ← our default primary
  firstname@            ~25%   (smaller startups)
  flastname@            ~15%   (banks / legacy enterprise)
  firstnamelastname@    ~10%
  firstname_lastname@   ~5%

Use a verifier downstream (Hunter.io / MX probe) before actually sending.
"""
from __future__ import annotations

import re

from src.people.models import DecisionMaker
from src.people.ports import ContactEnrichment

_NAME_CLEAN = re.compile(r"[^a-z\-]")


def primary_guess(full_name: str, company_domain: str) -> str | None:
    """Single most-likely email for a tech org. None when we can't form one."""
    domain = _normalize_domain(company_domain)
    if not domain:
        return None
    first, last = _split_name(full_name)
    if not first:
        return None
    if last:
        return f"{first}.{last}@{domain}"
    return f"{first}@{domain}"


def alternate_guesses(full_name: str, company_domain: str) -> list[str]:
    """Backup patterns ordered by tech-industry frequency. Excludes the primary."""
    domain = _normalize_domain(company_domain)
    if not domain:
        return []
    first, last = _split_name(full_name)
    if not first or not last:
        return []
    return [
        f"{first}@{domain}",
        f"{first[0]}{last}@{domain}",
        f"{first}{last}@{domain}",
        f"{first}_{last}@{domain}",
    ]


class EmailPatternGuesser(ContactEnrichment):
    source_name = "email_pattern"

    async def enrich(self, decision_maker: DecisionMaker, company_domain: str) -> DecisionMaker:
        # If the DM already has a verified email, never overwrite it
        if (decision_maker.contacts.get("email") or "").count("@") == 1:
            return decision_maker

        primary = primary_guess(decision_maker.full_name, company_domain)
        if not primary:
            return decision_maker

        decision_maker.contacts["email_guess"] = primary
        alts = alternate_guesses(decision_maker.full_name, company_domain)
        if alts:
            decision_maker.contacts["email_alts"] = ", ".join(alts)
        return decision_maker


def _split_name(full: str) -> tuple[str, str | None]:
    parts = [p for p in re.split(r"\s+", (full or "").strip()) if p]
    if not parts:
        return "", None
    first = _NAME_CLEAN.sub("", parts[0].lower())
    last = _NAME_CLEAN.sub("", parts[-1].lower()) if len(parts) > 1 else None
    return first, last or None


def _normalize_domain(d: str) -> str:
    d = d.strip().lower()
    d = d.removeprefix("https://").removeprefix("http://").removeprefix("www.")
    d = d.split("/")[0].split("?")[0]
    return d
