"""
Modular scrapers subpackage for jobs.ge, LinkedIn, and future job boards.
"""

from src.scrapers.jobsge import (
    scrape_jobs_listing,
    scrape_jobsge_details,
    scrape_jobsge_listing,
    scrape_job_details,
)
from src.scrapers.linkedin import (
    extract_job_id,
    scrape_linkedin_job_details,
    scrape_linkedin_jobs,
)
from src.scrapers.manifest_engine import (
    fetch_company_job_details,
    get_active_company_ids,
    get_company_manifest,
    load_manifest,
    matches_location_filter,
    scrape_all_companies_concurrent,
    scrape_company_listings,
)

__all__ = [
    "scrape_jobsge_listing",
    "scrape_jobsge_details",
    "scrape_jobs_listing",
    "scrape_job_details",
    "scrape_linkedin_jobs",
    "scrape_linkedin_job_details",
    "extract_job_id",
    "load_manifest",
    "get_company_manifest",
    "get_active_company_ids",
    "scrape_company_listings",
    "fetch_company_job_details",
    "scrape_all_companies_concurrent",
    "matches_location_filter",
]
