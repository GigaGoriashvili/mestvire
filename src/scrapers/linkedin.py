"""
LinkedIn scraper module using unofficial public guest endpoints.
Fetches job listings and full descriptions without login or authentication.
Includes resilient multi-tier fallback selectors and graceful error handling.
"""

import re
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from src.config import (
    LINKEDIN_BASE_URL,
    LINKEDIN_DEFAULT_GEO_ID,
    LINKEDIN_DEFAULT_KEYWORD,
    LINKEDIN_DEFAULT_LOCATION,
    LINKEDIN_DEFAULT_TIME_RANGE,
    LINKEDIN_GUEST_DETAIL_URL,
    LINKEDIN_GUEST_SEARCH_URL,
    USER_AGENT,
    logger,
)
from src.utils import clean_text


def extract_job_id(element_urn: Optional[str], href: Optional[str]) -> Optional[str]:
    """Extract numeric job ID from URN or link attribute."""
    if element_urn:
        match = re.search(r"jobPosting:(\d+)", element_urn)
        if match:
            return match.group(1)

    if href:
        match = re.search(r"(?:view/[^/]*?-|currentJobId=|jobId=)(\d{8,})", href)
        if match:
            return match.group(1)
        match = re.search(r"/(\d{8,})", href)
        if match:
            return match.group(1)

    return None


def scrape_linkedin_jobs(
    session: requests.Session,
    keywords: str = LINKEDIN_DEFAULT_KEYWORD,
    geo_id: str = LINKEDIN_DEFAULT_GEO_ID,
    location: str = LINKEDIN_DEFAULT_LOCATION,
    time_range: str = LINKEDIN_DEFAULT_TIME_RANGE,
    start: int = 0,
    max_jobs: int = 50,
) -> List[Dict[str, str]]:
    """
    Scrape LinkedIn job listings via public guest endpoint.

    Parameters:
    - keywords: Search keyword (default: "data")
    - geo_id: Country or region geoId (default: "102974008" for Georgia)
    - location: Location text filter (default: "Georgia")
    - time_range: Past time window (default: "r86400" for past 24 hours in seconds)
    - start: Pagination offset (default: 0)
    """
    params = {
        "keywords": keywords,
        "location": location,
        "geoId": geo_id,
        "f_TPR": time_range,
        "start": start,
    }
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        response = session.get(
            LINKEDIN_GUEST_SEARCH_URL,
            params=params,
            headers=headers,
            timeout=15,
        )
        response.encoding = "utf-8"

        if response.status_code == 429:
            logger.warning("LinkedIn rate limited (429) on guest search endpoint.")
            return []

        response.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"Failed to fetch LinkedIn job listings from guest endpoint: {e}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    cards = soup.select("li")
    if not cards:
        # Fallback card selector
        cards = soup.select(".base-card, .job-search-card, div[data-entity-urn]")

    jobs = []
    seen_ids = set()

    for card in cards:
        # 1. Extract Job ID
        urn = card.get("data-entity-urn")
        if not urn:
            urn_elem = card.find(attrs={"data-entity-urn": True})
            if urn_elem:
                urn = urn_elem.get("data-entity-urn")

        link_elem = (
            card.select_one("a.base-card__full-link")
            or card.select_one("a[data-tracking-control-name*='job']")
            or card.find("a", href=re.compile(r"/jobs/view/"))
            or card.find("a", href=True)
        )
        href = link_elem["href"] if link_elem and link_elem.has_attr("href") else None

        job_id = extract_job_id(urn, href)
        if not job_id or job_id in seen_ids:
            continue
        seen_ids.add(job_id)

        # 2. Extract Title (with tiered fallback selectors)
        title_elem = (
            card.select_one(".base-search-card__title")
            or card.select_one(".job-search-card__title")
            or card.find(["h3", "h2"])
            or link_elem
        )
        title = clean_text(title_elem.get_text() if title_elem else "")
        if not title:
            continue

        # 3. Extract Company (with tiered fallback selectors)
        company_elem = (
            card.select_one(".base-search-card__subtitle")
            or card.select_one(".job-search-card__subtitle")
            or card.select_one("a.hidden-nested-link")
            or card.select_one(".job-search-card__company-name")
            or card.find("h4")
        )
        company = clean_text(company_elem.get_text() if company_elem else "უცნობი კომპანია")

        # 4. Extract Location
        loc_elem = (
            card.select_one(".job-search-card__location")
            or card.select_one(".job-search-card__bullet")
        )
        location_text = clean_text(loc_elem.get_text() if loc_elem else location)

        # 5. Extract published date if available
        time_elem = card.find("time")
        published_date = clean_text(time_elem.get_text() if time_elem else "")

        # Canonical clean job link
        clean_link = f"{LINKEDIN_BASE_URL}/jobs/view/{job_id}"

        jobs.append({
            "job_id": job_id,
            "title": title,
            "company": company,
            "location": location_text,
            "published_date": published_date,
            "link": clean_link,
            "source": "linkedin",
        })

        if len(jobs) >= max_jobs:
            break

    logger.info(f"Successfully scraped {len(jobs)} jobs from LinkedIn guest endpoint.")
    return jobs


def scrape_linkedin_job_details(session: requests.Session, job_id: str) -> str:
    """
    Scrape full job description from LinkedIn guest endpoint.
    Uses multi-tier fallback selectors and handles DOM changes gracefully.
    """
    detail_url = f"{LINKEDIN_GUEST_DETAIL_URL}/{job_id}"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        response = session.get(detail_url, headers=headers, timeout=15)
        response.encoding = "utf-8"

        if response.status_code == 429:
            logger.warning(f"LinkedIn rate limited (429) while fetching details for job ID {job_id}.")
            return ""

        response.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"Failed to fetch LinkedIn job details for {job_id} ({detail_url}): {e}")
        return ""

    soup = BeautifulSoup(response.text, "html.parser")

    # Clean out non-content elements
    for tag in soup(["script", "style", "noscript", "meta"]):
        tag.decompose()

    # Tiered fallback selectors for job description
    desc_elem = (
        soup.select_one(".show-more-less-html__markup")
        or soup.select_one(".description__text")
        or soup.select_one(".show-more-less-html")
        or soup.select_one(".decorated-job-posting__details")
        or soup.select_one("section.description")
        or soup.select_one("div.description__text")
        or soup.select_one("div.core-section-container__content")
        or soup.select_one("article")
    )

    if desc_elem:
        desc_text = desc_elem.get_text(separator="\n", strip=True)
        return clean_text(desc_text)

    # If no expected selector matches, attempt general body container fallback
    logger.warning(
        f"No standard description selector matched for LinkedIn job ID {job_id}. "
        "Attempting body container fallback."
    )
    main_container = soup.find("main") or soup.find("div", class_=re.compile(r"details|content|description"))
    if main_container:
        for tag in main_container(["header", "nav", "footer"]):
            tag.decompose()
        desc_text = main_container.get_text(separator="\n", strip=True)
        if desc_text:
            return clean_text(desc_text)

    logger.warning(f"Unable to extract full description for LinkedIn job ID {job_id}.")
    return ""
