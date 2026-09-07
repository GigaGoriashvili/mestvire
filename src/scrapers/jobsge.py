"""
Scraper module for jobs.ge listings and vacancy details.
"""

import re
from typing import Dict, List

import requests
from bs4 import BeautifulSoup

from src.config import (
    BASE_URL,
    TARGET_URL,
    USER_AGENT,
    logger,
)
from src.utils import clean_text


def scrape_jobsge_listing(session: requests.Session) -> List[Dict[str, str]]:
    """
    Scrape jobs.ge listing page and extract job metadata:
    job_id, title, company, and link.
    """
    headers = {"User-Agent": USER_AGENT}
    try:
        response = session.get(TARGET_URL, headers=headers, timeout=15)
        # Explicit UTF-8 decoding to ensure Georgian characters render properly
        response.encoding = "utf-8"
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch jobs listing from {TARGET_URL}: {e}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    jobs = []
    seen_in_page = set()

    # Find job listing tables (usually #job_list_table and any regular/vip entries)
    rows = soup.select("table#job_list_table tr, div.regularEntries table tr, div.vipEntries table tr")
    if not rows:
        # Fallback to all table rows with links containing ?view=jobs
        rows = soup.find_all("tr")

    for row in rows:
        # Look for links containing view=jobs
        links = row.find_all("a", href=True)
        job_link_elem = None
        for a in links:
            href = a["href"]
            if "view=jobs" in href and "id=" in href and "print=yes" not in href:
                job_link_elem = a
                break

        if not job_link_elem:
            continue

        href = job_link_elem["href"]
        match = re.search(r"id=(\d+)", href)
        if not match:
            continue

        job_id = match.group(1)
        if job_id in seen_in_page:
            continue
        seen_in_page.add(job_id)

        title = clean_text(job_link_elem.get_text())
        if not title:
            continue

        # Extract company name from the 4th column (td index 3) if available, or client link
        cols = row.find_all("td")
        company = "უცნობი კომპანია"
        published_date = ""
        if len(cols) >= 4:
            company_candidate = clean_text(cols[3].get_text())
            if company_candidate:
                company = company_candidate
        else:
            client_elem = row.find("a", href=re.compile(r"view=client"))
            if client_elem:
                company = clean_text(client_elem.get_text())

        # Extract published date from column 5 (td index 4) if present
        if len(cols) >= 5:
            published_date = clean_text(cols[4].get_text())

        full_link = f"{BASE_URL}/?view=jobs&id={job_id}"

        jobs.append({
            "job_id": job_id,
            "title": title,
            "company": company,
            "published_date": published_date,
            "link": full_link,
            "source": "jobsge",
        })

    logger.info(f"Successfully scraped {len(jobs)} jobs from listing.")
    return jobs


# Backwards compatibility alias
scrape_jobs_listing = scrape_jobsge_listing


def scrape_jobsge_details(session: requests.Session, job_id: str) -> str:
    """
    Scrape individual jobs.ge vacancy detail page and extract the description text.
    """
    detail_url = f"{BASE_URL}/?view=jobs&id={job_id}"
    headers = {"User-Agent": USER_AGENT}
    try:
        response = session.get(detail_url, headers=headers, timeout=15)
        # Explicit UTF-8 decoding to ensure Georgian characters render properly
        response.encoding = "utf-8"
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch job details for {job_id} ({detail_url}): {e}")
        return ""

    soup = BeautifulSoup(response.text, "html.parser")

    # The main vacancy content is typically located inside table.dtable
    dtable = soup.find("table", class_="dtable")
    if dtable:
        # Extract external links from dtable (e.g., HireHive, company career pages)
        ext_links = []
        for a in dtable.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith("http") and "jobs.ge" not in href:
                ext_links.append(f"{a.get_text(strip=True)}: {href}")

        # Extract text from the full description container
        rows = dtable.find_all("tr")
        if len(rows) >= 4:
            # Combine all content rows starting from row 4
            desc_parts = [r.get_text(separator="\n", strip=True) for r in rows[3:] if r.get_text(strip=True)]
            desc_text = "\n".join(desc_parts)
        else:
            desc_text = dtable.get_text(separator="\n", strip=True)

        if ext_links:
            desc_text += "\n\nგარე ბმულები:\n" + "\n".join(ext_links)

        return desc_text

    # Fallback to main body container
    body_container = soup.find("div", class_="inner_text") or soup.find("div", id="job_details")
    if body_container:
        return body_container.get_text(separator="\n", strip=True)

    # Fallback to general page text excluding nav
    return soup.get_text(separator="\n", strip=True)


# Backwards compatibility alias
scrape_job_details = scrape_jobsge_details
