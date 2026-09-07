"""
Shared utility functions for text normalization and date parsing.
"""

import re
from datetime import datetime, timedelta
from typing import Optional

from src.config import GEORGIAN_MONTHS


def clean_text(text: str) -> str:
    """Normalize whitespace and strip extraneous characters."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def parse_georgian_date(date_str: str) -> Optional[datetime]:
    """Parse a Georgian date string like '05 სექტემბერი' into a datetime object."""
    if not date_str:
        return None
    match = re.search(r"(\d{1,2})\s+([ა-ჰ]+)", date_str.strip())
    if not match:
        return None
    day = int(match.group(1))
    month_name = match.group(2).lower()
    month = GEORGIAN_MONTHS.get(month_name)
    if not month:
        return None

    now = datetime.now()
    year = now.year
    try:
        parsed = datetime(year, month, day)
        # If the parsed date is in the future by more than 1 day, it belongs to the previous year
        if parsed > now + timedelta(days=1):
            parsed = datetime(year - 1, month, day)
        return parsed
    except ValueError:
        return None


def is_recent_job(date_str: str, max_days: int = 2) -> bool:
    """Check if the published date string is within the last max_days (default: 2 days / 48 hours)."""
    if not date_str:
        return True
    parsed = parse_georgian_date(date_str)
    if not parsed:
        return True
    now = datetime.now()
    age = now - parsed
    return age.days <= max_days


def is_within_one_week(date_str: str, max_days: int = 7) -> bool:
    """Backwards-compatible alias for is_recent_job with 7-day default."""
    return is_recent_job(date_str, max_days=max_days)
