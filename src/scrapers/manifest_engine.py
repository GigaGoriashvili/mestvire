"""
Declarative, manifest-driven scraping engine for verified company career pages.

Extracts job listings and vacancy details from ATS APIs, internal REST/GraphQL APIs,
and server-rendered static HTML as specified in data/companies_manifest.yaml.
Handles client-side/server-side location filtering, dot-notation JSON paths,
tiered CSS selectors, dynamic/browser fallbacks, and concurrent execution.
"""

import concurrent.futures
import hashlib
import json
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import yaml
from bs4 import BeautifulSoup

from src.config import USER_AGENT, logger
from src.utils import clean_text

# Candidate locations for companies_manifest.yaml
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_MANIFEST_CACHE: Optional[List[Dict[str, Any]]] = None

# Common keywords matching Georgia / Tbilisi / Remote roles
GEORGIA_LOCATION_KEYWORDS = [
    "georgia",
    "tbilisi",
    "batumi",
    "kutaisi",
    "rustavi",
    "remote",
    "თბილისი",
    "ბათუმი",
    "ქუთაისი",
    "რუსთავი",
    "საქართველო",
]


def get_manifest_path() -> Path:
    """Resolve the absolute path to companies_manifest.yaml across local and Lambda environments."""
    env_path = os.getenv("COMPANIES_MANIFEST_PATH")
    if env_path and Path(env_path).is_file():
        return Path(env_path)

    candidates = [
        PROJECT_ROOT / "data" / "companies_manifest.yaml",
        Path.cwd() / "data" / "companies_manifest.yaml",
        Path(__file__).resolve().parent.parent / "data" / "companies_manifest.yaml",
        Path("/var/task/data/companies_manifest.yaml"),  # AWS Lambda standard root
    ]

    for cand in candidates:
        if cand.is_file():
            return cand

    raise FileNotFoundError(
        f"companies_manifest.yaml not found. Checked: {[str(c) for c in candidates]}"
    )


def load_manifest(manifest_path: Optional[str] = None, reload: bool = False) -> List[Dict[str, Any]]:
    """
    Load and filter active company configurations from companies_manifest.yaml.

    Filter rules:
    - manifest_ready must be True
    - strategy_group must NOT be 'EXCLUDE'
    """
    global _MANIFEST_CACHE
    if not reload and _MANIFEST_CACHE is not None:
        return _MANIFEST_CACHE

    target_path = Path(manifest_path) if manifest_path else get_manifest_path()
    logger.info(f"Loading companies manifest from: {target_path}")

    with open(target_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    all_companies = data.get("companies", [])
    active_companies = [
        comp
        for comp in all_companies
        if comp.get("manifest_ready") is True and comp.get("strategy_group") != "EXCLUDE"
    ]

    logger.info(
        f"Loaded manifest: {len(all_companies)} total, "
        f"{len(active_companies)} active companies selected."
    )
    _MANIFEST_CACHE = active_companies
    return active_companies


def get_company_manifest(company_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve the manifest configuration dictionary for a specific company ID."""
    companies = load_manifest()
    for comp in companies:
        if comp.get("id") == company_id:
            return comp
    return None


def get_active_company_ids() -> List[str]:
    """Return a list of all active company IDs from the manifest."""
    return [c["id"] for c in load_manifest() if "id" in c]


def clean_endpoint_url(url: str) -> str:
    """Clean endpoint string by trimming surrounding notes or parentheses."""
    if not url:
        return ""
    return url.strip().split()[0]


def extract_path(data: Any, path: Optional[str]) -> Any:
    """
    Traverse nested JSON structures using dot-notation and array indices.
    Examples:
      - 'data.jobs'
      - 'result.data.allVacancies.edges'
      - 'departments[0].name'
      - 'data.title[0].text'
      - '$' -> returns root data
    """
    if data is None:
        return None
    if path == "$":
        return data
    if not path:
        return None

    # Support comma-separated or pipe-separated fallback paths: e.g. "job_title_en, job_title_local"
    if "," in path or "|" in path:
        sub_paths = [p.strip() for p in re.split(r"[,|]", path) if p.strip()]
        for sp in sub_paths:
            val = extract_path(data, sp)
            if val is not None and val != "" and val != []:
                return val
        return None

    tokens = [t for t in re.split(r"\.|\[(\d+)\]", path) if t]
    current = data
    for token in tokens:
        if token.isdigit():
            idx = int(token)
            if isinstance(current, (list, tuple)) and 0 <= idx < len(current):
                current = current[idx]
            else:
                return None
        elif isinstance(current, dict):
            current = current.get(token)
        else:
            return None
        if current is None:
            return None
    return current


def stringify_value(val: Any) -> str:
    """Convert an extracted value (primitive, dict, or list) into a clean string."""
    if val is None:
        return ""
    if isinstance(val, str):
        return clean_text(val)
    if isinstance(val, (int, float, bool)):
        return str(val)
    if isinstance(val, list):
        items = []
        for v in val:
            if isinstance(v, dict):
                label = (
                    v.get("name")
                    or v.get("city")
                    or v.get("city_name")
                    or v.get("office")
                    or v.get("label")
                    or v.get("title")
                    or v.get("value")
                )
                items.append(str(label) if label is not None else str(v))
            else:
                items.append(str(v))
        return clean_text(", ".join(items))
    if isinstance(val, dict):
        label = (
            val.get("name")
            or val.get("city")
            or val.get("city_name")
            or val.get("office")
            or val.get("label")
            or val.get("title")
            or val.get("text")
        )
        return clean_text(str(label) if label is not None else str(val))
    return clean_text(str(val))


def matches_location_filter(job: Dict[str, Any], company: Dict[str, Any]) -> bool:
    """
    Apply location filtering to a job listing.
    - Local companies: default True unless foreign location explicitly present.
    - International companies: checks location string against Georgia/Tbilisi/Remote keywords or manifest value.
    """
    loc_filter = company.get("location_filter") or {}
    filter_type = loc_filter.get("type", "none")

    if filter_type in ("api_parameter", "api_payload", "url_parameter"):
        # Already filtered server-side
        return True

    category = company.get("category", "international")
    location_str = (job.get("location") or "").lower()
    title_str = (job.get("title") or "").lower()

    # Foreign indicators that explicitly signify a position outside Georgia
    foreign_indicators = [
        "armenia", "yerevan",
        "azerbaijan", "baku",
        "poland", "warsaw", "krakow",
        "germany", "berlin", "munich", "frankfurt", "hamburg", "cologne",
        "finland", "helsinki",
        "united kingdom", "london", "uk",
        "san jose", "costa rica", "colombo",
        "sweden", "stockholm",
        "hungary", "budapest",
        "israel", "ramat gan", "tel aviv",
        "greece", "athens",
        "czechia", "prague",
        "kazakhstan", "almaty",
        "albania", "tirana",
        "romania", "timisoara", "bucharest",
        "cyprus", "limassol", "nicosia",
        "spain", "madrid", "barcelona",
        "netherlands", "amsterdam",
        "austria", "wien", "vienna",
        "denmark", "aalborg", "aarhus", "copenhagen",
        "croatia", "zagreb",
        "usa", "united states",
    ]

    def is_foreign_loc(loc: str) -> bool:
        for f in foreign_indicators:
            if len(f) <= 3:
                if re.search(r"\b" + re.escape(f) + r"\b", loc):
                    return True
            else:
                if f in loc:
                    return True
        return False

    has_georgia_kw = any(
        g in location_str or g in title_str
        for g in ["georgia", "tbilisi", "batumi", "kutaisi", "rustavi", "თბილისი", "ბათუმი", "ქუთაისი", "საქართველო"]
    )

    # Local Georgian companies: accept by default unless explicitly located in another country
    if category == "local":
        if is_foreign_loc(location_str) and not has_georgia_kw:
            return False
        return True

    # International companies:
    # 1. Candidate texts from vacancy metadata ONLY (do not include filter_val)
    candidate_texts = [location_str, title_str]

    # 2. If location explicitly mentions a foreign indicator without Georgia keyword, reject
    if is_foreign_loc(location_str) and not has_georgia_kw:
        return False

    # 3. Build allowed keywords from manifest filter_val and GEORGIA_LOCATION_KEYWORDS
    allowed_keywords = set(GEORGIA_LOCATION_KEYWORDS)
    filter_val = str(loc_filter.get("value", "") or "").strip()
    if filter_val and filter_val.lower() != "null":
        for token in re.split(r"[,/|]+", filter_val):
            tok_clean = token.strip().lower()
            if tok_clean and len(tok_clean) > 1:
                allowed_keywords.add(tok_clean)

    # 4. Check whether vacancy metadata contains any allowed keyword
    for kw in allowed_keywords:
        if any(kw in text for text in candidate_texts):
            return True

    # If location is unspecified, permit if filter_type is none
    if not location_str and filter_type == "none":
        return True

    return False


def _scrape_ats_or_internal_api(session: requests.Session, company: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Scrape listings from ATS_API or INTERNAL_API endpoints."""
    endpoint = clean_endpoint_url(company.get("endpoint", ""))
    if not endpoint:
        logger.warning(f"[{company['id']}] Missing endpoint.")
        return []

    method = company.get("method", "GET").upper()
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
    }
    custom_headers = company.get("api", {}).get("headers") or {}
    headers.update(custom_headers)

    # Prepare query params / payload
    params: Dict[str, Any] = {}
    payload = company.get("api", {}).get("payload")
    loc_filter = company.get("location_filter") or {}

    if loc_filter.get("type") == "api_parameter":
        param_name = loc_filter.get("parameter")
        param_val = loc_filter.get("value")
        if param_name and param_val:
            params[param_name] = param_val

    try:
        if method == "POST":
            content_type = headers.get("Content-Type", "")
            if "application/json" in content_type and isinstance(payload, dict):
                response = session.post(endpoint, json=payload, headers=headers, params=params, timeout=15)
            elif isinstance(payload, dict):
                response = session.post(endpoint, data=payload, headers=headers, params=params, timeout=15)
            elif isinstance(payload, str):
                response = session.post(endpoint, data=payload.encode("utf-8"), headers=headers, params=params, timeout=15)
            else:
                response = session.post(endpoint, headers=headers, params=params, timeout=15)
        else:
            response = session.get(endpoint, params=params, headers=headers, timeout=15)

        response.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"[{company['id']}] API request failed: {e}")
        return []

    # Attempt JSON parsing first
    try:
        data = response.json()
    except Exception:
        text = response.text
        data = None
        # 1. Check for embedded window.vacancies = [...] (Liberty Bank, etc.)
        vac_match = re.search(r"window\.vacancies\s*=\s*(\[.*?\]);", text, re.DOTALL)
        if vac_match:
            try:
                vac_list = json.loads(vac_match.group(1))
                data = {"window": {"vacancies": vac_list}, "vacancies": vac_list}
            except Exception:
                pass

        # 2. Check for embedded Phenom People state (Cisco phApp.ddo = {...};)
        if not data:
            ph_match = re.search(r"phApp\.ddo\s*=\s*(\{.*?\});", text, re.DOTALL)
            if ph_match:
                try:
                    ph_dict = json.loads(ph_match.group(1))
                    data = {"phApp": {"ddo": ph_dict}, **ph_dict}
                except Exception:
                    pass

        # If neither matched, parse as HTML partial (e.g. Glovo/WordPress admin-ajax)
        if not data:
            return _parse_html_partial(text, company)

    response_path = company.get("api", {}).get("response_path", "$")
    raw_items = extract_path(data, response_path)

    if raw_items is None:
        logger.debug(f"[{company['id']}] No items extracted at response_path: {response_path}")
        return []

    if isinstance(raw_items, dict):
        # Look for common array keys or values
        if "jobs" in raw_items and isinstance(raw_items["jobs"], list):
            items = raw_items["jobs"]
        elif "vacancies" in raw_items and isinstance(raw_items["vacancies"], list):
            items = raw_items["vacancies"]
        elif "data" in raw_items and isinstance(raw_items["data"], list):
            items = raw_items["data"]
        else:
            items = list(raw_items.values())
    elif isinstance(raw_items, list):
        items = raw_items
    else:
        items = [raw_items]

    mapping = company.get("mapping") or {}
    jobs: List[Dict[str, Any]] = []
    seen_ids = set()

    for item in items:
        if not isinstance(item, dict):
            continue

        title = stringify_value(extract_path(item, mapping.get("title")))
        if not title:
            continue

        raw_id = stringify_value(extract_path(item, mapping.get("job_id")))
        location = stringify_value(extract_path(item, mapping.get("location")))
        raw_url = stringify_value(extract_path(item, mapping.get("job_url")) or extract_path(item, mapping.get("apply_url")))
        description = stringify_value(extract_path(item, mapping.get("description")))
        dept = stringify_value(extract_path(item, mapping.get("department")))
        emp_type = stringify_value(extract_path(item, mapping.get("employment_type")))
        posted_at = stringify_value(extract_path(item, mapping.get("posted_at")))

        # Resolve full link
        if raw_url:
            full_link = urllib.parse.urljoin(company.get("source_url") or endpoint, raw_url)
        else:
            full_link = company.get("source_url") or endpoint

        if full_link.startswith("//"):
            full_link = f"https:{full_link}"

        # Deterministic job ID fallback
        if not raw_id:
            raw_id = hashlib.sha256(f"{company['id']}:{title}:{full_link}".encode("utf-8")).hexdigest()[:16]

        job_id_str = str(raw_id)
        if job_id_str in seen_ids:
            continue
        seen_ids.add(job_id_str)

        # SmartRecruiters candidate portal public web URL generation
        ats_platform = (company.get("ats") or {}).get("platform", "")
        is_smartrecruiters = (
            ats_platform.lower() == "smartrecruiters"
            or "api.smartrecruiters.com" in raw_url
            or ("api.smartrecruiters.com" in endpoint and ("api.smartrecruiters.com" in full_link or not raw_url))
        )
        api_url = raw_url if "api.smartrecruiters.com" in raw_url else None
        if is_smartrecruiters:
            if item.get("postingUrl"):
                full_link = item["postingUrl"]
            else:
                company_identifier = ""
                if isinstance(item.get("company"), dict) and item["company"].get("identifier"):
                    company_identifier = item["company"]["identifier"]
                if not company_identifier:
                    m = re.search(r"companies/([^/]+)", endpoint)
                    if m:
                        company_identifier = m.group(1)
                if not company_identifier and raw_url:
                    m = re.search(r"companies/([^/]+)", raw_url)
                    if m:
                        company_identifier = m.group(1)
                if not company_identifier and company.get("id") == "tbc_bank":
                    company_identifier = "TBCBANK"

                if company_identifier and job_id_str:
                    full_link = f"https://jobs.smartrecruiters.com/{company_identifier}/{job_id_str}"

        job = {
            "job_id": job_id_str,
            "title": title,
            "company": company.get("name", company["id"]),
            "location": location,
            "link": full_link,
            "description": description,
            "source": company["id"],
            "department": dept,
            "employment_type": emp_type,
            "published_date": posted_at,
        }
        if api_url:
            job["api_url"] = api_url

        if matches_location_filter(job, company):
            jobs.append(job)

    logger.info(f"[{company['id']}] Extracted {len(jobs)} jobs via API.")
    return jobs


def _parse_html_partial(html_content: str, company: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse HTML partials returned from API endpoints (e.g. Glovo WordPress admin-ajax)."""
    soup = BeautifulSoup(html_content, "html.parser")
    selectors = company.get("selectors") or {}
    mapping = company.get("mapping") or {}

    container_sel = selectors.get("container") or ".job-card, tr, article, li"
    containers = soup.select(container_sel)
    if not containers:
        containers = soup.find_all("tr") or soup.find_all("li")

    jobs: List[Dict[str, Any]] = []
    seen_ids = set()
    for card in containers:
        link_el = card.find("a", href=True)
        href = link_el["href"].strip() if link_el else ""
        if not href or href.startswith("javascript:") or href.startswith("#"):
            continue
        full_url = urllib.parse.urljoin(company.get("source_url") or "", href) if href else company.get("source_url", "")

        title_sel = mapping.get("title") or selectors.get("title", "h3, h4, .title, a")
        title_el = card.select_one(title_sel) or link_el
        title = clean_text(title_el.get_text(separator=" ", strip=True)) if title_el else ""
        if not title:
            continue

        loc_sel = mapping.get("location") or selectors.get("location")
        loc_el = card.select_one(loc_sel) if loc_sel else None
        location = clean_text(loc_el.get_text(separator=" ", strip=True)) if loc_el else ""

        job_id = href or hashlib.sha256(f"{company['id']}:{title}".encode("utf-8")).hexdigest()[:16]
        job_id_str = str(job_id)
        if job_id_str in seen_ids:
            continue
        seen_ids.add(job_id_str)

        job = {
            "job_id": job_id_str,
            "title": title,
            "company": company.get("name", company["id"]),
            "location": location,
            "link": full_url,
            "description": "",
            "source": company["id"],
        }
        if matches_location_filter(job, company):
            jobs.append(job)

    return jobs


def _scrape_innowise(session: requests.Session, company: Dict[str, Any], headers: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Direct lightweight scraper for Innowise Group.
    Dynamically discovers and parses vacancies_data from WordPress LiteSpeed Cache JS bundle.
    """
    endpoint = clean_endpoint_url(company.get("endpoint", "https://innowise.com/careers/"))
    try:
        response = session.get(endpoint, headers=headers, timeout=15)
        response.encoding = "utf-8"
        response.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"[{company['id']}] Failed to load careers page: {e}")
        return []

    page_html = response.text
    js_text = ""
    v_match = re.search(r"vacancies_data\s*=\s*(\{.*?\});", page_html, re.DOTALL)
    if v_match:
        js_text = page_html
    else:
        soup = BeautifulSoup(page_html, "html.parser")
        candidate_scripts = []
        for s in soup.find_all("script"):
            src = s.get("data-src") or s.get("src")
            if src and "wp-content/litespeed/js/" in src:
                candidate_scripts.append(src)

        if not candidate_scripts:
            found = re.findall(r'(?:src|data-src)=["\']([^"\']*wp-content/litespeed/js/[^"\']+)["\']', page_html)
            candidate_scripts.extend(found)

        for script_url in candidate_scripts:
            full_script_url = urllib.parse.urljoin("https://innowise.com", script_url)
            try:
                s_resp = session.get(full_script_url, headers=headers, timeout=15)
                s_resp.encoding = "utf-8"
                if "vacancies_data" in s_resp.text:
                    js_text = s_resp.text
                    break
            except Exception:
                continue

    if not js_text:
        logger.warning(f"[{company['id']}] Could not locate vacancies_data in page or scripts.")
        return []

    v_idx = js_text.find("vacancies_data")
    if v_idx == -1:
        return []

    start_idx = js_text.find("{", v_idx)
    brace_count = 0
    end_idx = start_idx
    for i in range(start_idx, len(js_text)):
        if js_text[i] == "{":
            brace_count += 1
        elif js_text[i] == "}":
            brace_count -= 1
            if brace_count == 0:
                end_idx = i + 1
                break

    raw_v = js_text[start_idx:end_idx]

    loc_map: Dict[str, str] = {
        "all": "All locations",
        "georgia": "Georgia",
        "poland": "Poland",
        "warsaw": "Warsaw",
        "germany": "Germany",
        "lithuania": "Lithuania",
    }
    loc_def = re.search(r"locationData\s*=\s*(\{.*?\});", js_text, re.DOTALL)
    if loc_def:
        for pair in re.finditer(r'(\w+)\s*:\s*["\']([^"\']+)["\']', loc_def.group(1)):
            loc_map[pair.group(1).lower()] = pair.group(2)

    item_pattern = re.compile(r"\{[^{}]*name:\s*[\'\"]([^\'\"]+)[\'\"][^{}]*\}")
    jobs: List[Dict[str, Any]] = []
    seen_ids = set()

    for m in item_pattern.finditer(raw_v):
        block = m.group(0)
        name_m = re.search(r"name:\s*[\'\"]([^\'\"]+)[\'\"]", block)
        url_m = re.search(r"url:\s*[\'\"]([^\'\"]+)[\'\"]", block)
        loc_m = re.search(r"location:\s*\[([^\]]+)\]", block)
        level_m = re.search(r"level:\s*[\'\"]([^\'\"]+)[\'\"]", block)

        if not name_m or not url_m:
            continue

        title = clean_text(name_m.group(1))
        rel_url = url_m.group(1).strip()
        full_url = urllib.parse.urljoin("https://innowise.com", rel_url)
        level = clean_text(level_m.group(1)) if level_m else ""
        loc_raw = loc_m.group(1).lower() if loc_m else ""

        locations = []
        for key, val in loc_map.items():
            if f"locationdata.{key}" in loc_raw or key in loc_raw:
                locations.append(val)

        loc_str = ", ".join(locations) if locations else ""
        job_id = rel_url.strip("/").split("/")[-1] or hashlib.sha256(f"innowise:{title}".encode("utf-8")).hexdigest()[:16]
        if job_id in seen_ids:
            continue
        seen_ids.add(job_id)

        job = {
            "job_id": job_id,
            "title": title,
            "company": company.get("name", "Innowise Group"),
            "location": loc_str,
            "link": full_url,
            "description": "",
            "source": company["id"],
        }
        if matches_location_filter(job, company):
            jobs.append(job)

    logger.info(f"[{company['id']}] Extracted {len(jobs)} jobs via direct script extraction.")
    return jobs


def _scrape_static_html(session: requests.Session, company: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Scrape listings from server-rendered STATIC_HTML career boards."""
    endpoint = clean_endpoint_url(company.get("endpoint", ""))
    if not endpoint:
        logger.warning(f"[{company['id']}] Missing static HTML endpoint.")
        return []

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    custom_headers = (company.get("api") or {}).get("headers") or {}
    headers.update(custom_headers)

    # Innowise specialized direct bundle parser
    if company.get("id") == "innowise":
        return _scrape_innowise(session, company, headers)

    params: Dict[str, Any] = {}
    loc_filter = company.get("location_filter") or {}

    if loc_filter.get("type") == "api_parameter":
        param_name = loc_filter.get("parameter")
        param_val = loc_filter.get("value")
        if param_name and param_val:
            params[param_name] = param_val

    pagination = company.get("pagination") or {}
    is_offset_pagination = pagination.get("type") == "offset"
    offset_param = (pagination.get("parameter") or {}).get("offset_parameter", "startrow")
    step = int((pagination.get("parameter") or {}).get("step", 5))
    max_pages = int((pagination.get("parameter") or {}).get("max_pages", 10))

    page_limit = max_pages if is_offset_pagination else 1
    jobs: List[Dict[str, Any]] = []
    seen_ids = set()

    for page_idx in range(page_limit):
        req_params = dict(params)
        if is_offset_pagination:
            req_params[offset_param] = page_idx * step

        try:
            response = session.get(endpoint, params=req_params, headers=headers, timeout=15)
            response.encoding = "utf-8"
            response.raise_for_status()
        except requests.RequestException as e:
            logger.warning(f"[{company['id']}] Static HTML fetch failed (page {page_idx}): {e}")
            break

        soup = BeautifulSoup(response.text, "html.parser")
        selectors = company.get("selectors") or {}
        container_sel = selectors.get("container")
        if not container_sel:
            logger.warning(f"[{company['id']}] Missing container selector.")
            break

        cards = soup.select(container_sel)
        if not cards:
            break

        new_on_page = 0
        page_jobs: List[Dict[str, Any]] = []
        for card in cards:
            # Title
            title_sel = selectors.get("title", "h2, h3, h4, a")
            title_el = card.select_one(title_sel)
            if not title_el and (card.name == "a" or title_sel == "a" or title_sel == card.name):
                title_el = card
            title = clean_text(title_el.get_text(separator=" ", strip=True)) if title_el else ""
            if not title:
                continue

            # Link & Job ID
            link_sel = selectors.get("link", "a")
            link_el = card.select_one(link_sel) if link_sel else None
            if not link_el and link_sel and link_sel != "null":
                link_el = card.find("a", href=True)
            if not link_el and card.name == "a" and card.has_attr("href"):
                link_el = card

            href = link_el.get("href") if link_el else ""
            full_url = urllib.parse.urljoin(company.get("source_url") or endpoint, href) if href else company.get("source_url", endpoint)

            # Theneo anchor slug generation for modal popup vacancies
            if company.get("id") == "theneo" and (not href or href == "#" or full_url == endpoint):
                slug = re.sub(r"[^a-zA-Z0-9]+", "-", title.lower()).strip("-")
                full_url = f"{endpoint}#{slug}"

            id_key = selectors.get("job_id")
            if id_key and card.has_attr(id_key):
                job_id = card[id_key]
            elif id_key == "href" and href:
                job_id = href
            elif id_key and id_key != "href":
                id_el = card.select_one(id_key)
                job_id = clean_text(id_el.get_text(separator=" ", strip=True)) if id_el else None
            else:
                job_id = None

            # Safeguard 1: title + full_url in hash to avoid modal job ID collision
            if not job_id:
                job_id = hashlib.sha256(f"{company['id']}:{title}:{full_url}".encode("utf-8")).hexdigest()[:16]

            job_id_str = str(job_id)
            if job_id_str in seen_ids:
                continue
            seen_ids.add(job_id_str)
            new_on_page += 1

            # Location
            loc_sel = selectors.get("location")
            loc_el = card.select_one(loc_sel) if loc_sel else None
            if not loc_el and loc_sel and (".link-item" in loc_sel or company.get("id") == "softteco"):
                loc_el = card
            location = clean_text(loc_el.get_text(separator=" ", strip=True)) if loc_el else ""

            # Description (if embedded)
            desc_sel = selectors.get("description")
            desc_el = card.select_one(desc_sel) if desc_sel else None
            description = clean_text(desc_el.get_text(separator="\n", strip=True)) if desc_el else ""

            # Date
            date_sel = selectors.get("date")
            date_el = card.select_one(date_sel) if date_sel else None
            posted_at = clean_text(date_el.get_text(separator=" ", strip=True)) if date_el else ""

            dept_sel = selectors.get("department")
            dept_el = card.select_one(dept_sel) if dept_sel else None
            dept = clean_text(dept_el.get_text(separator=" ", strip=True)) if dept_el else ""

            job = {
                "job_id": job_id_str,
                "title": title,
                "company": company.get("name", company["id"]),
                "location": location,
                "link": full_url,
                "description": description,
                "source": company["id"],
                "published_date": posted_at,
                "department": dept,
            }
            page_jobs.append(job)

        # Customertimes detail page location resolution
        if company.get("id") == "customertimes":
            def fetch_ct_georgia(j: Dict[str, Any]) -> None:
                link = j.get("link")
                if link and link != endpoint:
                    try:
                        r = session.get(link, headers=headers, timeout=8)
                        if "georgia" in r.text.lower():
                            j["location"] = "Georgia"
                    except Exception:
                        pass

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
                list(ex.map(fetch_ct_georgia, page_jobs))

        for j in page_jobs:
            if matches_location_filter(j, company):
                jobs.append(j)

        # Safeguard 5: termination condition for pagination loops
        if is_offset_pagination and new_on_page == 0:
            break

    logger.info(f"[{company['id']}] Extracted {len(jobs)} jobs via static HTML.")
    return jobs


def _scrape_dynamic_or_browser(session: requests.Session, company: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Handle DYNAMIC_HTML or BROWSER_ONLY sources gracefully.
    Attempts static HTML extraction first; if empty or blocked, gracefully skips.
    """
    try:
        jobs = _scrape_static_html(session, company)
        if jobs:
            return jobs
    except Exception as e:
        logger.debug(f"[{company['id']}] Static fallback failed for dynamic site: {e}")

    logger.info(
        f"[{company['id']}] Requires browser environment / Playwright "
        "(gracefully skipped in standard Lambda runtime)."
    )
    return []


def scrape_company_listings(session: requests.Session, company: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Primary dispatch function to scrape job listings for any company defined in the manifest.
    Routes to the appropriate handler based on company['strategy_group'].
    """
    strat = company.get("strategy_group")
    try:
        if strat in ("ATS_API", "INTERNAL_API"):
            return _scrape_ats_or_internal_api(session, company)
        elif strat == "STATIC_HTML":
            return _scrape_static_html(session, company)
        elif strat in ("DYNAMIC_HTML", "BROWSER_ONLY"):
            return _scrape_dynamic_or_browser(session, company)
        else:
            logger.warning(f"[{company.get('id')}] Unknown strategy group '{strat}'. Skipping.")
            return []
    except Exception as e:
        logger.exception(f"[{company.get('id')}] Unexpected error during scraping: {e}")
        return []


def fetch_company_job_details(
    session: requests.Session,
    company_id: str,
    job_id: str,
    job: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Fetch vacancy detail text for a company job.
    If the job dictionary already contains the description, returns it directly.
    Otherwise, attempts to fetch the job link and extract the main content.
    """
    if job and job.get("description"):
        desc = job["description"]
        if "<" in desc and ">" in desc:
            desc = clean_text(BeautifulSoup(desc, "html.parser").get_text(separator="\n", strip=True))
        if len(desc) >= 200:
            return desc

    link = job.get("link") if job else None
    if not link:
        if str(job_id).startswith("http"):
            link = str(job_id)
        elif str(job_id).startswith("/"):
            manifest = get_company_manifest(company_id)
            if manifest:
                base = manifest.get("source_url") or manifest.get("endpoint") or ""
                link = urllib.parse.urljoin(base, str(job_id))

    manifest = get_company_manifest(company_id) if company_id else None
    ats_platform = ((manifest.get("ats") or {}).get("platform") or "").lower() if manifest else ""
    m_endpoint = manifest.get("endpoint", "") if manifest else ""
    is_smartrecruiters = (
        ats_platform == "smartrecruiters"
        or "smartrecruiters.com" in m_endpoint
        or (bool(link) and "smartrecruiters.com" in link)
    )

    fetch_url = link
    if is_smartrecruiters:
        company_identifier = ""
        if job and isinstance(job.get("company"), dict) and job["company"].get("identifier"):
            company_identifier = job["company"]["identifier"]
        if not company_identifier and m_endpoint:
            m = re.search(r"companies/([^/]+)", m_endpoint)
            if m:
                company_identifier = m.group(1)
        if not company_identifier and link:
            m = re.search(r"smartrecruiters\.com/(?:v\d+/companies/)?([^/]+)", link)
            if m:
                company_identifier = m.group(1)
        if not company_identifier and company_id == "tbc_bank":
            company_identifier = "TBCBANK"

        api_url = job.get("api_url") if job else None
        if api_url and "api.smartrecruiters.com" in api_url:
            fetch_url = api_url
        elif company_identifier and job_id:
            fetch_url = f"https://api.smartrecruiters.com/v1/companies/{company_identifier}/postings/{job_id}"

    if company_id == "devexperts":
        fetch_url = f"https://api-careers.in.devexperts.com/v1/vacancies/{job_id}"

    if not fetch_url or not fetch_url.startswith("http"):
        return ""

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json, text/html, */*"}
    try:
        response = session.get(fetch_url, headers=headers, timeout=15)
        response.encoding = "utf-8"
        response.raise_for_status()
    except requests.RequestException as e:
        if link and fetch_url != link:
            try:
                response = session.get(link, headers=headers, timeout=15)
                response.encoding = "utf-8"
                response.raise_for_status()
            except requests.RequestException as err:
                logger.warning(f"[{company_id}] Failed to fetch job details for {job_id} ({link}): {err}")
                return ""
        else:
            logger.warning(f"[{company_id}] Failed to fetch job details for {job_id} ({fetch_url}): {e}")
            return ""

    # Check if response is JSON or contains a SmartRecruiters jobAd structure
    data = None
    try:
        data = response.json()
    except Exception:
        text = response.text.strip()
        if text.startswith("{") and "jobAd" in text:
            try:
                data = json.loads(text)
            except Exception:
                pass

    # Check if response is Devexperts internal API
    if company_id == "devexperts" and isinstance(data, dict):
        parts = []
        descs = data.get("descriptions", {})
        if isinstance(descs, dict):
            if descs.get("job"):
                parts.append(clean_text(BeautifulSoup(descs["job"], "html.parser").get_text(separator="\n", strip=True)))
            if descs.get("qualification"):
                parts.append("საკვალიფიკაციო მოთხოვნები:\n" + clean_text(BeautifulSoup(descs["qualification"], "html.parser").get_text(separator="\n", strip=True)))
            if descs.get("company"):
                parts.insert(0, clean_text(BeautifulSoup(descs["company"], "html.parser").get_text(separator="\n", strip=True)))
        add_info = data.get("additionalInformation", {})
        if isinstance(add_info, dict) and add_info.get("text"):
            parts.append("დამატებითი ინფორმაცია:\n" + clean_text(BeautifulSoup(add_info["text"], "html.parser").get_text(separator="\n", strip=True)))
        if parts:
            return "\n\n".join(parts)

    if isinstance(data, dict) and ("jobAd" in data or "sections" in data):
        job_ad = data.get("jobAd") if "jobAd" in data else data
        sections = job_ad.get("sections", {}) if isinstance(job_ad, dict) else {}

        jd_sec = sections.get("jobDescription", {})
        jd_html = jd_sec.get("text", "") if isinstance(jd_sec, dict) else (jd_sec if isinstance(jd_sec, str) else "")

        qual_sec = sections.get("qualifications", {})
        qual_html = qual_sec.get("text", "") if isinstance(qual_sec, dict) else (qual_sec if isinstance(qual_sec, str) else "")

        job_desc_text = clean_text(BeautifulSoup(jd_html, "html.parser").get_text(separator="\n", strip=True)) if jd_html else ""
        qualifications_text = clean_text(BeautifulSoup(qual_html, "html.parser").get_text(separator="\n", strip=True)) if qual_html else ""

        if job_desc_text and qualifications_text:
            return f"{job_desc_text}\n\nსაკვალიფიკაციო მოთხოვნები:\n{qualifications_text}"
        elif qualifications_text:
            return f"საკვალიფიკაციო მოთხოვნები:\n{qualifications_text}"
        elif job_desc_text:
            return job_desc_text

    soup = BeautifulSoup(response.text, "html.parser")

    # If HTML has SmartRecruiters sections (st-jobDescription / st-qualifications)
    st_jd = soup.find(id="st-jobDescription") or soup.find(class_=re.compile(r"st-jobDescription"))
    st_qual = soup.find(id="st-qualifications") or soup.find(class_=re.compile(r"st-qualifications"))
    if st_jd or st_qual:
        jd_t = clean_text(st_jd.get_text(separator="\n", strip=True)) if st_jd else ""
        qual_t = clean_text(st_qual.get_text(separator="\n", strip=True)) if st_qual else ""
        if jd_t and qual_t:
            return f"{jd_t}\n\nსაკვალიფიკაციო მოთხოვნები:\n{qual_t}"
        elif qual_t:
            return f"საკვალიფიკაციო მოთხოვნები:\n{qual_t}"
        elif jd_t:
            return jd_t

    # 1. Check for schema.org JobPosting JSON-LD (strictly JobPosting, ignoring dummy teasers and non-postings)
    ld_json_candidates: List[str] = []
    meta_ld = soup.find("meta", attrs={"name": "script:ld+json"})
    if meta_ld and meta_ld.get("content"):
        ld_json_candidates.append(meta_ld["content"])

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        if script.string:
            ld_json_candidates.append(script.string)

    dummy_patterns = [
        "visit the website to find more information",
        "visit our website to find more information",
        "contact us directly via an email",
        "contact us directly",
    ]

    for ld_text in ld_json_candidates:
        try:
            data = json.loads(ld_text)
            postings = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
            for item in postings:
                if isinstance(item, dict):
                    types = item.get("@type", [])
                    types = types if isinstance(types, list) else [types]
                    if "JobPosting" in types:
                        raw_desc = item.get("description", "")
                        if raw_desc:
                            desc_soup = BeautifulSoup(raw_desc, "html.parser")
                            clean_desc = clean_text(desc_soup.get_text(separator="\n", strip=True))
                            if "<" in clean_desc and ">" in clean_desc:
                                clean_desc = clean_text(BeautifulSoup(clean_desc, "html.parser").get_text(separator="\n", strip=True))
                            if any(p in clean_desc.lower() for p in dummy_patterns):
                                continue
                            if len(clean_desc) > 20:
                                return clean_desc
        except Exception:
            pass

    # 2. Decompose navigational/irrelevant tags for standard HTML pages
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        tag.decompose()

    # Step A: Primary candidate containers
    content_elem = soup.select_one(
        ".vacancy-container, .career-detail, .jobdescription, .vacancy-content, "
        ".job-description, .vacancy-description, [data-testid='job-description'], "
        ".single-vacancy, article, main"
    ) or soup.find("div", class_=re.compile(r"(?:vacancy|career)[-_](?:container|detail|content|body)"))

    if content_elem and len(clean_text(content_elem.get_text())) >= 20:
        return clean_text(content_elem.get_text(separator="\n", strip=True))

    # Step B: Section headings & Elementor/CMS parent container discovery (Innowise, etc.)
    heading_keywords = (
        "require",
        "need",
        "must have",
        "must-have",
        "plus",
        "what to do",
        "responsibilit",
        "qualificat",
        "skill",
        "about",
        "საკვალიფიკაციო",
        "მოვალეობები",
        "მოთხოვნები",
    )
    best_parent = None
    best_parent_len = 0
    for h in soup.find_all(["h2", "h3"]):
        ht = h.get_text().lower()
        if any(k in ht for k in heading_keywords):
            parent = h.find_parent(
                "div",
                class_=lambda c: c and any(k in c for k in ("e-con", "elementor-section", "elementor-widget-wrap", "content-section", "career-detail"))
            )
            if parent:
                p_text = clean_text(parent.get_text(separator="\n", strip=True))
                if len(p_text) >= 50 and len(p_text) > best_parent_len:
                    best_parent = parent
                    best_parent_len = len(p_text)

    if best_parent:
        return clean_text(best_parent.get_text(separator="\n", strip=True))

    # Step C: Fallback regex div containers with non-empty content verification
    for div in soup.find_all("div", class_=re.compile(r"desc|detail|content|body")):
        div_text = clean_text(div.get_text(separator="\n", strip=True))
        if len(div_text) >= 50:
            return div_text

    # Step D: Final whole-page text fallback
    full_text = clean_text(soup.get_text(separator="\n", strip=True))
    return full_text if len(full_text) >= 50 else ""


def scrape_all_companies_concurrent(
    session: requests.Session,
    companies: Optional[List[Dict[str, Any]]] = None,
    max_workers: int = 10,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Concurrently scrape job listings across all target companies using ThreadPoolExecutor.
    Enables all 37 company career boards to be scraped in ~5-10 seconds.
    """
    target_companies = companies if companies is not None else load_manifest()
    results: Dict[str, List[Dict[str, Any]]] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_company = {
            executor.submit(scrape_company_listings, session, comp): comp
            for comp in target_companies
        }
        for future in concurrent.futures.as_completed(future_to_company):
            comp = future_to_company[future]
            cid = comp["id"]
            try:
                jobs = future.result()
                results[cid] = jobs
            except Exception as e:
                logger.warning(f"[{cid}] Concurrency failure: {e}")
                results[cid] = []

    total_scraped = sum(len(j) for j in results.values())
    logger.info(
        f"Concurrent scraping complete: {len(results)} companies processed, "
        f"{total_scraped} total vacancies collected."
    )
    return results
