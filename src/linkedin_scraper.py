"""
Backwards-compatibility facade for LinkedIn scraper.
Maintained so existing callers importing from `src.linkedin_scraper` continue to work seamlessly.
"""

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
from src.scrapers.linkedin import (
    extract_job_id,
    scrape_linkedin_job_details,
    scrape_linkedin_jobs,
)
from src.utils import clean_text

__all__ = [
    "LINKEDIN_BASE_URL",
    "LINKEDIN_GUEST_SEARCH_URL",
    "LINKEDIN_GUEST_DETAIL_URL",
    "LINKEDIN_DEFAULT_GEO_ID",
    "LINKEDIN_DEFAULT_LOCATION",
    "LINKEDIN_DEFAULT_KEYWORD",
    "LINKEDIN_DEFAULT_TIME_RANGE",
    "USER_AGENT",
    "logger",
    "clean_text",
    "extract_job_id",
    "scrape_linkedin_jobs",
    "scrape_linkedin_job_details",
]
