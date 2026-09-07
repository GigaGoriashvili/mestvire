"""
LLM evaluation and summarization module using Google Gemini API.
Includes prompt definitions, rate-limit backoff, and decision parsing.
"""

import time
from typing import Optional

from google import genai
from google.genai import types

from src.config import FILTER_SENIOR_ROLES, PRIMARY_MODEL, logger


def get_evaluation_system_prompt(filter_senior: bool = True) -> str:
    """Generate system prompt with optional Stage 2 Senior/Lead negative filter rule."""
    if filter_senior:
        return """You are a technical talent evaluator.
Target domains: Data Engineering, Data Analytics, Business Intelligence (BI), Data Warehousing (DWH), Big Data, and Databricks / Cloud Data Platforms.

Rules:
1. If the posting is clearly outside our target domains (e.g., Software Engineering / Web / Mobile Dev without core data pipeline focus, Machine Learning / AI Research, Cybersecurity/SOC, pure financial/credit risk analysis without data tech, QA testing, IT Helpdesk/Support, DevOps, SEO/Digital Marketing), respond with ONLY the word: REJECT.
2. If the job description explicitly demands a Senior, Lead, or Principal role, or strictly requires senior-level experience (e.g. 5+ years), respond with ONLY the word: REJECT.
3. If the posting matches our target domains (Junior, Mid-level, or unstated level) and is not rejected by previous rules, OR if it is ambiguous/hybrid (e.g., Systems Analyst requiring SQL/ETL, Backend Engineer with strong data pipeline focus), DO NOT reject it.
4. If NOT rejected, produce an ultra-concise summary in Georgian (2-3 short bullet points, strictly 1 brief line each):
   • ძირითადი მოვალეობა: [მაქსიმუმ 1 მოკლე წინადადება, მთავარი მიმართულება / მითითებული არ არის]
   • ტექნოლოგიური სტეკი: [მხოლოდ ძირითადი ხელსაწყოების ჩამონათვალი მძიმით, მაგ: Python, SQL, AWS, Airflow / მითითებული არ არის]
   • გამოცდილება: [მოკლედ: მაგ. 1-3 წელი / Junior / Middle / არ არის დაკონკრეტებული]

Strict Guidelines:
- Keep the summary minimal, compact, and token-efficient. Do not write full paragraphs or repeat detailed requirements—a direct link is provided.
- Base your summary strictly on the provided text without hallucinating tools or duties."""

    return """You are a technical talent evaluator.
Target domains: Data Engineering, Data Analytics, Business Intelligence (BI), Data Warehousing (DWH), Big Data, and Databricks / Cloud Data Platforms.

Rules:
1. If the posting is clearly outside our target domains (e.g., Software Engineering / Web / Mobile Dev without core data pipeline focus, Machine Learning / AI Research, Cybersecurity/SOC, pure financial/credit risk analysis without data tech, QA testing, IT Helpdesk/Support, DevOps, SEO/Digital Marketing), respond with ONLY the word: REJECT.
2. If the posting matches our target domains, OR if it is ambiguous/hybrid (e.g., Systems Analyst requiring SQL/ETL, Backend Engineer with strong data pipeline focus), DO NOT reject it.
3. If NOT rejected, produce an ultra-concise summary in Georgian (2-3 short bullet points, strictly 1 brief line each):
   • ძირითადი მოვალეობა: [მაქსიმუმ 1 მოკლე წინადადება, მთავარი მიმართულება / მითითებული არ არის]
   • ტექნოლოგიური სტეკი: [მხოლოდ ძირითადი ხელსაწყოების ჩამონათვალი მძიმით, მაგ: Python, SQL, AWS, Airflow / მითითებული არ არის]
   • გამოცდილება: [მოკლედ: მაგ. 3+ წელი / Senior / არ არის დაკონკრეტებული]

Strict Guidelines:
- Keep the summary minimal, compact, and token-efficient. Do not write full paragraphs or repeat detailed requirements—a direct link is provided.
- Base your summary strictly on the provided text without hallucinating tools or duties."""


EVALUATION_SYSTEM_PROMPT = get_evaluation_system_prompt(filter_senior=FILTER_SENIOR_ROLES)


def build_evaluation_prompt(
    title: str,
    company: str,
    details_text: str,
    filter_senior: Optional[bool] = None,
) -> str:
    """Build unified Stage 2 classification and concise summarization prompt."""
    if filter_senior is None:
        filter_senior = FILTER_SENIOR_ROLES

    system_prompt = get_evaluation_system_prompt(filter_senior=filter_senior)
    truncated_details = details_text[:3500] if len(details_text) > 3500 else details_text
    return (
        f"{system_prompt}\n\n"
        f"ვაკანსიის დასახელება: {title}\n"
        f"კომპანია: {company}\n\n"
        f"ვაკანსიის ტექსტი:\n"
        f"{truncated_details}\n"
    )


def is_rate_limit_error(e: Exception) -> bool:
    """Check if an exception represents an HTTP 429 / ResourceExhausted rate limit."""
    code = getattr(e, "code", None)
    if code == 429:
        return True

    status = getattr(e, "status", None)
    if status and "RESOURCE_EXHAUSTED" in str(status).upper():
        return True

    err_str = str(e).lower()
    return (
        "429" in err_str
        or "resource_exhausted" in err_str
        or "resourceexhausted" in err_str
        or "rate limit" in err_str
        or "quota" in err_str
    )


def call_gemini_with_retry(
    client: genai.Client,
    prompt: str,
    model: str = PRIMARY_MODEL,
    max_retries: int = 3,
) -> Optional[str]:
    """
    Call Gemini API without artificial free-tier delays (pay-as-you-go pricing).
    Includes streamlined exponential backoff retry on 429 rate limit or transient errors.
    Caps max_output_tokens to prevent unnecessary token generation costs while leaving headroom for thinking.
    """
    backoff_delays = [2, 4, 8]
    # Set max_output_tokens to 2048 to prevent truncation during thinking and summary generation
    config = types.GenerateContentConfig(
        temperature=0.0,
        max_output_tokens=2048,
    )

    for attempt in range(max_retries + 1):
        try:
            logger.info(f"Calling Gemini ({model}, attempt {attempt + 1}/{max_retries + 1})...")
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=config,
            )

            if response and response.text:
                return response.text.strip()
            return None

        except Exception as e:
            if is_rate_limit_error(e):
                if attempt < max_retries:
                    wait_time = backoff_delays[attempt] if attempt < len(backoff_delays) else backoff_delays[-1]
                    logger.warning(f"[RATE LIMIT HIT] Cooling down for {wait_time}s before retry...")
                    time.sleep(wait_time)
                    continue
                else:
                    logger.error(f"[RATE LIMIT HIT] Max retries ({max_retries}) exceeded for Gemini API. Skipping job.")
                    return None
            else:
                logger.error(f"Gemini API error ({type(e).__name__}): {e}")
                if attempt < max_retries:
                    wait_time = 2 * (attempt + 1)
                    logger.info(f"Retrying Gemini call after {wait_time}s due to error...")
                    time.sleep(wait_time)
                    continue
                else:
                    logger.error("All retries exhausted for Gemini API call. Skipping job.")
                    return None

    return None


def is_rejected_decision(response_text: str) -> bool:
    """Check if the LLM output is a REJECT decision."""
    if not response_text:
        return False
    cleaned = response_text.strip().upper()
    return cleaned == "REJECT" or cleaned.startswith("REJECT")
