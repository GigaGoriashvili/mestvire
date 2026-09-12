"""
Unit tests for Senior-level negative filtering (Stage 1 Title Filter & Stage 2 LLM Filter).
"""

import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import FILTER_SENIOR_ROLES, SENIOR_TITLE_KEYWORDS
from src.database import is_job_seen, mark_job_seen
from src.llm import build_evaluation_prompt, get_evaluation_system_prompt
from src.pipeline import process_single_job
from src.scraper import is_senior_title


class MockDynamoDBStore:
    def __init__(self):
        self.store = {}

    def mark_seen(self, job_id, title, source=None, **kwargs):
        src = source or "jobsge"
        self.store[(src, str(job_id))] = {"title": title, "seen": True}

    def is_seen(self, job_id, source=None, **kwargs):
        src = source or "jobsge"
        return (src, str(job_id)) in self.store


def test_is_senior_title_positive():
    positives = [
        "Senior Data Engineer",
        "senior data analyst",
        "SENIOR BI DEVELOPER",
        "Lead Data Engineer",
        "Tech Lead",
        "Data Analytics Team Lead",
        "BI Developer (Lead)",
        "Lead/Senior Data Architect",
        "უფროსი მონაცემთა ინჟინერი",
        "მონაცემთა ბაზების უფროსი ადმინისტრატორი",
        "სენიორ ანალიტიკოსი",
        "სენიორი დეველოპერი",
        "სენიორ-ინჟინერი",
        "წამყვანი მონაცემთა ინჟინერი",
        "წამყვანი ანალიტიკოსი",
        "მთავარი ანალიტიკოსი",
        "მთავარი ინჟინერი",
        "Principal Data Engineer",
        "Head of BI",
        "Chief Data Officer",
    ]
    for title in positives:
        assert is_senior_title(title), f"Expected True for '{title}'"
    print("PASS: test_is_senior_title_positive")


def test_is_senior_title_negative():
    negatives = [
        "Junior Data Analyst",
        "Middle Data Engineer",
        "Data Analyst",
        "BI Specialist",
        "SQL Developer",
        "ETL Engineer",
        # Important check: 'leading' should NOT match 'lead'
        "Leading Financial Institution seeks Data Analyst",
        "Leading bank hiring ETL Specialist",
        "",
        None,
    ]
    for title in negatives:
        assert not is_senior_title(title), f"Expected False for '{title}'"
    print("PASS: test_is_senior_title_negative")


def test_system_prompt_toggle():
    # Prompt WITH senior filter
    prompt_on = get_evaluation_system_prompt(filter_senior=True)
    expected_rule = (
        "If the job description explicitly demands a Senior, Lead, or Principal role, "
        "or strictly requires senior-level experience (e.g. 5+ years), respond with ONLY the word: REJECT."
    )
    assert expected_rule in prompt_on, "Senior filter rule missing from prompt_on"

    # Prompt WITHOUT senior filter
    prompt_off = get_evaluation_system_prompt(filter_senior=False)
    assert expected_rule not in prompt_off, "Senior filter rule should not be in prompt_off"
    assert "Junior, Mid-level" not in prompt_off, "Prompt off should not restrict levels"
    print("PASS: test_system_prompt_toggle")


def test_build_evaluation_prompt_toggle():
    prompt_default = build_evaluation_prompt("Data Analyst", "TechCo", "Description...")
    # Default should follow FILTER_SENIOR_ROLES (True)
    assert "Senior, Lead, or Principal" in prompt_default

    prompt_disabled = build_evaluation_prompt("Data Analyst", "TechCo", "Description...", filter_senior=False)
    assert "Senior, Lead, or Principal" not in prompt_disabled
    print("PASS: test_build_evaluation_prompt_toggle")


def test_stage1_db_recording():
    mock_store = MockDynamoDBStore()
    mock_session = MagicMock()
    mock_gemini = MagicMock()
    config = {
        "TELEGRAM_BOT_TOKEN": "mock",
        "TELEGRAM_CHAT_ID": "mock",
        "GEMINI_API_KEY": "mock",
    }

    senior_job = {
        "job_id": "99901",
        "title": "Senior Data Engineer",
        "company": "Enterprise Corp",
        "source": "jobsge",
    }

    # 1. Process in live mode with filter_senior=True:
    # Should be skipped at title filter, marked seen in DB, and never call gemini or fetch details
    with patch("src.pipeline.mark_job_seen", side_effect=mock_store.mark_seen), \
         patch("src.pipeline.is_job_seen", side_effect=mock_store.is_seen):
        result = process_single_job(
            mock_session,
            mock_gemini,
            config,
            senior_job,
            is_test=False,
            filter_senior=True,
        )
        assert result is True
        assert mock_store.is_seen("99901", source="jobsge")
        # Verify Gemini client was NOT called
        assert mock_gemini.models.generate_content.call_count == 0

    # 2. In test mode with filter_senior=True:
    test_job = {
        "job_id": "99902",
        "title": "Lead BI Analyst",
        "company": "Analytics Corp",
        "source": "linkedin",
    }
    with patch("src.pipeline.mark_job_seen", side_effect=mock_store.mark_seen), \
         patch("src.pipeline.is_job_seen", side_effect=mock_store.is_seen):
        result = process_single_job(
            mock_session,
            mock_gemini,
            config,
            test_job,
            is_test=True,
            filter_senior=True,
        )
        assert result is True
        # In test mode, should NOT mark seen in DB
        assert not mock_store.is_seen("99902", source="linkedin")
        assert mock_gemini.models.generate_content.call_count == 0

    print("PASS: test_stage1_db_recording")


def test_run_monitor_skipping_in_listing():
    from src.pipeline import run_monitor

    mock_store = MockDynamoDBStore()
    mock_jobs = [
        {"job_id": "1001", "title": "Senior Data Engineer", "published_date": "06 სექტემბერი"},
        {"job_id": "1002", "title": "უფროსი ანალიტიკოსი", "published_date": "06 სექტემბერი"},
        {"job_id": "1003", "title": "Junior Data Analyst", "published_date": "06 სექტემბერი"},
    ]

    with patch("src.pipeline.load_config", return_value={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_CHAT_ID": "x", "GEMINI_API_KEY": "x"}), \
         patch("src.pipeline.init_db", return_value=None), \
         patch("src.pipeline.genai.Client"), \
         patch("src.pipeline.scrape_jobs_listing", return_value=mock_jobs), \
         patch("src.pipeline.is_recent_job", return_value=True), \
         patch("src.pipeline.process_single_job") as mock_process, \
         patch("src.pipeline.mark_job_seen", side_effect=mock_store.mark_seen), \
         patch("src.pipeline.is_job_seen", side_effect=mock_store.is_seen):

        # Run monitor for jobsge with filter_senior=True
        run_monitor(source="jobsge", is_test=False, filter_senior=True)

        # 1001 and 1002 are senior roles -> must be marked seen in DB
        assert mock_store.is_seen("1001", source="jobsge"), "1001 should be marked seen in DB"
        assert mock_store.is_seen("1002", source="jobsge"), "1002 should be marked seen in DB"

        # 1003 is junior -> should NOT be skipped by title filter; it should be passed to process_single_job
        assert not mock_store.is_seen("1003", source="jobsge")
        assert mock_process.call_count == 1
        called_job = mock_process.call_args[0][3]
        assert called_job["job_id"] == "1003"

    print("PASS: test_run_monitor_skipping_in_listing")


def test_is_recent_job():
    from datetime import datetime, timedelta
    from src.config import JOBSGE_MAX_DAYS
    from src.scraper import is_recent_job, is_within_one_week, GEORGIAN_MONTHS

    assert JOBSGE_MAX_DAYS == 2, f"Expected JOBSGE_MAX_DAYS == 2, got {JOBSGE_MAX_DAYS}"

    # Format dates in Georgian
    now = datetime.now()
    month_name = [k for k, v in GEORGIAN_MONTHS.items() if v == now.month and not k.endswith("ს")][0]
    
    today_str = f"{now.day} {month_name}"
    yesterday = now - timedelta(days=1)
    yesterday_month = [k for k, v in GEORGIAN_MONTHS.items() if v == yesterday.month and not k.endswith("ს")][0]
    yesterday_str = f"{yesterday.day} {yesterday_month}"

    old_day = now - timedelta(days=5)
    old_month = [k for k, v in GEORGIAN_MONTHS.items() if v == old_day.month and not k.endswith("ს")][0]
    old_str = f"{old_day.day} {old_month}"

    assert is_recent_job(today_str, max_days=2) is True
    assert is_recent_job(yesterday_str, max_days=2) is True
    assert is_recent_job(old_str, max_days=2) is False
    # Backwards compatibility test: is_within_one_week default is 7 days
    assert is_within_one_week(old_str) is True

    print("PASS: test_is_recent_job")


def test_linkedin_keywords_matching():
    from src.config import LINKEDIN_DEFAULT_KEYWORD
    from src.scraper import matches_keywords

    assert LINKEDIN_DEFAULT_KEYWORD == 'data OR analytics OR "business intelligence" OR bi OR etl OR sql OR databricks'

    # Verify each target domain term matches Stage 1 broad keyword filter
    assert matches_keywords("Data Engineer") is True
    assert matches_keywords("Analytics Specialist") is True
    assert matches_keywords("Business Intelligence Developer") is True
    assert matches_keywords("Junior BI Developer") is True
    assert matches_keywords("ETL Developer") is True
    assert matches_keywords("SQL Developer") is True
    assert matches_keywords("Databricks Developer") is True
    assert matches_keywords("Spark Developer") is True
    assert matches_keywords("Tableau Specialist") is True
    assert matches_keywords("Power BI Developer") is True
    assert matches_keywords("Database Administrator") is True
    assert matches_keywords("DBA") is True
    assert matches_keywords("Reporting Specialist") is True
    assert matches_keywords("რეპორტინგის სპეციალისტი") is True

    # Negative check to avoid false positives (e.g. 'mobile' contains 'bi', generic engineering)
    assert matches_keywords("Mobile App Developer") is False
    assert matches_keywords("Front End React Developer") is False
    assert matches_keywords("Software Engineer") is False
    assert matches_keywords("QA Engineer") is False
    assert matches_keywords("DevOps Engineer") is False
    assert matches_keywords("Civil Engineer") is False

    # Data Center false positive prevention (must not match solely due to 'data' in 'data center')
    assert matches_keywords("Cloud Infrastructure Engineer (Data Center)") is False
    assert matches_keywords("Mulsoft Engineer (Data Center)") is False
    assert matches_keywords("RPA Engineer (Data Center)") is False
    assert matches_keywords("Data Center Facilities Technician") is False
    assert matches_keywords("Data Center Architect") is False
    assert matches_keywords("ინფრასტრუქტურის ინჟინერი (მონაცემთა ცენტრი)") is False

    # Valid data engineering / analytics roles involving data centers or other data keywords
    assert matches_keywords("DevOps Data Engineer (Digital Marketing sphere)") is True
    assert matches_keywords("DevOps Data Engineer (Data Center)") is True
    assert matches_keywords("Data Center BI Analyst") is True
    assert matches_keywords("Data Center SQL Specialist") is True
    assert matches_keywords("მონაცემთა ინჟინერი (მონაცემთა ცენტრი)") is True

    print("PASS: test_linkedin_keywords_matching")


if __name__ == "__main__":
    test_is_senior_title_positive()
    test_is_senior_title_negative()
    test_system_prompt_toggle()
    test_build_evaluation_prompt_toggle()
    test_stage1_db_recording()
    test_run_monitor_skipping_in_listing()
    test_is_recent_job()
    test_linkedin_keywords_matching()
    print("\nALL 8 TESTS PASSED SUCCESSFULLY!")
