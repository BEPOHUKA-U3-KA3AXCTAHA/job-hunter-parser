"""RustJobs.dev scraper - JS-rendered, needs Playwright."""
from __future__ import annotations

from collections.abc import AsyncIterator

from loguru import logger
from playwright.async_api import async_playwright

from app.modules.companies.models import Company, JobPosting
from app.modules.companies.ports import CompanySource
from app.modules.companies import SearchCriteria, Seniority, TechStack

_URL = "https://rustjobs.dev/locations/remote"


class RustJobsScraper(CompanySource):
    source_name = "rustjobs"

    async def fetch_companies(self, criteria: SearchCriteria) -> AsyncIterator[Company]:
        postings = await self._scrape_page()
        seen: dict[str, Company] = {}

        for p in postings:
            name = p["company"]
            if not name or name in seen:
                continue

            if not criteria.matches_title(p["title"]):
                continue

            tags = p.get("tags", [])
            seen[name] = Company(
                name=name,
                tech_stack=TechStack(frozenset(t.lower() for t in tags)),
                is_hiring=True,
                source="rustjobs",
                source_url=p.get("link"),
                location=p.get("location"),
            )
            if len(seen) >= criteria.limit_per_source:
                break

        logger.info("RustJobs: found {} companies", len(seen))
        for company in seen.values():
            yield company

    async def fetch_job_postings(self, criteria: SearchCriteria) -> AsyncIterator[JobPosting]:
        postings = await self._scrape_page()
        count = 0

        for p in postings:
            if not criteria.matches_title(p["title"]):
                continue

            tags = p.get("tags", [])
            yield JobPosting(
                title=p["title"],
                company_name=p.get("company", ""),
                tech_stack=TechStack(frozenset(t.lower() for t in tags)),
                seniority=Seniority.from_text(p["title"]),
                is_remote=True,
                location=p.get("location"),
                source="rustjobs",
                source_url=p.get("link"),
            )
            count += 1
            if count >= criteria.limit_per_source:
                break

        logger.info("RustJobs: yielded {} postings", count)

    async def _scrape_page(self) -> list[dict]:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()

            try:
                await page.goto(_URL, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(2000)

                postings = await page.evaluate("""
                    () => {
                        const results = [];
                        const cards = document.querySelectorAll('a[href*="/jobs/"], a[href*="featured-jobs"]');
                        for (const card of cards) {
                            const title = card.querySelector('h3, h2, [class*="title"]');
                            const company = card.querySelector('[class*="company"], [class*="org"]');
                            const location = card.querySelector('[class*="location"]');
                            const tags = card.querySelectorAll('[class*="tag"], [class*="badge"], [class*="skill"]');
                            if (title) {
                                results.push({
                                    title: title.innerText.trim(),
                                    company: company ? company.innerText.trim() : '',
                                    location: location ? location.innerText.trim() : 'Remote',
                                    link: card.href || '',
                                    tags: Array.from(tags).map(t => t.innerText.trim()).filter(Boolean),
                                });
                            }
                        }
                        return results;
                    }
                """)
            finally:
                await browser.close()

        logger.info("RustJobs: scraped {} raw postings", len(postings))
        return postings
