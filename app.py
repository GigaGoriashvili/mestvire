#!/usr/bin/env python3
"""
jobs.ge Data Roles Monitor & Telegram Alerting System

Monitors jobs.ge (IT category) for Data Engineering & Analytics roles,
summarizes them with Google Gemini in Georgian, and delivers instant alerts to Telegram.
"""

import argparse
import time

from src import (
    BASE_URL,
    DB_NAME,
    EVALUATION_SYSTEM_PROMPT,
    FILTER_SENIOR_ROLES,
    JOBSGE_MAX_DAYS,
    KEYWORDS,
    PRIMARY_MODEL,
    TARGET_URL,
    USER_AGENT,
    build_evaluation_prompt,
    call_gemini_with_retry,
    clean_text,
    format_telegram_summary,
    init_db,
    is_job_seen,
    is_rate_limit_error,
    is_recent_job,
    is_rejected_decision,
    is_within_one_week,
    load_config,
    logger,
    mark_job_seen,
    matches_keywords,
    parse_georgian_date,
    process_single_job,
    run_monitor,
    scrape_job_details,
    scrape_jobs_listing,
    send_telegram_alert,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Monitor jobs.ge & LinkedIn for Data Engineering/Analytics roles with Gemini & Telegram."
    )
    parser.add_argument(
        "--test",
        nargs="?",
        const="jobsge",
        default=None,
        help="Test mode: process the first job from the specified source ('jobsge', 'linkedin', 'companies', 'all', or any company ID like 'epam') and send alert to Telegram.",
    )
    parser.add_argument(
        "--source",
        default="all",
        help="Source to monitor: 'all' (default), 'aggregators', 'companies', 'jobsge', 'linkedin', or any company ID.",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run continuously in a loop at specified interval.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        help="Interval in seconds between scans when running with --loop (default: 300s).",
    )
    parser.add_argument(
        "--no-senior-filter",
        action="store_true",
        help="Disable Senior-level negative filtering (process Senior, Lead, and Principal roles).",
    )

    args = parser.parse_args()
    filter_senior = False if args.no_senior_filter else None

    if args.test is not None:
        test_source = args.test
        if test_source == "jobsge" and args.source != "all":
            test_source = args.source
        run_monitor(
            source=args.source,
            is_test=True,
            test_source=test_source,
            filter_senior=filter_senior,
        )
        return

    if args.loop:
        logger.info(f"Starting continuous monitoring loop for '{args.source}' (interval: {args.interval} seconds)...")
        while True:
            try:
                run_monitor(source=args.source, is_test=False, filter_senior=filter_senior)
            except Exception as e:
                logger.exception(f"Unhandled error during monitoring cycle: {e}")
            logger.info(f"Sleeping for {args.interval} seconds...")
            time.sleep(args.interval)
    else:
        run_monitor(source=args.source, is_test=False, filter_senior=filter_senior)


if __name__ == "__main__":
    main()

