"""Scraper for meinestadt.de Branchenbuch — security services leads."""
from __future__ import annotations

import re
import time
import urllib.parse
import urllib.robotparser
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from crm.importer import _dedup_key, _existing_keys
from crm.models import JobStatus, Lead, LeadStatus, ScrapeJob

SCRAPER_UA = "OutreachCRM/1.0 (compliant research bot; contact pashavt@gmail.com)"
DEFAULT_DELAY = 2.5       # seconds between requests
REQUEST_TIMEOUT = 15      # seconds
CATEGORY = "Sicherheitsdienste"
MAX_PAGES = 10            # safety cap on pagination per city

# Default keyword filter: only save companies whose name contains one of these
DEFAULT_NAME_KEYWORDS = ["security", "sicherheit", "secur", "wach"]

LISTING_URL = (
    "https://branchenbuch.meinestadt.de/{city}/brazl/100-19055-19077-71497"
)

# External link patterns to exclude from website extraction
_EXCLUDE_DOMAINS = re.compile(
    r"(meinestadt|google\.|facebook\.|instagram\.|twitter\.|linkedin\.|app\.link|branch\.io)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------

def _make_http_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = SCRAPER_UA
    s.headers["Accept-Language"] = "de-DE,de;q=0.9,en;q=0.8"
    s.headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    return s


# ---------------------------------------------------------------------------
# robots.txt check
# ---------------------------------------------------------------------------

def _robots_allowed(target_url: str) -> bool:
    """Return True if the target URL is allowed by robots.txt, or if check fails."""
    parsed = urllib.parse.urlparse(target_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(robots_url)
    try:
        rp.read()
        return rp.can_fetch(SCRAPER_UA, target_url)
    except Exception:
        return True  # assume allowed when robots.txt is unreachable


# ---------------------------------------------------------------------------
# Parser functions (network-free, testable with static HTML)
# ---------------------------------------------------------------------------

def parse_listing_page(html: str, city: str) -> list[dict]:
    """Parse a Branchenbuch listing page; return list of entry dicts.

    Keys: company_name, city, address, detail_url, category

    Selectors confirmed against meinestadt.de branchenbuch (2025):
      items   : li.m-resultListEntries
      name    : h3.a-headline
      url     : a[href*='/company/']
      address : div.a-resultListMetainfoItem__text (first occurrence)
    """
    soup = BeautifulSoup(html, "html.parser")
    entries: list[dict] = []

    # Primary: meinestadt.de confirmed structure
    items = soup.select("li.m-resultListEntries")

    # Fallback selector ladder for future site changes
    if not items:
        items = (
            soup.select("[data-cy='business-results-item']")
            or soup.select("[data-testid='search-result-item']")
            or soup.select(".search-result-item")
            or soup.select("article.result")
            or soup.select("article")
        )

    for item in items:
        # Company name
        name_el = (
            item.select_one("h3.a-headline")
            or item.select_one("h2.a-headline")
            or item.select_one("h3")
            or item.select_one("h2")
        )
        if not name_el:
            continue
        company_name = name_el.get_text(strip=True)
        if not company_name:
            continue

        # Detail URL: prefer /company/ link, fall back to first link
        detail_link = item.select_one("a[href*='/company/']")
        if not detail_link:
            detail_link = item.find("a", href=True)
        detail_url = ""
        if detail_link:
            href = detail_link["href"]
            if href and not href.startswith("http"):
                href = "https://branchenbuch.meinestadt.de" + href
            detail_url = href

        # Address: first metainfo item text (address, not distance)
        addr_el = item.select_one("div.a-resultListMetainfoItem__text")
        if not addr_el:
            addr_el = (
                item.select_one("address")
                or item.select_one(".address")
                or item.select_one(".result-address")
            )
        address = addr_el.get_text(strip=True) if addr_el else ""

        entries.append({
            "company_name": company_name,
            "city": city,
            "address": address,
            "detail_url": detail_url,
            "category": CATEGORY,
        })

    return entries


def parse_detail_page(html: str) -> dict:
    """Extract email, website, phone from a company detail page.

    Only uses data visible in the HTML — no JavaScript evaluation.
    Keys: email, website, phone
    """
    soup = BeautifulSoup(html, "html.parser")

    # Email: public mailto: links only
    email = ""
    mailto_el = soup.select_one("a[href^='mailto:']")
    if mailto_el:
        raw = mailto_el.get("href", "").replace("mailto:", "").strip()
        email = raw.split("?")[0].strip()

    # Website: first external link that isn't meinestadt or social media
    website = ""
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if (
            href.startswith("http")
            and not _EXCLUDE_DOMAINS.search(href)
        ):
            website = href
            break

    # Phone: tel: link first, then visible text in known selectors
    phone = ""
    tel_el = soup.select_one("a[href^='tel:']")
    if tel_el:
        phone = tel_el["href"].replace("tel:", "").replace("%20", " ").strip()
    else:
        for sel in (".phone", "[data-testid='phone']", ".contact-phone", ".phonenumber"):
            el = soup.select_one(sel)
            if el:
                phone = el.get_text(strip=True)
                break

    return {"email": email, "website": website, "phone": phone}


def find_next_page_url(html: str) -> Optional[str]:
    """Return the URL of the next pagination page, or None if last page.

    meinestadt.de uses 'vor' (German for 'forward') as the next-page link text.
    Fallbacks cover rel=next and other common patterns.
    """
    soup = BeautifulSoup(html, "html.parser")

    # meinestadt.de confirmed: <a href="?page=N">vor</a>
    for a in soup.select("a[href]"):
        if a.get_text(strip=True) == "vor":
            href = a["href"]
            if not href.startswith("http"):
                href = "https://branchenbuch.meinestadt.de" + href
            return href

    # Generic fallbacks
    next_link = (
        soup.select_one("a[rel='next']")
        or soup.select_one(".pagination a.next")
        or soup.select_one("[data-testid='pagination-next']")
    )
    if not next_link:
        for a in soup.select("a"):
            txt = a.get_text(strip=True)
            if txt in ("Weiter", "Nächste Seite", ">", "»"):
                next_link = a
                break

    if not next_link:
        return None
    href = next_link.get("href", "")
    if not href:
        return None
    if not href.startswith("http"):
        href = "https://branchenbuch.meinestadt.de" + href
    return href


# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------

def _log(job: ScrapeJob, db_session: Session, message: str) -> None:
    ts = datetime.utcnow().strftime("%H:%M:%S")
    job.log_text = (job.log_text or "") + f"[{ts}] {message}\n"
    db_session.commit()


# ---------------------------------------------------------------------------
# City-level scraper
# ---------------------------------------------------------------------------

def _matches_keywords(name: str, keywords: list[str]) -> bool:
    """Return True if the company name contains at least one keyword (case-insensitive)."""
    lower = name.lower()
    return any(kw.lower() in lower for kw in keywords)


def scrape_city(
    city: str,
    db_session: Session,
    job: ScrapeJob,
    *,
    limit: Optional[int] = None,
    delay: float = DEFAULT_DELAY,
    http: Optional[requests.Session] = None,
    name_keywords: Optional[list[str]] = None,
) -> tuple[int, int, int, int]:
    """Scrape one city. Returns (found, imported, skipped, failed).

    name_keywords: if set, only save leads whose company name contains at least
    one keyword. Pass [] to disable filtering.
    """
    if name_keywords is None:
        name_keywords = DEFAULT_NAME_KEYWORDS

    own_http = http is None
    if own_http:
        http = _make_http_session()

    found = imported = skipped = failed = 0
    db_keys = _existing_keys(db_session)
    seen_keys: set[tuple[str, str]] = set()
    url: Optional[str] = LISTING_URL.format(city=city)

    try:
        for page_num in range(1, MAX_PAGES + 1):
            _log(job, db_session, f"{city}: page {page_num} — {url}")

            try:
                resp = http.get(url, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
            except requests.RequestException as exc:
                _log(job, db_session, f"{city}: HTTP error on page {page_num}: {exc}")
                break

            html = resp.text
            entries = parse_listing_page(html, city)
            _log(job, db_session, f"{city}: parsed {len(entries)} entries on page {page_num}")

            if not entries:
                _log(job, db_session, f"{city}: no entries found — check selectors or page structure")
                break

            for entry in entries:
                if limit is not None and (imported + skipped) >= limit:
                    break

                found += 1

                # Keyword filter
                if name_keywords and not _matches_keywords(entry["company_name"], name_keywords):
                    skipped += 1
                    _log(job, db_session, f"  filtered: {entry['company_name']}")
                    continue
                detail_url = entry.get("detail_url", "")
                email = website = phone = ""

                if detail_url:
                    time.sleep(delay)
                    try:
                        dresp = http.get(detail_url, timeout=REQUEST_TIMEOUT)
                        dresp.raise_for_status()
                        detail = parse_detail_page(dresp.text)
                        email = detail["email"]
                        website = detail["website"]
                        phone = detail["phone"]
                        _log(
                            job, db_session,
                            f"  {entry['company_name']}: email={email or '-'} "
                            f"web={website or '-'} phone={phone or '-'}",
                        )
                    except requests.RequestException as exc:
                        _log(job, db_session, f"  {entry['company_name']}: detail error: {exc}")
                        failed += 1

                row = {
                    "company_name": entry["company_name"],
                    "city": city,
                    "email": email,
                    "website": website,
                }
                key = _dedup_key(row)
                if key in db_keys or key in seen_keys:
                    skipped += 1
                    _log(job, db_session, f"  skipped (dup): {entry['company_name']}")
                    continue

                seen_keys.add(key)
                db_keys.add(key)
                db_session.add(Lead(
                    company_name=entry["company_name"],
                    city=city,
                    category=entry.get("category", CATEGORY),
                    website=website or None,
                    email=email or None,
                    phone=phone or None,
                    source_url=detail_url or url,
                    status=LeadStatus.new,
                ))
                db_session.commit()
                imported += 1

            if limit is not None and (imported + skipped) >= limit:
                _log(job, db_session, f"{city}: limit {limit} reached")
                break

            next_url = find_next_page_url(html)
            if not next_url:
                break
            url = next_url
            time.sleep(delay)

    finally:
        if own_http:
            http.close()

    return found, imported, skipped, failed


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------

def scrape_meinestadt_security(
    cities: list[str],
    db_session: Session,
    job: ScrapeJob,
    *,
    limit_per_city: Optional[int] = None,
    delay: float = DEFAULT_DELAY,
    name_keywords: Optional[list[str]] = None,
) -> None:
    """Run scrape across all cities, updating job record throughout."""
    if name_keywords is None:
        name_keywords = DEFAULT_NAME_KEYWORDS

    job.status = JobStatus.running
    job.cities_json = cities
    job.log_text = ""
    job.total_found = 0
    job.total_imported = 0
    job.total_skipped = 0
    job.total_failed = 0
    db_session.commit()

    kw_display = ", ".join(name_keywords) if name_keywords else "(none — all companies)"
    _log(job, db_session,
         f"Start: cities={cities} limit_per_city={limit_per_city} "
         f"delay={delay}s keywords=[{kw_display}]")

    # robots.txt check
    sample_url = LISTING_URL.format(city=cities[0]) if cities else LISTING_URL.format(city="dresden")
    allowed = _robots_allowed(sample_url)
    _log(job, db_session, f"robots.txt: {'allowed' if allowed else 'DISALLOWED — aborting'}")
    if not allowed:
        job.status = JobStatus.failed
        db_session.commit()
        return

    try:
        http = _make_http_session()
        for city in cities:
            job.current_city = city
            db_session.commit()
            _log(job, db_session, f"=== City: {city} ===")

            found, imp, skipped, failed = scrape_city(
                city, db_session, job,
                limit=limit_per_city,
                delay=delay,
                name_keywords=name_keywords,
                http=http,
            )

            job.total_found = (job.total_found or 0) + found
            job.total_imported = (job.total_imported or 0) + imp
            job.total_skipped = (job.total_skipped or 0) + skipped
            job.total_failed = (job.total_failed or 0) + failed
            db_session.commit()
            _log(
                job, db_session,
                f"=== {city} done: found={found} imported={imp} skipped={skipped} failed={failed} ===",
            )

        http.close()
        job.status = JobStatus.finished
        job.current_city = None
        db_session.commit()
        _log(
            job, db_session,
            f"Finished. Total: found={job.total_found} imported={job.total_imported} "
            f"skipped={job.total_skipped} failed={job.total_failed}",
        )

    except Exception as exc:
        job.status = JobStatus.failed
        db_session.commit()
        _log(job, db_session, f"FATAL: {exc}")
        raise
