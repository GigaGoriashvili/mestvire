#!/usr/bin/env python3
"""
AWS Lambda Entrypoint for Mestvire Job Monitor.

Orchestrates job scraping across modular sources (jobs.ge, LinkedIn, etc.),
evaluates vacancies via Google Gemini, and dispatches Telegram alerts.
Persists seen vacancies to Amazon DynamoDB with a 30-day TTL.
"""

import json
from typing import Any, Dict, Optional

from src.config import FILTER_SENIOR_ROLES, logger
from src.pipeline import SCRAPER_REGISTRY, register_scraper, run_monitor


def lambda_handler(event: Optional[Dict[str, Any]], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda handler function.

    Expected event parameters (all optional):
    - source: 'all' (default), 'aggregators', 'companies', 'jobsge', 'linkedin', or any company ID.
    - test: bool, if True runs in test mode (processes first vacancy, does not commit to DB).
    - test_source: 'jobsge' (default), 'linkedin', 'companies', 'all', or any company ID (used when test=True).
    - filter_senior: bool or None, controls senior-level filtering (defaults to config).

    Returns:
    - Dict with standard Lambda proxy response format (statusCode, headers, body).
    """
    event = event or {}
    req_id = getattr(context, "aws_request_id", "local-exec")
    logger.info(f"Lambda execution initiated. Request ID: {req_id}, Event: {json.dumps(event)}")

    # Extract invocation options with sensible defaults
    source = event.get("source", "all")
    is_test = bool(event.get("test", False))
    test_source = event.get("test_source", "jobsge")
    filter_senior = event.get("filter_senior", None)
    if filter_senior is None:
        filter_senior = FILTER_SENIOR_ROLES

    # If remaining execution time is available from context, log it
    if hasattr(context, "get_remaining_time_in_millis"):
        rem_ms = context.get_remaining_time_in_millis()
        logger.info(f"Remaining execution time: {rem_ms} ms")

    try:
        logger.info(
            f"Starting scraper pipeline (source='{source}', is_test={is_test}, "
            f"test_source='{test_source}', filter_senior={filter_senior})..."
        )
        stats = run_monitor(
            source=source,
            is_test=is_test,
            test_source=test_source,
            filter_senior=filter_senior,
        )

        logger.info(f"Scraping cycle completed successfully. Stats: {stats}")
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({
                "status": "success",
                "requestId": req_id,
                "source": source,
                "is_test": is_test,
                "stats": stats,
            }),
        }

    except Exception as e:
        logger.exception(f"Unhandled error in lambda_handler: {e}")
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({
                "status": "error",
                "requestId": req_id,
                "error": str(e),
            }),
        }


if __name__ == "__main__":
    # Local CLI test of lambda_handler
    print("Testing lambda_handler locally...")
    response = lambda_handler({"source": "all", "test": True, "test_source": "jobsge"}, None)
    print("Lambda response:")
    print(json.dumps(response, indent=2))
