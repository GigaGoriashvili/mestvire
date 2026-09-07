"""
Unit tests for DynamoDB database layer (src/database.py).
Verifies Partition Key 'source', Sort Key 'job_id', and TTL attribute 'expire_at' (+30 days).
"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.database import (
    _normalize_keys,
    get_table,
    init_db,
    is_job_seen,
    make_composite_id,
    mark_job_seen,
)


def test_key_normalization():
    # Standard source and job_id
    src, jid = _normalize_keys("12345", "jobsge")
    assert src == "jobsge"
    assert jid == "12345"

    # Composite id passed with explicit source
    src, jid = _normalize_keys("jobsge:12345", "jobsge")
    assert src == "jobsge"
    assert jid == "12345"

    # Composite id passed without explicit source
    src, jid = _normalize_keys("linkedin:67890", None)
    assert src == "linkedin"
    assert jid == "67890"

    # Raw id passed without explicit source (defaults to jobsge)
    src, jid = _normalize_keys("12345", None)
    assert src == "jobsge"
    assert jid == "12345"

    print("PASS: test_key_normalization")


def test_mark_job_seen_put_item_and_ttl():
    mock_table = MagicMock()
    now_before = int(time.time())

    with patch("src.database.get_table", return_value=mock_table):
        mark_job_seen(
            job_id="98765",
            title="Senior Data Analyst",
            source="jobsge",
            table_name="jobs_tracker",
        )

    now_after = int(time.time())
    mock_table.put_item.assert_called_once()
    item = mock_table.put_item.call_args[1]["Item"]

    # Verify schema: PK source, SK job_id
    assert item["source"] == "jobsge", f"Expected source 'jobsge', got {item['source']}"
    assert item["job_id"] == "98765", f"Expected job_id '98765', got {item['job_id']}"
    assert item["title"] == "Senior Data Analyst"
    assert "seen_at" in item

    # Verify TTL: current timestamp + 30 days (30 * 24 * 3600 = 2,592,000 seconds)
    ttl_expected_min = now_before + (30 * 24 * 3600)
    ttl_expected_max = now_after + (30 * 24 * 3600)
    assert ttl_expected_min <= item["expire_at"] <= ttl_expected_max, (
        f"TTL {item['expire_at']} outside expected range [{ttl_expected_min}, {ttl_expected_max}]"
    )

    print("PASS: test_mark_job_seen_put_item_and_ttl")


def test_is_job_seen_query():
    mock_table = MagicMock()

    # Case 1: Job found in DynamoDB
    mock_table.get_item.return_value = {
        "Item": {"source": "linkedin", "job_id": "55555"}
    }
    with patch("src.database.get_table", return_value=mock_table):
        seen = is_job_seen("55555", source="linkedin", table_name="jobs_tracker")
        assert seen is True
        mock_table.get_item.assert_called_with(
            Key={"source": "linkedin", "job_id": "55555"},
            ProjectionExpression="#src, #jid",
            ExpressionAttributeNames={"#src": "source", "#jid": "job_id"},
        )

    # Case 2: Job NOT found
    mock_table.get_item.return_value = {}
    with patch("src.database.get_table", return_value=mock_table):
        seen = is_job_seen("40404", source="jobsge", table_name="jobs_tracker")
        assert seen is False

    print("PASS: test_is_job_seen_query")


def test_init_db_creates_table_with_ttl_when_missing():
    mock_client = MagicMock()
    mock_client.list_tables.return_value = {"TableNames": []}
    mock_waiter = MagicMock()
    mock_client.get_waiter.return_value = mock_waiter

    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = mock_client

    with patch("src.database.boto3", mock_boto3):
        init_db(table_name="jobs_tracker")

    # Verify create_table was called with PK source and SK job_id
    mock_client.create_table.assert_called_once_with(
        TableName="jobs_tracker",
        KeySchema=[
            {"AttributeName": "source", "KeyType": "HASH"},
            {"AttributeName": "job_id", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "source", "AttributeType": "S"},
            {"AttributeName": "job_id", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    # Verify TTL enabled on expire_at
    mock_client.update_time_to_live.assert_called_once_with(
        TableName="jobs_tracker",
        TimeToLiveSpecification={
            "Enabled": True,
            "AttributeName": "expire_at",
        },
    )

    print("PASS: test_init_db_creates_table_with_ttl_when_missing")


if __name__ == "__main__":
    test_key_normalization()
    test_mark_job_seen_put_item_and_ttl()
    test_is_job_seen_query()
    test_init_db_creates_table_with_ttl_when_missing()
    print("\nALL DYNAMODB DATABASE TESTS PASSED SUCCESSFULLY!")
