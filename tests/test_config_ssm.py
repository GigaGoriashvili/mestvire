"""
Unit tests for configuration and AWS SSM Parameter Store resolution (src/config.py).
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import fetch_ssm_parameters, load_config


def test_fetch_ssm_parameters_success():
    mock_ssm = MagicMock()
    mock_ssm.get_parameters.return_value = {
        "Parameters": [
            {"Name": "/jobs/telegram_bot_token", "Value": "ssm_token_123"},
            {"Name": "/jobs/telegram_chat_id", "Value": "ssm_chat_456"},
            {"Name": "/jobs/gemini_api_key", "Value": "ssm_gemini_key_789"},
        ]
    }

    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = mock_ssm
    with patch("src.config.boto3", mock_boto3):
        params = fetch_ssm_parameters(ssm_prefix="/jobs", region_name="eu-central-1")

    assert params.get("TELEGRAM_BOT_TOKEN") == "ssm_token_123"
    assert params.get("TELEGRAM_CHAT_ID") == "ssm_chat_456"
    assert params.get("GEMINI_API_KEY") == "ssm_gemini_key_789"
    print("PASS: test_fetch_ssm_parameters_success")


def test_load_config_ssm_precedence():
    mock_ssm_params = {
        "TELEGRAM_BOT_TOKEN": "ssm_bot_token",
        "TELEGRAM_CHAT_ID": "ssm_chat_id",
        "GEMINI_API_KEY": "ssm_key",
    }

    with patch("src.config.fetch_ssm_parameters", return_value=mock_ssm_params), \
         patch.dict(os.environ, {
             "TELEGRAM_BOT_TOKEN": "env_bot_token",
             "TELEGRAM_CHAT_ID": "env_chat_id",
             "GEMINI_API_KEY": "env_key",
             "DYNAMODB_TABLE_NAME": "jobs_tracker",
         }):
        cfg = load_config(reload=True)

    # SSM values should take precedence over env
    assert cfg["TELEGRAM_BOT_TOKEN"] == "ssm_bot_token"
    assert cfg["TELEGRAM_CHAT_ID"] == "ssm_chat_id"
    assert cfg["GEMINI_API_KEY"] == "ssm_key"
    assert cfg["DYNAMODB_TABLE_NAME"] == "jobs_tracker"
    print("PASS: test_load_config_ssm_precedence")


def test_load_config_fallback_to_env():
    # When SSM returns empty (e.g. running locally without SSM setup)
    with patch("src.config.fetch_ssm_parameters", return_value={}), \
         patch.dict(os.environ, {
             "TELEGRAM_BOT_TOKEN": "fallback_token",
             "TELEGRAM_CHAT_ID": "fallback_chat",
             "GEMINI_API_KEY": "fallback_gemini",
             "DYNAMODB_TABLE_NAME": "jobs_tracker_dev",
         }):
        cfg = load_config(reload=True)

    assert cfg["TELEGRAM_BOT_TOKEN"] == "fallback_token"
    assert cfg["TELEGRAM_CHAT_ID"] == "fallback_chat"
    assert cfg["GEMINI_API_KEY"] == "fallback_gemini"
    assert cfg["DYNAMODB_TABLE_NAME"] == "jobs_tracker_dev"
    print("PASS: test_load_config_fallback_to_env")


def test_load_config_missing_raises():
    with patch("src.config.fetch_ssm_parameters", return_value={}), \
         patch("src.config.load_dotenv"), \
         patch.dict(os.environ, {}, clear=True):
        try:
            load_config(reload=True)
            assert False, "Expected ValueError when secrets are completely missing"
        except ValueError as e:
            assert "Missing required configuration/secrets" in str(e)

    print("PASS: test_load_config_missing_raises")


if __name__ == "__main__":
    test_fetch_ssm_parameters_success()
    test_load_config_ssm_precedence()
    test_load_config_fallback_to_env()
    test_load_config_missing_raises()
    print("\nALL CONFIG & SSM TESTS PASSED SUCCESSFULLY!")
