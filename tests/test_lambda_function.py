"""
Unit tests for AWS Lambda entrypoint handler (lambda_function.py)
and modular scraper registry functionality.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lambda_function import lambda_handler
from src.pipeline import SCRAPER_REGISTRY, register_scraper, run_monitor


def test_lambda_handler_default_event():
    mock_stats = {
        "jobsge": {"total_scraped": 10, "new_matched": 2, "alerts_sent": 1},
        "linkedin": {"total_scraped": 8, "new_matched": 1, "alerts_sent": 1},
    }
    mock_context = MagicMock()
    mock_context.aws_request_id = "test-req-1234"
    mock_context.get_remaining_time_in_millis.return_value = 295000

    with patch("lambda_function.run_monitor", return_value=mock_stats) as mock_run:
        response = lambda_handler({}, mock_context)

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["status"] == "success"
    assert body["requestId"] == "test-req-1234"
    assert body["source"] == "all"
    assert body["is_test"] is False
    assert body["stats"] == mock_stats

    mock_run.assert_called_once_with(
        source="all",
        is_test=False,
        test_source="jobsge",
        filter_senior=True,
    )
    print("PASS: test_lambda_handler_default_event")


def test_lambda_handler_custom_event():
    mock_context = MagicMock()
    mock_context.aws_request_id = "custom-req-5678"

    event = {
        "source": "linkedin",
        "test": True,
        "test_source": "linkedin",
        "filter_senior": False,
    }

    with patch("lambda_function.run_monitor", return_value={"linkedin": {}}) as mock_run:
        response = lambda_handler(event, mock_context)

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["status"] == "success"
    assert body["source"] == "linkedin"
    assert body["is_test"] is True

    mock_run.assert_called_once_with(
        source="linkedin",
        is_test=True,
        test_source="linkedin",
        filter_senior=False,
    )
    print("PASS: test_lambda_handler_custom_event")


def test_lambda_handler_error_response():
    mock_context = MagicMock()
    mock_context.aws_request_id = "err-req-999"

    with patch("lambda_function.run_monitor", side_effect=RuntimeError("SSM Service Unavailable")):
        response = lambda_handler({}, mock_context)

    assert response["statusCode"] == 500
    body = json.loads(response["body"])
    assert body["status"] == "error"
    assert "SSM Service Unavailable" in body["error"]
    print("PASS: test_lambda_handler_error_response")


def test_modular_scraper_registration():
    # Register a new custom scraper 'mock_remote'
    mock_listings_fn = MagicMock(return_value=[
        {"job_id": "rem_1", "title": "Junior Data Analyst", "company": "RemoteCo"}
    ])
    mock_details_fn = MagicMock(return_value="Detailed job description for data analyst...")

    register_scraper(
        name="mock_remote",
        fetch_listings=mock_listings_fn,
        fetch_details=mock_details_fn,
    )

    assert "mock_remote" in SCRAPER_REGISTRY
    assert SCRAPER_REGISTRY["mock_remote"]["name"] == "mock_remote"

    # Test execution of the newly registered scraper
    with patch("src.pipeline.load_config", return_value={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_CHAT_ID": "x", "GEMINI_API_KEY": "x"}), \
         patch("src.pipeline.init_db"), \
         patch("src.pipeline.genai.Client"), \
         patch("src.pipeline.is_job_seen", return_value=False), \
         patch("src.pipeline.process_single_job") as mock_process:

        stats = run_monitor(source="mock_remote", is_test=False)
        assert "mock_remote" in stats
        assert stats["mock_remote"]["total_scraped"] == 1
        assert mock_listings_fn.call_count == 1
        assert mock_process.call_count == 1

    print("PASS: test_modular_scraper_registration")


if __name__ == "__main__":
    test_lambda_handler_default_event()
    test_lambda_handler_custom_event()
    test_lambda_handler_error_response()
    test_modular_scraper_registration()
    print("\nALL LAMBDA & MODULAR SCRAPER TESTS PASSED SUCCESSFULLY!")
