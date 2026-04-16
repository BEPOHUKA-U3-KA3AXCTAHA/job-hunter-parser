"""RustJobs.dev scraper - JS-rendered, needs Playwright."""
from __future__ import annotations

from collections.abc import AsyncIterator

from loguru import logger
from playwright.async_api import async_playwright

from src.companies.models import Company, JobPosting
from src.companies.ports import CompanySource
from src.shared import Seniority, TechStack

_URL = "https://rustjobs.dev/locations/remote"


class RustJobsScraper(CompanySource):
    source_name = "rustjobs"

    async def fetch_companies(
        self,
        tech_stack_filter: list[str] | None = None,
        limit: int = 100,
    ) -> AsyncIterator[Company]:
        postings = await self._scrape_page()
        seen: dict[str, Company] = {}

        for p in postings:
            name = p["company"]
            if not name or name in seen:
                continue

            tags = p.get("tags", [])
            if tech_stack_filter:
                if not any(t.lower() in [x.lower() for x in tags] for t in tech_stack_filter):
                    continue

            seen[name] = Company(
                name=name,
                tech_stack=TechStack(frozenset(t.lower() for t in tags)),
                is_hiring=True,
                source="rustjobs",
                source_url=p.get("link"),
                location=p.get("location"),
            )
            if len(seen) >= limit:
                break

        logger.info("RustJobs: found {} companies", len(seen))
        for company in seen.values():
            yield company

    async def fetch_job_postings(
        self,
        company_id: str | None = None,
        tech_stack_filter: list[str] | None = None,
        limit: int = 100,
    ) -> AsyncIterator[JobPosting]:
        postings = await self._scrape_page()
        count = 0

        for p in postings:
            tags = p.get("tags", [])
            if tech_stack_filter:
                if not any(t.lower() in [x.lower() for x in tags] for t in tech_stack_filter):
                    continue

            yield JobPosting(
                company_id=0,
                title=p["title"],
                tech_stack=TechStack(frozenset(t.lower() for t in tags)),
                seniority=Seniority.from_text(p["title"]),
                is_remote=True,
                location=p.get("location"),
                source_url=p.get("link"),
            )
            count += 1
            if count >= limit:
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
                        // RustJobs uses card-like elements for job listings
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
