"""Scrape YC company directory and profile pages."""

import os
import sys
import time
from pathlib import Path

# Ensure project root is on path when run as python src/scrape_yc.py
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from src.models import CompanyRaw
from src.utils import ensure_dirs, log, write_csv

MAX_COMPANIES = int(os.environ.get("MAX_COMPANIES", "500"))
HEADLESS = os.environ.get("HEADLESS", "true").lower() in ("true", "1", "yes")
RATE_LIMIT_MS = 500
BASE_URL = "https://www.ycombinator.com"
DIRECTORY_URL = f"{BASE_URL}/companies"


def _extract_text(page, selector: str, default: str = "", root=None) -> str:
    """Safely extract text from element; return default if missing."""
    try:
        el = (root or page).query_selector(selector) if root else page.query_selector(selector)
        return (el.inner_text().strip() if el else default) or default
    except Exception:
        return default


def _extract_href(page, selector: str, default: str = "", root=None) -> str:
    """Safely extract href from element; return default if missing."""
    try:
        el = (root or page).query_selector(selector) if root else page.query_selector(selector)
        if not el:
            return default
        href = el.get_attribute("href") or ""
        return href.strip() or default
    except Exception:
        return default


def _collect_profile_urls(page, max_count: int) -> list[str]:
    """Scroll directory page and collect company profile URLs."""
    seen: set[str] = set()
    last_count = 0
    stable_count = 0

    while len(seen) < max_count and stable_count < 5:
        # Find links to company profiles (e.g. /companies/acme, not /companies)
        links = page.query_selector_all('a[href^="/companies/"]')
        for link in links:
            href = link.get_attribute("href") or ""
            if not href or href == "/companies" or href == "/companies/":
                continue
            full = f"{BASE_URL}{href}" if href.startswith("/") else href
            if full not in seen:
                seen.add(full)
                if len(seen) >= max_count:
                    break

        if len(seen) == last_count:
            stable_count += 1
        else:
            stable_count = 0
        last_count = len(seen)

        if len(seen) >= max_count:
            break

        # Scroll to load more
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(0.3)

    return list(seen)[:max_count]


def _scrape_profile(page, url: str) -> CompanyRaw | None:
    """Scrape a single company profile page."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeout:
        log(f"Timeout loading {url}")
        return None
    except Exception as e:
        log(f"Failed to load {url}: {e}")
        return None

    # Scope to main content to avoid header/footer
    main_el = page.query_selector("main") or page.query_selector("[role='main']") or page

    # Company name: prefer main content, exclude footer-like text
    name = (
        _extract_text(page, "h1", root=main_el)
        or _extract_text(page, "[data-testid='company-name']")
        or _extract_text(page, ".company-name")
    )
    if name and name.lower() in ("footer", "header", "navigation"):
        name = _extract_text(page, "h2", root=main_el) or ""
    if not name:
        # Fallback: slug from URL (e.g. /companies/doordash -> DoorDash)
        slug = url.rstrip("/").split("/")[-1]
        if slug and slug != "companies":
            name = slug.replace("-", " ").title()

    location = (
        _extract_text(page, "[data-testid='company-location']", root=main_el)
        or _extract_text(page, ".company-location", root=main_el)
        or _extract_text(page, "a[href*='locations']", root=main_el)
    )

    batch = (
        _extract_text(page, "[data-testid='company-batch']", root=main_el)
        or _extract_text(page, ".company-batch", root=main_el)
        or _extract_text(page, "a[href*='batch']", root=main_el)
    )

    desc = (
        _extract_text(page, "[data-testid='company-description']", root=main_el)
        or _extract_text(page, ".company-description", root=main_el)
        or _extract_text(page, "p.profile-description", root=main_el)
        or _extract_text(page, ".prose p", root=main_el)
    )

    # Website: prefer "Website" link in main, exclude startupschool/yc
    website = ""
    try:
        for sel in ["a:has-text('Website')", "a:has-text('Visit')", "a:has-text('website')"]:
            el = main_el.query_selector(sel)
            if el:
                href = (el.get_attribute("href") or "").strip()
                if href and "ycombinator" not in href.lower() and "startupschool" not in href.lower():
                    website = href
                    break
        if not website:
            for link in main_el.query_selector_all("a[href^='http']"):
                href = (link.get_attribute("href") or "").strip()
                if href and "ycombinator" not in href.lower() and "startupschool" not in href.lower():
                    website = href
                    break
    except Exception:
        pass

    return CompanyRaw(
        company_name=name or "",
        yc_batch=batch or "",
        location=location or "",
        short_description=desc or "",
        yc_company_url=url,
        website_url=website or "",
    )


def main() -> None:
    """Scrape YC directory and profile pages, write raw CSV."""
    ensure_dirs()
    log(f"Starting scrape: MAX_COMPANIES={MAX_COMPANIES}, HEADLESS={HEADLESS}")

    rows: list[dict] = []
    failed: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = context.new_page()

        try:
            log("Loading directory...")
            page.goto(DIRECTORY_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)

            urls = _collect_profile_urls(page, MAX_COMPANIES)
            log(f"Collected {len(urls)} profile URLs")

            for i, url in enumerate(urls):
                log(f"[{i + 1}/{len(urls)}] {url}")
                company = _scrape_profile(page, url)
                if company:
                    rows.append(company.to_dict())
                else:
                    failed.append(url)
                time.sleep(RATE_LIMIT_MS / 1000.0)

        finally:
            browser.close()

    out_path = Path(__file__).resolve().parent.parent / "data" / "raw" / "yc_companies_raw.csv"
    write_csv(out_path, rows)
    log(f"Wrote {len(rows)} rows to {out_path}")

    if failed:
        log(f"Failed profiles ({len(failed)}): {failed[:5]}{'...' if len(failed) > 5 else ''}")


if __name__ == "__main__":
    main()
