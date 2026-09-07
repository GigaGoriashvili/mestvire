"""
Database operations for tracking seen vacancies using Amazon DynamoDB.
"""

import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:
    boto3 = None
    BotoCoreError = Exception
    ClientError = Exception

from src.config import AWS_REGION, DYNAMODB_TABLE_NAME, logger

# Module-level cache for DynamoDB resource to optimize warm Lambda executions
_dynamodb_resource: Optional[Any] = None


def get_dynamodb_resource(region_name: Optional[str] = None) -> Any:
    """Get or create cached boto3 DynamoDB resource."""
    global _dynamodb_resource
    if _dynamodb_resource is None:
        if boto3 is None:
            raise RuntimeError("boto3 is required for DynamoDB operations. Please install boto3.")
        _dynamodb_resource = boto3.resource("dynamodb", region_name=region_name or AWS_REGION)
    return _dynamodb_resource


def get_table(table_name: Optional[str] = None, region_name: Optional[str] = None) -> Any:
    """Return DynamoDB Table instance for the specified table name."""
    resource = get_dynamodb_resource(region_name=region_name)
    target_table = table_name or os.getenv("DYNAMODB_TABLE_NAME", DYNAMODB_TABLE_NAME)
    return resource.Table(target_table)


def make_composite_id(job_id: str, source: Optional[str] = None) -> str:
    """
    Generate composite identifier in the format 'source:job_id'.
    Maintained for backwards compatibility with legacy callers.
    """
    if not source:
        return job_id
    prefix = f"{source}:"
    if job_id.startswith(prefix):
        return job_id
    return f"{prefix}{job_id}"


def _normalize_keys(job_id: str, source: Optional[str] = None) -> Tuple[str, str]:
    """
    Normalize source and job_id.
    Handles legacy composite IDs like 'jobsge:12345' gracefully.
    """
    clean_job_id = str(job_id).strip()
    clean_source = str(source).strip() if source else ""

    if not clean_source:
        if ":" in clean_job_id:
            clean_source, clean_job_id = clean_job_id.split(":", 1)
        else:
            clean_source = "jobsge"
    elif ":" in clean_job_id and clean_job_id.startswith(f"{clean_source}:"):
        clean_job_id = clean_job_id[len(clean_source) + 1:]

    return clean_source, clean_job_id


def is_job_seen(
    job_id: str,
    source: Optional[str] = "jobsge",
    table_name: Optional[str] = None,
    db_path: Optional[str] = None,
) -> bool:
    """
    Check if a job has already been processed and recorded in DynamoDB.
    Schema:
      - Partition key: source (String)
      - Sort key: job_id (String)
    """
    target_table = table_name or db_path or os.getenv("DYNAMODB_TABLE_NAME", DYNAMODB_TABLE_NAME)
    src, jid = _normalize_keys(job_id, source)

    try:
        table = get_table(table_name=target_table)
        response = table.get_item(
            Key={"source": src, "job_id": jid},
            ProjectionExpression="#src, #jid",
            ExpressionAttributeNames={"#src": "source", "#jid": "job_id"},
        )
        return "Item" in response
    except (BotoCoreError, ClientError, Exception) as e:
        logger.error(f"Error querying DynamoDB for job [{jid}] in source [{src}]: {e}")
        return False


def mark_job_seen(
    job_id: str,
    title: str,
    source: Optional[str] = "jobsge",
    table_name: Optional[str] = None,
    db_path: Optional[str] = None,
) -> None:
    """
    Record a seen job in DynamoDB table 'jobs_tracker'.
    Sets:
      - source: Partition key (String)
      - job_id: Sort key (String)
      - title: Job title (String)
      - seen_at: ISO timestamp (String)
      - expire_at: Unix epoch timestamp in seconds (+30 days TTL)
    """
    target_table = table_name or db_path or os.getenv("DYNAMODB_TABLE_NAME", DYNAMODB_TABLE_NAME)
    src, jid = _normalize_keys(job_id, source)

    # Calculate TTL: 30 days from now in Unix epoch seconds
    now_epoch = int(time.time())
    ttl_seconds = 30 * 24 * 60 * 60  # 30 days
    expire_at = now_epoch + ttl_seconds

    item = {
        "source": src,
        "job_id": jid,
        "title": title or "",
        "seen_at": datetime.now(timezone.utc).isoformat(),
        "expire_at": expire_at,
    }

    try:
        table = get_table(table_name=target_table)
        table.put_item(Item=item)
        logger.debug(f"Recorded job [{jid}] ({src}) with TTL {expire_at} to DynamoDB.")
    except (BotoCoreError, ClientError, Exception) as e:
        logger.error(f"Failed to record job [{jid}] ({src}) to DynamoDB table '{target_table}': {e}")
        raise


def init_db(table_name: Optional[str] = None, db_path: Optional[str] = None) -> None:
    """
    Initialize / verify DynamoDB table connection.
    In AWS environments, the table is usually pre-provisioned.
    If the table does not exist and permissions allow, creates it with TTL enabled.
    """
    target_table = table_name or db_path or os.getenv("DYNAMODB_TABLE_NAME", DYNAMODB_TABLE_NAME)
    if boto3 is None:
        logger.warning("boto3 is not available; skipping DynamoDB initialization.")
        return

    try:
        client = boto3.client("dynamodb", region_name=AWS_REGION)
        # Check if table exists
        existing_tables = client.list_tables().get("TableNames", [])
        if target_table not in existing_tables:
            logger.info(f"DynamoDB table '{target_table}' not found. Attempting to create...")
            client.create_table(
                TableName=target_table,
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
            waiter = client.get_waiter("table_exists")
            waiter.wait(TableName=target_table)
            # Enable TTL on expire_at
            client.update_time_to_live(
                TableName=target_table,
                TimeToLiveSpecification={
                    "Enabled": True,
                    "AttributeName": "expire_at",
                },
            )
            logger.info(f"DynamoDB table '{target_table}' created with TTL on 'expire_at'.")
        else:
            logger.info(f"DynamoDB table '{target_table}' verified and ready.")
    except (BotoCoreError, ClientError, Exception) as e:
        # If IAM lacks ListTables or CreateTable, log informative message without crashing
        logger.info(f"DynamoDB table check for '{target_table}' finished: {e}")
