"""Pattern-based email guesser. Free, no API.

Given a DM's full name + company domain, generates the most common email patterns
used by tech companies. Stored under contacts['email_guesses'] (comma-separated)
so it stays distinct from a verified contacts['email'].

Use a verifier (Hunter.io free tier, MX probe, etc.) downstream to narrow the list.
"""
from __future__ import annotations

import re

from src.people.models import DecisionMaker
from src.people.ports import ContactEnrichment

_NAME_CLEAN = re.compile(r"[^a-z\-]")


class EmailPatternGuesser(ContactEnrichment):
    source_name = "email_pattern"

    async def enrich(self, decision_maker: DecisionMaker, company_domain: str) -> DecisionMaker:
        if not company_domain or "@" in (decision_maker.contacts.get("email") or ""):
            return decision_maker  # already verified, skip

        domain = _normalize_domain(company_domain)
        if not domain:
            return decision_maker

        first, last = _split_name(decision_maker.full_name)
        if not first:
            return decision_maker

        guesses: list[str] = []
        if last:
            guesses = [
                f"{first}@{domain}",
                f"{first}.{last}@{domain}",
                f"{first[0]}{last}@{domain}",
                f"{first}{last}@{domain}",
                f"{first}_{last}@{domain}",
            ]
        else:
            guesses = [f"{first}@{domain}"]

        # Dedup, preserve order
        seen: set[str] = set()
        uniq = [g for g in guesses if not (g in seen or seen.add(g))]

        decision_maker.contacts["email_guesses"] = ", ".join(uniq)
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
