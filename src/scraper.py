"""
Backwards-compatibility facade for jobs.ge scraper, utilities, and filters.
Maintained so existing callers importing from `src.scraper` continue to work seamlessly.
"""

from src.config import (
    BASE_URL,
    GEORGIAN_MONTHS,
    KEYWORDS,
    SENIOR_TITLE_KEYWORDS,
    SHORT_ACRONYMS,
    SHORT_ACRONYMS_REGEX,
    TARGET_URL,
    USER_AGENT,
    logger,
)
from src.filters import (
    is_senior_title,
    matches_keywords,
)
from src.scrapers.jobsge import (
    scrape_job_details,
    scrape_jobs_listing,
    scrape_jobsge_details,
    scrape_jobsge_listing,
)
from src.utils import (
    clean_text,
    is_recent_job,
    is_within_one_week,
    parse_georgian_date,
)

__all__ = [
    "BASE_URL",
    "TARGET_URL",
    "USER_AGENT",
    "KEYWORDS",
    "SENIOR_TITLE_KEYWORDS",
    "SHORT_ACRONYMS",
    "SHORT_ACRONYMS_REGEX",
    "GEORGIAN_MONTHS",
    "logger",
    "clean_text",
    "parse_georgian_date",
    "is_recent_job",
    "is_within_one_week",
    "matches_keywords",
    "is_senior_title",
    "scrape_jobs_listing",
    "scrape_jobsge_listing",
    "scrape_job_details",
    "scrape_jobsge_details",
]
