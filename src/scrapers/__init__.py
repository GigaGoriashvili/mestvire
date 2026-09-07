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

__all__ = [
    "scrape_jobsge_listing",
    "scrape_jobsge_details",
    "scrape_jobs_listing",
    "scrape_job_details",
    "scrape_linkedin_jobs",
    "scrape_linkedin_job_details",
    "extract_job_id",
]
