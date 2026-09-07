"""
Telegram alert notification module.
Formats summaries into HTML and delivers alerts with rate limiting and fallback handling.
"""

import html
import re
import time
from typing import Dict

import requests

from src.config import logger
from src.utils import clean_text


def format_telegram_summary(raw_summary: str) -> str:
    """Format and sanitize the summary text for Telegram HTML parse mode."""
    lines = [clean_text(line) for line in raw_summary.split("\n") if clean_text(line)]
    formatted_lines = []
    for line in lines:
        # Standardize bullet symbols
        line = re.sub(r"^[\*\-\•]\s*", "", line)
        line = re.sub(r"^\d+\.\s*", "", line)
        # Escape any HTML characters to prevent breaking Telegram's HTML mode
        escaped_line = html.escape(line)
        # Convert **bold** to <b>bold</b> safely after escaping
        escaped_line = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped_line)
        formatted_lines.append(f"• {escaped_line}")

    if not formatted_lines:
        return "• დეტალები იხილეთ ბმულზე"

    return "\n".join(formatted_lines[:3])


def send_telegram_alert(
    session: requests.Session,
    bot_token: str,
    chat_id: str,
    job: Dict[str, str],
    summary: str,
) -> bool:
    """
    Send formatted vacancy alert to Telegram using HTML parse mode.
    Handles rate limiting (HTTP 429) gracefully and falls back to plain text on 400 Bad Request.
    """
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    escaped_title = html.escape(job["title"])
    escaped_company = html.escape(job["company"])
    clean_summary = format_telegram_summary(summary)

    source = job.get("source", "jobsge")
    source_name = "LinkedIn" if source == "linkedin" else "jobs.ge"

    message_html = (
        f"🎯 <b>ახალი ვაკანსია:</b> {escaped_title}\n"
        f"🏢 <b>კომპანია:</b> {escaped_company}\n\n"
        f"📋 <b>მოკლე მიმოხილვა:</b>\n"
        f"{clean_summary}\n\n"
        f'🔗 <a href="{job["link"]}">ვაკანსიის ნახვა {source_name}-ზე</a>'
    )


    payload = {
        "chat_id": chat_id,
        "text": message_html,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = session.post(url, json=payload, timeout=15)
            response.encoding = "utf-8"
            if response.status_code == 200:
                result = response.json()
                if result.get("ok"):
                    logger.info(f"Telegram alert sent successfully for job ID {job['job_id']}")
                    return True

            if response.status_code == 429:
                retry_after = 5
                try:
                    retry_after = response.json().get("parameters", {}).get("retry_after", 5)
                except Exception:
                    pass
                logger.warning(f"Telegram rate limited (429). Sleeping {retry_after}s before retry...")
                time.sleep(retry_after)
                continue

            logger.error(f"Telegram API error {response.status_code}: {response.text}")
            # If Bad Request, try sending without HTML parse mode as fallback
            if response.status_code == 400:
                logger.info("Attempting plain text fallback delivery...")
                plain_payload = {
                    "chat_id": chat_id,
                    "text": (
                        f"🎯 ახალი ვაკანსია: {job['title']}\n"
                        f"🏢 კომპანია: {job['company']}\n\n"
                        f"📋 მოკლე მიმოხილვა:\n{summary}\n\n"
                        f"🔗 ბმული ({source_name}): {job['link']}"
                    ),
                    "disable_web_page_preview": True,

                }
                fallback_resp = session.post(url, json=plain_payload, timeout=15)
                fallback_resp.encoding = "utf-8"
                if fallback_resp.status_code == 200 and fallback_resp.json().get("ok"):
                    logger.info("Fallback alert sent successfully.")
                    return True

            time.sleep(2)

        except requests.RequestException as e:
            logger.error(f"Network error while sending Telegram message (attempt {attempt + 1}): {e}")
            time.sleep(2)

    return False
