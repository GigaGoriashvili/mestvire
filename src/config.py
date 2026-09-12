"""
Configuration and logging setup for jobs.ge monitor.
"""

import logging
import os
import re
import sys
from typing import Dict, Optional

from dotenv import load_dotenv

# Early load of .env file so environment variables are available at import time
load_dotenv(override=False)


# Force UTF-8 on Windows stdout/stderr to support Georgian characters and emojis cleanly
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Configure centralized logger
log_handler = logging.StreamHandler(sys.stdout)
log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger = logging.getLogger("jobs_monitor")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(log_handler)
logger.propagate = False

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:
    boto3 = None
    BotoCoreError = Exception
    ClientError = Exception

# Constants & Configuration
BASE_URL = "https://jobs.ge"
TARGET_URL = f"{BASE_URL}/?page=1&q=&cid=6&lid=&jid="
JOBSGE_MAX_DAYS = 2  # Max age in days (48 hours) for jobs.ge vacancies
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# AWS Configuration
AWS_REGION = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "eu-central-1"))
DYNAMODB_TABLE_NAME = os.getenv("DYNAMODB_TABLE_NAME", "jobs_tracker")
DB_NAME = DYNAMODB_TABLE_NAME  # Backwards compatibility alias
SSM_PREFIX = os.getenv("SSM_PREFIX", "/jobs").rstrip("/")

PRIMARY_MODEL = "gemini-3.8-flash"

# Cached config dict to avoid redundant SSM calls across warm Lambda invocations
_CACHED_CONFIG: Optional[Dict[str, str]] = None

# ==============================================================================
# Filter Configuration: Senior / Lead Roles
# ==============================================================================
# Master toggle to turn Senior-level filtering ON or OFF completely across all stages:
# - Stage 1 (Title Filter): Automatically skips any job with senior/lead in title & marks seen in DB
# - Stage 2 (LLM Filter): Prompts Gemini to return REJECT for senior/lead/principal / 5+ yrs roles
# Set this to False in code (or FILTER_SENIOR_ROLES=false in .env) to disable Senior filtering completely.
FILTER_SENIOR_ROLES = os.getenv("FILTER_SENIOR_ROLES", "true").strip().lower() not in ("false", "0", "no")

# Stage 1 Title Filter keywords (case-insensitive)
# Includes Georgian and international senior/lead role indicators
SENIOR_TITLE_KEYWORDS = [
    "senior",
    "lead",
    "უფროსი",
    "სენიორ",
    "წამყვანი",
    "მთავარი",
    "principal",
    "head",
    "chief",
]

# LinkedIn Configuration (Public Guest Endpoints)
LINKEDIN_BASE_URL = "https://www.linkedin.com"
LINKEDIN_GUEST_SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
LINKEDIN_GUEST_DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting"
LINKEDIN_DEFAULT_GEO_ID = "106315325"
LINKEDIN_DEFAULT_LOCATION = "Georgia"
LINKEDIN_DEFAULT_KEYWORD = 'data OR analytics OR "business intelligence" OR bi OR etl OR sql OR databricks'
LINKEDIN_DEFAULT_TIME_RANGE = "r86400"

# Keywords for Data Engineering & Analytics roles (Stage 1: Broad Keyword Filter)
# Focuses on data engineering, analytics, BI, DWH, and databases
# Excludes generic 'engineer'/'ინჟინერი' to prevent non-data engineering false positives
KEYWORDS = [
    # Core Data & Analytics terms
    "data",
    "მონაცემთა",
    "analyst",
    "analytics",
    "ანალიტიკოსი",
    "ანალიტიკის",
    "bi",
    "business intelligence",
    "etl",
    "dbt",
    "sql",
    "dwh",
    # Platforms, Big Data & Databases
    "databricks",
    "spark",
    "big data",
    "bigdata",
    "database",
    "dba",
    # BI Tools & Reporting
    "tableau",
    "power bi",
    "powerbi",
    "qlik",
    "reporting",
    "რეპორტინგი",
    "რეპორტინგის",
]

# Short acronyms requiring regex word boundaries to prevent false positives (e.g. 'mobile' matching 'bi')
SHORT_ACRONYMS = {"bi", "etl", "dbt", "sql", "dwh", "dba"}
SHORT_ACRONYMS_REGEX = re.compile(r"\b(bi|sql|etl|dbt|dwh|dba)\b", re.IGNORECASE)

# Pattern matching data center / datacenter terms to avoid false positive 'data' keyword matches
DATA_CENTER_REGEX = re.compile(
    r"\bdata[\s-]*cent(?:er|re)s?\b|\bdatacent(?:er|re)s?\b|მონაცემთა\s+ცენტრ\w*",
    re.IGNORECASE,
)

# Georgian month name to month number mapping
GEORGIAN_MONTHS = {
    "იანვარი": 1, "იანვარს": 1,
    "თებერვალი": 2, "თებერვალს": 2,
    "მარტი": 3, "მარტს": 3,
    "აპრილი": 4, "აპრილს": 4,
    "მაისი": 5, "მაისს": 5,
    "ივნისი": 6, "ივნისს": 6,
    "ივლისი": 7, "ივლისს": 7,
    "აგვისტო": 8, "აგვისტოს": 8,
    "სექტემბერი": 9, "სექტემბერს": 9,
    "ოქტომბერი": 10, "ოქტომბერს": 10,
    "ნოემბერი": 11, "ნოემბერს": 11,
    "დეკემბერი": 12, "დეკემბერს": 12,
}


def fetch_ssm_parameters(ssm_prefix: str = SSM_PREFIX, region_name: str = AWS_REGION) -> Dict[str, str]:
    """
    Attempt to fetch secrets from AWS SSM Parameter Store under ssm_prefix (default: /jobs).
    Returns a dictionary of key-value pairs (e.g. {'TELEGRAM_BOT_TOKEN': '...', ...}).
    Falls back gracefully to empty dict if SSM is unavailable or fails.
    """
    if boto3 is None:
        return {}

    # Query lowercase and uppercase variants: /jobs/telegram_bot_token and /jobs/TELEGRAM_BOT_TOKEN
    param_keys = [
        "telegram_bot_token", "TELEGRAM_BOT_TOKEN",
        "telegram_chat_id", "TELEGRAM_CHAT_ID",
        "gemini_api_key", "GEMINI_API_KEY",
    ]
    param_paths = [f"{ssm_prefix}/{key}" for key in param_keys]

    results: Dict[str, str] = {}
    try:
        ssm = boto3.client("ssm", region_name=region_name)
        response = ssm.get_parameters(Names=param_paths, WithDecryption=True)
        for param in response.get("Parameters", []):
            name = param.get("Name", "")
            val = param.get("Value", "")
            short_key = name.split("/")[-1].upper()
            results[short_key] = val
        if results:
            logger.info(f"Successfully loaded {len(results)} secret(s) from AWS SSM Parameter Store ({ssm_prefix}).")
    except (BotoCoreError, ClientError, Exception) as e:
        logger.debug(f"SSM Parameter Store fetch skipped or failed ({e}); proceeding with environment variable fallback.")

    return results


def load_config(reload: bool = False) -> Dict[str, str]:
    """
    Load and validate required configuration and secrets.
    Attempts to read from AWS SSM Parameter Store (/jobs/...) first,
    falling back to environment variables (.env or OS environment).
    Caches the result to avoid redundant network roundtrips in warm Lambda environments.
    """
    global _CACHED_CONFIG
    if not reload and _CACHED_CONFIG is not None:
        return _CACHED_CONFIG

    load_dotenv(override=False)

    # 1. Fetch from AWS SSM Parameter Store
    ssm_params = fetch_ssm_parameters(ssm_prefix=SSM_PREFIX, region_name=AWS_REGION)

    # 2. Resolve required secrets with SSM precedence and env fallback
    telegram_bot_token = ssm_params.get("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = ssm_params.get("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    gemini_api_key = ssm_params.get("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY")

    missing = []
    if not telegram_bot_token:
        missing.append("TELEGRAM_BOT_TOKEN (/jobs/telegram_bot_token)")
    if not telegram_chat_id:
        missing.append("TELEGRAM_CHAT_ID (/jobs/telegram_chat_id)")
    if not gemini_api_key:
        missing.append("GEMINI_API_KEY (/jobs/gemini_api_key)")

    if missing:
        err_msg = f"Missing required configuration/secrets: {', '.join(missing)}"
        logger.error(err_msg)
        raise ValueError(err_msg)

    config = {
        "TELEGRAM_BOT_TOKEN": telegram_bot_token.strip().strip('"').strip("'"),
        "TELEGRAM_CHAT_ID": telegram_chat_id.strip().strip('"').strip("'"),
        "GEMINI_API_KEY": gemini_api_key.strip().strip('"').strip("'"),
        "DYNAMODB_TABLE_NAME": os.getenv("DYNAMODB_TABLE_NAME", DYNAMODB_TABLE_NAME),
        "AWS_REGION": os.getenv("AWS_REGION", AWS_REGION),
        "SSM_PREFIX": SSM_PREFIX,
    }

    _CACHED_CONFIG = config
    return config
