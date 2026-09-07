"""
Unit tests for Gemini LLM evaluation, zero-delay execution,
and backoff retry handling under pay-as-you-go pricing (src/llm.py).
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from google.genai.errors import APIError

from src.llm import (
    call_gemini_with_retry,
    is_rate_limit_error,
    is_rejected_decision,
)


def test_call_gemini_success_no_sleep():
    """Verify that on success, call_gemini_with_retry returns immediately without sleeping."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "• ძირითადი მოვალეობა: მონაცემთა მოდელირება\n• სტეკი: Python, SQL"
    mock_client.models.generate_content.return_value = mock_response

    with patch("src.llm.time.sleep") as mock_sleep:
        result = call_gemini_with_retry(mock_client, prompt="Test prompt", model="gemini-3.8-flash")

    assert result == "• ძირითადი მოვალეობა: მონაცემთა მოდელირება\n• სტეკი: Python, SQL"
    assert mock_client.models.generate_content.call_count == 1
    # Crucial check: zero proactive sleeps
    assert mock_sleep.call_count == 0, f"Expected 0 sleep calls on success, got {mock_sleep.call_count}"
    print("PASS: test_call_gemini_success_no_sleep")


def test_call_gemini_rate_limit_retry():
    """Verify that a 429 rate limit triggers the new [2, 4, 8] backoff delay and succeeds on retry."""
    mock_client = MagicMock()
    
    # 429 on first call, success on second call
    error_429 = Exception("429 Resource has been exhausted (e.g. check quota).")
    mock_response = MagicMock()
    mock_response.text = "REJECT"
    mock_client.models.generate_content.side_effect = [error_429, mock_response]

    with patch("src.llm.time.sleep") as mock_sleep:
        result = call_gemini_with_retry(mock_client, prompt="Test prompt", model="gemini-3.8-flash")

    assert result == "REJECT"
    assert mock_client.models.generate_content.call_count == 2
    # Verify sleep was called with 2s (first backoff delay in [2, 4, 8])
    assert mock_sleep.call_count == 1
    mock_sleep.assert_called_once_with(2)
    print("PASS: test_call_gemini_rate_limit_retry")


def test_call_gemini_general_error_retry():
    """Verify that general errors retry with streamlined backoff 2 * (attempt + 1)."""
    mock_client = MagicMock()
    err = RuntimeError("Temporary network glitch")
    mock_response = MagicMock()
    mock_response.text = "Valid summary"
    mock_client.models.generate_content.side_effect = [err, mock_response]

    with patch("src.llm.time.sleep") as mock_sleep:
        result = call_gemini_with_retry(mock_client, prompt="Test prompt", model="gemini-3.8-flash")

    assert result == "Valid summary"
    assert mock_client.models.generate_content.call_count == 2
    assert mock_sleep.call_count == 1
    mock_sleep.assert_called_once_with(2)  # 2 * (0 + 1) = 2s
    print("PASS: test_call_gemini_general_error_retry")


def test_call_gemini_max_retries_exhausted():
    """Verify that exhausting all retries on 429 returns None."""
    mock_client = MagicMock()
    error_429 = Exception("429 ResourceExhausted")
    mock_client.models.generate_content.side_effect = error_429

    with patch("src.llm.time.sleep") as mock_sleep:
        result = call_gemini_with_retry(mock_client, prompt="Test prompt", max_retries=3)

    assert result is None
    # 4 attempts total: initial + 3 retries
    assert mock_client.models.generate_content.call_count == 4
    # 3 sleeps corresponding to [2, 4, 8]
    assert mock_sleep.call_count == 3
    expected_calls = [((2,),), ((4,),), ((8,),)]
    assert mock_sleep.call_args_list == expected_calls
    print("PASS: test_call_gemini_max_retries_exhausted")


def test_is_rate_limit_error():
    """Verify rate limit detection across exception types."""
    assert is_rate_limit_error(Exception("429 Quota exceeded")) is True
    assert is_rate_limit_error(Exception("RESOURCE_EXHAUSTED: Please check quota")) is True
    assert is_rate_limit_error(Exception("Rate limit reached")) is True
    
    mock_status_obj = MagicMock()
    mock_status_obj.code = 429
    assert is_rate_limit_error(mock_status_obj) is True

    assert is_rate_limit_error(Exception("500 Internal Server Error")) is False
    assert is_rate_limit_error(ValueError("Invalid argument")) is False
    print("PASS: test_is_rate_limit_error")


def test_is_rejected_decision():
    """Verify rejection parser handles different formats correctly."""
    assert is_rejected_decision("REJECT") is True
    assert is_rejected_decision("reject") is True
    assert is_rejected_decision("  REJECT  \n") is True
    assert is_rejected_decision("REJECT: Job is outside target domain.") is True
    assert is_rejected_decision("• ძირითადი მოვალეობა: BI") is False
    assert is_rejected_decision("") is False
    assert is_rejected_decision(None) is False
    print("PASS: test_is_rejected_decision")


if __name__ == "__main__":
    test_call_gemini_success_no_sleep()
    test_call_gemini_rate_limit_retry()
    test_call_gemini_general_error_retry()
    test_call_gemini_max_retries_exhausted()
    test_is_rate_limit_error()
    test_is_rejected_decision()
    print("\nALL LLM TESTS PASSED SUCCESSFULLY!")
