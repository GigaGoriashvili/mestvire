"""
Filtering functions for vacancy domain matching and Senior/Lead negative filtering.
"""

import re
from typing import List, Optional

from src.config import (
    DATA_CENTER_REGEX,
    KEYWORDS,
    SENIOR_TITLE_KEYWORDS,
    SHORT_ACRONYMS,
    SHORT_ACRONYMS_REGEX,
)


def matches_keywords(title: str) -> bool:
    """
    Check if vacancy title matches any target Data/Analytics keywords (Stage 1).
    Uses regex word boundaries for short acronyms (bi, sql, etl, dbt, dwh)
    to prevent false positives like 'mobile' matching 'bi'.
    Prevents 'data center' / 'datacenter' facility roles from falsely matching on 'data'.
    """
    if not title:
        return False
    title_lower = title.lower()

    # 1. Regex word boundary check for short acronyms (case-insensitive)
    if SHORT_ACRONYMS_REGEX.search(title):
        return True

    # Strip data center / datacenter references so pure infrastructure/facility roles
    # do not match simply because the substring 'data' or 'მონაცემთა' is in 'data center'.
    title_no_dc = DATA_CENTER_REGEX.sub(" ", title_lower)

    # 2. Broad keyword check for longer terms against the sanitized title
    for kw in KEYWORDS:
        if kw not in SHORT_ACRONYMS:
            if kw in title_no_dc:
                return True

    return False


def is_senior_title(title: str, keywords: Optional[List[str]] = None) -> bool:
    """
    Check if vacancy title contains senior/lead indicators (Stage 1 negative filter).
    Target keywords: 'senior', 'lead', 'უფროსი', 'სენიორ'.
    Uses regex word boundaries for ASCII terms like 'lead' to avoid false positives (e.g. 'leading').
    """
    if not title:
        return False

    if keywords is None:
        keywords = SENIOR_TITLE_KEYWORDS

    title_lower = title.lower()

    for kw in keywords:
        kw_clean = kw.strip().lower()
        if not kw_clean:
            continue
        # For ASCII/Latin words (e.g. 'lead', 'senior'), enforce word boundary regex to avoid false positives
        if kw_clean.isascii() and kw_clean.isalnum():
            pattern = rf"\b{re.escape(kw_clean)}\b"
            if re.search(pattern, title_lower):
                return True
        else:
            # For Georgian terms or special strings, match substring
            if kw_clean in title_lower:
                return True

    return False
