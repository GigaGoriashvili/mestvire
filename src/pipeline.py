"""
Pipeline module orchestrating the job monitoring and evaluation workflow.
"""

from typing import Any, Callable, Dict, List, Optional

import requests
from google import genai

from src.config import (
    FILTER_SENIOR_ROLES,
    JOBSGE_MAX_DAYS,
    PRIMARY_MODEL,
    load_config,
    logger,
)
from src.database import init_db, is_job_seen, mark_job_seen
from src.filters import is_senior_title, matches_keywords
from src.llm import build_evaluation_prompt, call_gemini_with_retry, is_rejected_decision
from src.notifier import send_telegram_alert
from src.scrapers.jobsge import scrape_job_details, scrape_jobs_listing
from src.scrapers.linkedin import (
    scrape_linkedin_job_details,
    scrape_linkedin_jobs,
)
from src.utils import is_recent_job, is_within_one_week

# Registry of modular scraper sources.
# New scrapers (e.g. CV.ge, Indeed) can register via register_scraper()
SCRAPER_REGISTRY: Dict[str, Dict[str, Any]] = {
    "jobsge": {
        "name": "jobsge",
        "fetch_listings": lambda session: scrape_jobs_listing(session),
        "fetch_details": lambda session, job_id: scrape_job_details(session, job_id),
        "date_filter": lambda job: not (
            job.get("published_date") and not is_recent_job(job["published_date"], max_days=JOBSGE_MAX_DAYS)
        ),
    },
    "linkedin": {
        "name": "linkedin",
        "fetch_listings": lambda session: scrape_linkedin_jobs(session),
        "fetch_details": lambda session, job_id: scrape_linkedin_job_details(session, job_id),
        "date_filter": lambda job: True,
    },
}


def register_scraper(
    name: str,
    fetch_listings: Callable[[requests.Session], List[Dict[str, Any]]],
    fetch_details: Callable[[requests.Session, str], str],
    date_filter: Optional[Callable[[Dict[str, Any]], bool]] = None,
) -> None:
    """Register a new job scraper source dynamically to extend scraping coverage."""
    SCRAPER_REGISTRY[name] = {
        "name": name,
        "fetch_listings": fetch_listings,
        "fetch_details": fetch_details,
        "date_filter": date_filter or (lambda job: True),
    }
    logger.info(f"Registered new modular scraper source: '{name}'")


def fetch_job_details(session: requests.Session, job: Dict[str, str]) -> str:
    """Fetch job description based on registered scraper or default fallback."""
    source = job.get("source", "jobsge")
    job_id = job["job_id"]
    if source in SCRAPER_REGISTRY:
        return SCRAPER_REGISTRY[source]["fetch_details"](session, job_id)
    if source == "linkedin":
        return scrape_linkedin_job_details(session, job_id)
    return scrape_job_details(session, job_id)


def process_single_job(
    session: requests.Session,
    gemini_client: genai.Client,
    config: Dict[str, str],
    job: Dict[str, str],
    is_test: bool = False,
    filter_senior: Optional[bool] = None,
    stats: Optional[Dict[str, int]] = None,
) -> bool:
    """
    Process a single vacancy:
    1. Check Stage 1 Negative Filter (Senior/Lead title).
    2. Scrape full description from source (jobs.ge, LinkedIn, etc.).
    3. Stage 2 semantic evaluation & summarization via Gemini.
    4. If REJECT: log [SKIPPED/REJECTED], record into seen_jobs (if live).
    5. If accepted: display decision, send Telegram alert, record into seen_jobs (if live).
    """
    if filter_senior is None:
        filter_senior = FILTER_SENIOR_ROLES

    job_id = job["job_id"]
    title = job["title"]
    company = job["company"]
    source = job.get("source", "jobsge")

    logger.info(f"Processing vacancy ({source}): [{job_id}] '{title}' at '{company}'")

    # Stage 1 Negative Filter: Senior / Lead title check
    if filter_senior and is_senior_title(title):
        logger.info(f"[SKIPPED/SENIOR TITLE] {job_id} - '{title}'")
        if stats is not None:
            stats["senior_skipped"] = stats.get("senior_skipped", 0) + 1
        if is_test:
            logger.info(f"[TEST DECISION] REJECT: Job [{job_id}] '{title}' rejected by Senior Title Filter.")
        else:
            mark_job_seen(job_id, title, source=source)
            logger.info(f"Recorded senior job ID {job_id} ({source}) to database.")
        return True

    # Scrape detail text with graceful fallback
    details_text = fetch_job_details(session, job)
    if not details_text:
        details_text = f"ვაკანსია: {title} კომპანიაში {company}"

    # Stage 2: Combined Semantic Classifier & Summarizer (Gemini)
    prompt = build_evaluation_prompt(title, company, details_text, filter_senior=filter_senior)
    eval_result = call_gemini_with_retry(gemini_client, prompt, model=PRIMARY_MODEL)

    if eval_result is None:
        logger.warning(f"Skipping job [{job_id}] '{title}' due to Gemini API evaluation failure.")
        if stats is not None:
            stats["errors"] = stats.get("errors", 0) + 1
        return False

    # Check for REJECT decision
    if is_rejected_decision(eval_result):
        logger.info(f"[SKIPPED/REJECTED] {job_id} - {title}")
        if stats is not None:
            stats["rejected"] = stats.get("rejected", 0) + 1
        if is_test:
            logger.info(f"[TEST DECISION] REJECT: Job [{job_id}] '{title}' is outside target data domains.")
        else:
            mark_job_seen(job_id, title, source=source)
            logger.info(f"Recorded rejected job ID {job_id} ({source}) to database.")
        return True

    # Job accepted!
    logger.info(f"[ACCEPTED] {job_id} - {title}")
    if stats is not None:
        stats["accepted"] = stats.get("accepted", 0) + 1
    if is_test:
        logger.info(f"[TEST DECISION] ACCEPTED: Job [{job_id}] '{title}' matches target domains.")
        logger.info(f"[TEST SUMMARY]\n{eval_result}")

    # Deliver via Telegram
    sent = send_telegram_alert(
        session=session,
        bot_token=config["TELEGRAM_BOT_TOKEN"],
        chat_id=config["TELEGRAM_CHAT_ID"],
        job=job,
        summary=eval_result,
    )

    if sent:
        if stats is not None:
            stats["alerts_sent"] = stats.get("alerts_sent", 0) + 1
        if not is_test:
            mark_job_seen(job_id, title, source=source)
            logger.info(f"Saved accepted job ID {job_id} ({source}) to database.")
        else:
            logger.info(f"Test run: job ID {job_id} ({source}) processed without DB commit.")
        return True
    else:
        if stats is not None:
            stats["errors"] = stats.get("errors", 0) + 1
        logger.error(f"Failed to deliver alert for job ID {job_id} ({source}).")
        return False


def _run_test_mode(
    session: requests.Session,
    gemini_client: genai.Client,
    config: Dict[str, str],
    test_source: str,
    filter_senior: Optional[bool] = None,
    stats: Optional[Dict[str, Dict[str, int]]] = None,
) -> None:
    """Execute test run for specific or all registered sources."""
    sources_to_test = list(SCRAPER_REGISTRY.keys()) if test_source == "all" else [test_source]

    for src in sources_to_test:
        logger.info(f"Running --test mode specifically for source: '{src}'")
        adapter = SCRAPER_REGISTRY.get(src)
        if not adapter:
            logger.warning(f"Unrecognized test source: '{src}'")
            continue

        jobs = adapter["fetch_listings"](session)
        for j in jobs:
            j["source"] = src

        if not jobs:
            logger.warning(f"No jobs found or failed to fetch listings for source: '{src}'.")
            continue

        first_job = jobs[0]
        logger.info(
            f"Selected first {src} job for test: {first_job['title']} "
            f"(ID: {first_job['job_id']})"
        )
        src_stats = stats.get(src) if stats else None
        process_single_job(
            session,
            gemini_client,
            config,
            first_job,
            is_test=True,
            filter_senior=filter_senior,
            stats=src_stats,
        )


def run_monitor(
    source: str = "all",
    is_test: bool = False,
    test_source: str = "jobsge",
    filter_senior: Optional[bool] = None,
) -> Dict[str, Dict[str, int]]:
    """
    Execute monitoring cycle for configured sources using the modular scraper registry.

    Parameters:
    - source: Which sources to monitor in live mode ('all', 'jobsge', 'linkedin', or any registered source)
    - is_test: If True, run in test mode without DB persistence
    - test_source: Which source to test in test mode ('jobsge', 'linkedin', 'all')
    - filter_senior: If True, filter out senior/lead roles (defaults to FILTER_SENIOR_ROLES)

    Returns:
    - Dictionary with execution statistics per source.
    """
    if filter_senior is None:
        filter_senior = FILTER_SENIOR_ROLES

    config = load_config()
    init_db()

    # Initialize Google GenAI client
    gemini_client = genai.Client(api_key=config["GEMINI_API_KEY"])
    session = requests.Session()

    # Determine sources to run
    if source == "all":
        target_sources = list(SCRAPER_REGISTRY.keys())
    elif source in SCRAPER_REGISTRY:
        target_sources = [source]
    else:
        logger.warning(f"Specified source '{source}' is not in registry; attempting fallback.")
        target_sources = [source]

    # Initialize statistics per source
    stats: Dict[str, Dict[str, int]] = {
        src: {
            "total_scraped": 0,
            "new_matched": 0,
            "senior_skipped": 0,
            "processed": 0,
            "accepted": 0,
            "rejected": 0,
            "alerts_sent": 0,
            "errors": 0,
        }
        for src in (list(SCRAPER_REGISTRY.keys()) if is_test and test_source == "all" else target_sources)
    }

    if is_test:
        _run_test_mode(session, gemini_client, config, test_source, filter_senior=filter_senior, stats=stats)
        return stats

    # Modular execution across each target source
    for src in target_sources:
        adapter = SCRAPER_REGISTRY.get(src)
        if not adapter:
            logger.warning(f"No registered adapter found for source '{src}'. Skipping.")
            continue

        logger.info(f"Starting {src} scraping cycle...")
        try:
            jobs_list = adapter["fetch_listings"](session)
        except Exception as e:
            logger.exception(f"Failed to fetch listings for source '{src}': {e}")
            stats[src]["errors"] += 1
            continue

        stats[src]["total_scraped"] = len(jobs_list)
        date_filter = adapter.get("date_filter", lambda job: True)
        matching_jobs: List[Dict[str, Any]] = []

        for job in jobs_list:
            job["source"] = src
            job_id = job["job_id"]
            title = job.get("title", "")

            # Source-specific date/age filter
            if not date_filter(job):
                continue

            if is_job_seen(job_id, source=src):
                continue

            # Stage 1 Negative Filter: Senior/Lead roles
            if filter_senior and is_senior_title(title):
                mark_job_seen(job_id, title, source=src)
                logger.info(f"[SKIPPED/SENIOR TITLE] {job_id} ({src}) - '{title}' recorded to database.")
                stats[src]["senior_skipped"] += 1
                continue

            if matches_keywords(title):
                matching_jobs.append(job)

        stats[src]["new_matched"] = len(matching_jobs)
        logger.info(f"Found {len(matching_jobs)} new matching {src} vacancies.")

        for job in matching_jobs:
            try:
                process_single_job(
                    session,
                    gemini_client,
                    config,
                    job,
                    is_test=False,
                    filter_senior=filter_senior,
                    stats=stats[src],
                )
                stats[src]["processed"] += 1
            except Exception as e:
                stats[src]["errors"] += 1
                logger.exception(f"Unexpected error processing {src} job {job.get('job_id')}: {e}")

    return stats

