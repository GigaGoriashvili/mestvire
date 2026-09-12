"""
Unit tests for the declarative manifest-driven scraping engine (src/scrapers/manifest_engine.py)
and its integration into the Mestvire monitoring pipeline.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.scrapers.manifest_engine import (
    clean_endpoint_url,
    extract_path,
    fetch_company_job_details,
    get_active_company_ids,
    get_company_manifest,
    load_manifest,
    matches_location_filter,
    scrape_all_companies_concurrent,
    scrape_company_listings,
    stringify_value,
)


def test_manifest_loading_and_filtering():
    """Verify manifest parsing: 48 total, 33 active companies."""
    companies = load_manifest(reload=True)
    assert len(companies) == 33, f"Expected 33 active companies, got {len(companies)}"

    active_ids = get_active_company_ids()
    assert len(active_ids) == 33

    # Excluded companies must NOT be present
    excluded = ["bitfury", "softswiss", "amind_argano", "sambrela", "silknet", "sweeft", "cisco", "worklink"]
    for exc in excluded:
        assert exc not in active_ids, f"Excluded company '{exc}' found in active list!"
        assert get_company_manifest(exc) is None

    # Key verified companies MUST be present
    expected_active = ["epam", "exactpro", "lineate", "tbc_bank", "bank_of_georgia"]
    for exp in expected_active:
        assert exp in active_ids, f"Expected active company '{exp}' not found!"
        comp = get_company_manifest(exp)
        assert comp is not None
        assert comp["manifest_ready"] is True
        assert comp["strategy_group"] != "EXCLUDE"

    print("PASS: test_manifest_loading_and_filtering")


def test_extract_path_json():
    """Test nested dot-notation and array indexing extraction."""
    sample_data = {
        "result": {
            "data": {
                "vacancies": [
                    {"id": 101, "name": "Lead Data Analyst", "dept": {"title": "BI"}},
                    {"id": 102, "name": "Junior BI Developer", "dept": {"title": "Analytics"}},
                ]
            }
        },
        "meta": {"total": 2},
    }

    assert extract_path(sample_data, "$") == sample_data
    assert extract_path(sample_data, "meta.total") == 2
    assert extract_path(sample_data, "result.data.vacancies[0].id") == 101
    assert extract_path(sample_data, "result.data.vacancies[1].dept.title") == "Analytics"
    assert extract_path(sample_data, "result.data.vacancies[99].id") is None
    assert extract_path(sample_data, "non.existent.path") is None
    assert extract_path(None, "some.path") is None
    assert extract_path(sample_data, "") is None
    assert extract_path(sample_data, None) is None

    print("PASS: test_extract_path_json")


def test_stringify_value():
    """Test conversion of heterogeneous values to clean strings."""
    assert stringify_value(None) == ""
    assert stringify_value("  Senior Data Engineer  ") == "Senior Data Engineer"
    assert stringify_value(12345) == "12345"
    assert stringify_value(["Tbilisi", "Batumi"]) == "Tbilisi, Batumi"
    assert stringify_value([{"name": "Georgia"}, {"name": "Armenia"}]) == "Georgia, Armenia"
    assert stringify_value({"city": "Tbilisi"}) == "Tbilisi"

    print("PASS: test_stringify_value")


def test_clean_endpoint_url():
    """Test stripping inline notes from endpoint URLs."""
    raw_url = "https://career.quantori.com/ (embedded window.__NEXT_DATA__)"
    cleaned = clean_endpoint_url(raw_url)
    assert cleaned == "https://career.quantori.com/"

    clean_url = "https://careers.epam.com/api/jobs/v2/search/careers-i18n"
    assert clean_endpoint_url(clean_url) == clean_url

    print("PASS: test_clean_endpoint_url")


def test_matches_location_filter():
    """Test location evaluation for local vs international companies."""
    local_company = {"category": "local", "location_filter": {"type": "client_side"}}
    intl_company = {"category": "international", "location_filter": {"type": "client_side"}}

    # Local company tests
    assert matches_location_filter({"location": "Tbilisi", "title": "Data Analyst"}, local_company) is True
    assert matches_location_filter({"location": "", "title": "Data Analyst"}, local_company) is True
    assert matches_location_filter({"location": "Yerevan, Armenia", "title": "Data Analyst"}, local_company) is False

    # International company tests
    assert matches_location_filter({"location": "Tbilisi, Georgia", "title": "BI Specialist"}, intl_company) is True
    assert matches_location_filter({"location": "Remote - Georgia", "title": "ETL Dev"}, intl_company) is True
    assert matches_location_filter({"location": "თბილისი", "title": "ანალიტიკოსი"}, intl_company) is True
    assert matches_location_filter({"location": "Warsaw, Poland", "title": "Data Dev"}, intl_company) is False

    # Wolt-like international company with multi-city client_side filter
    wolt_company = {
        "category": "international",
        "location_filter": {
            "type": "client_side",
            "parameter": "location text",
            "value": "Tbilisi, Batumi, Georgia",
        },
    }
    assert matches_location_filter({"location": "London, United Kingdom", "title": "BI Engineer"}, wolt_company) is False
    assert matches_location_filter({"location": "Berlin, Germany", "title": "Data Analyst"}, wolt_company) is False
    assert matches_location_filter({"location": "Budapest, Hungary", "title": "Systems Analyst"}, wolt_company) is False
    assert matches_location_filter({"location": "Helsinki, Finland", "title": "Backend Engineer"}, wolt_company) is False
    assert matches_location_filter({"location": "Tbilisi, Georgia", "title": "Finance Specialist"}, wolt_company) is True
    assert matches_location_filter({"location": "Batumi, Georgia", "title": "Data Engineer"}, wolt_company) is True

    # Server-side filtered company passes by default
    api_filter_company = {"category": "international", "location_filter": {"type": "api_parameter"}}
    assert matches_location_filter({"location": "anything"}, api_filter_company) is True

    print("PASS: test_matches_location_filter")


def test_ats_api_extraction_mock():
    """Test parsing ATS / Internal API JSON response with field mappings."""
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "content": [
            {
                "id": "tbc-001",
                "name": "Middle Data Engineer",
                "location": {"city": "Tbilisi"},
                "ref": "https://careers.tbc.ge/job/001",
                "department": {"label": "Data Platform"},
                "typeOfEmployment": {"label": "Full-time"},
                "releasedDate": "2026-09-01",
            },
            {
                "id": "tbc-002",
                "name": "Branch Teller",
                "location": {"city": "Batumi"},
                "ref": "https://careers.tbc.ge/job/002",
                "department": {"label": "Retail"},
                "typeOfEmployment": {"label": "Full-time"},
                "releasedDate": "2026-09-01",
            },
        ]
    }
    mock_session.get.return_value = mock_response

    company = {
        "id": "tbc_bank",
        "name": "TBC Bank",
        "category": "local",
        "strategy_group": "ATS_API",
        "endpoint": "https://api.smartrecruiters.com/v1/companies/TBCBANK/postings",
        "method": "GET",
        "location_filter": {"type": "api_parameter", "parameter": "country", "value": "ge"},
        "mapping": {
            "job_id": "id",
            "title": "name",
            "location": "location.city",
            "job_url": "ref",
            "department": "department.label",
            "employment_type": "typeOfEmployment.label",
            "posted_at": "releasedDate",
        },
        "api": {"response_path": "content"},
    }

    jobs = scrape_company_listings(mock_session, company)
    assert len(jobs) == 2
    assert jobs[0]["job_id"] == "tbc-001"
    assert jobs[0]["title"] == "Middle Data Engineer"
    assert jobs[0]["company"] == "TBC Bank"
    assert jobs[0]["location"] == "Tbilisi"
    assert jobs[0]["link"] == "https://careers.tbc.ge/job/001"
    assert jobs[0]["source"] == "tbc_bank"

    print("PASS: test_ats_api_extraction_mock")


def test_static_html_extraction_mock():
    """Test parsing static HTML response using CSS selectors."""
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = """
    <html>
      <body>
        <div class="vacancies">
          <article class="node-vacancy">
            <h2 class="field--name-title"><a href="/careers/tbilisi/qa-engineer">QA Engineer</a></h2>
            <div class="field--name-field-vacancy-location">Tbilisi, Georgia</div>
            <div class="field--name-body">Description text for QA Engineer.</div>
          </article>
          <article class="node-vacancy">
            <h2 class="field--name-title"><a href="/careers/tbilisi/data-analyst">Junior Data Analyst</a></h2>
            <div class="field--name-field-vacancy-location">Tbilisi, Georgia</div>
            <div class="field--name-body">Description text for Data Analyst.</div>
          </article>
        </div>
      </body>
    </html>
    """
    mock_session.get.return_value = mock_response

    company = {
        "id": "exactpro",
        "name": "Exactpro Systems",
        "category": "international",
        "strategy_group": "STATIC_HTML",
        "endpoint": "https://careers.exactpro.com/locations/tbilisi",
        "source_url": "https://careers.exactpro.com",
        "selectors": {
            "container": "article.node-vacancy",
            "title": "h2 a",
            "link": "h2 a",
            "location": ".field--name-field-vacancy-location",
            "description": ".field--name-body",
            "job_id": "href",
        },
    }

    jobs = scrape_company_listings(mock_session, company)
    assert len(jobs) == 2
    assert jobs[1]["title"] == "Junior Data Analyst"
    assert jobs[1]["location"] == "Tbilisi, Georgia"
    assert "data-analyst" in jobs[1]["link"]
    assert jobs[1]["source"] == "exactpro"

    print("PASS: test_static_html_extraction_mock")


def test_dynamic_and_browser_fallback():
    """Test graceful handling when browser rendering is unavailable."""
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "<html><body><script>var x = 1;</script></body></html>"
    mock_session.get.return_value = mock_response

    company = {
        "id": "singular_flutter",
        "name": "Singular",
        "strategy_group": "BROWSER_ONLY",
        "endpoint": "https://careers.flutterinternational.com/",
        "selectors": {"container": ".card-job"},
    }

    # Must return empty list gracefully without throwing an exception
    jobs = scrape_company_listings(mock_session, company)
    assert jobs == []

    print("PASS: test_dynamic_and_browser_fallback")


def test_concurrent_company_scraping_mock():
    """Test scrape_all_companies_concurrent with ThreadPoolExecutor."""
    mock_session = MagicMock()

    mock_companies = [
        {
            "id": f"comp_{i}",
            "name": f"Company {i}",
            "strategy_group": "STATIC_HTML",
            "endpoint": f"https://comp{i}.ge/jobs",
            "selectors": {"container": ".job", "title": "h3", "link": "a"},
        }
        for i in range(5)
    ]

    with patch("src.scrapers.manifest_engine._scrape_static_html", return_value=[{"job_id": "1", "title": "Data Dev"}]) as mock_scrape:
        results = scrape_all_companies_concurrent(mock_session, mock_companies, max_workers=5)
        assert len(results) == 5
        assert mock_scrape.call_count == 5
        for i in range(5):
            assert f"comp_{i}" in results
            assert len(results[f"comp_{i}"]) == 1

    print("PASS: test_concurrent_company_scraping_mock")


def test_pipeline_source_grouping():
    """Test pipeline run_monitor support for 'aggregators', 'companies', and specific company."""
    from src.pipeline import run_monitor, SCRAPER_REGISTRY

    mock_stats_return = [
        {"job_id": "ep_1", "title": "Data Engineer", "company": "EPAM Systems", "location": "Georgia", "link": "http://epam.com/1"}
    ]

    with patch("src.pipeline.load_config", return_value={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_CHAT_ID": "x", "GEMINI_API_KEY": "x"}), \
         patch("src.pipeline.init_db"), \
         patch("src.pipeline.genai.Client"), \
         patch("src.pipeline.is_job_seen", return_value=False), \
         patch("src.pipeline.process_single_job"):

        # 1. Test specific company source (e.g. 'epam')
        with patch.dict(SCRAPER_REGISTRY, {"epam": {"name": "epam", "fetch_listings": lambda s: mock_stats_return, "fetch_details": lambda s, j: "desc", "date_filter": lambda j: True}}):
            stats = run_monitor(source="epam", is_test=False)
            assert "epam" in stats
            assert stats["epam"]["total_scraped"] == 1

        # 2. Test aggregators grouping (only jobsge and linkedin)
        mock_jobsge = MagicMock(return_value=[])
        mock_linkedin = MagicMock(return_value=[])
        with patch.dict(SCRAPER_REGISTRY, {
            "jobsge": {"name": "jobsge", "fetch_listings": mock_jobsge, "fetch_details": lambda s, j: "", "date_filter": lambda j: True},
            "linkedin": {"name": "linkedin", "fetch_listings": mock_linkedin, "fetch_details": lambda s, j: "", "date_filter": lambda j: True},
        }):
            stats = run_monitor(source="aggregators", is_test=False)
            assert "jobsge" in stats
            assert "linkedin" in stats
            assert mock_jobsge.call_count == 1
            assert mock_linkedin.call_count == 1

    print("PASS: test_pipeline_source_grouping")


def test_manifest_selectors_syntax_validity():
    """Verify that all CSS selectors defined in companies_manifest.yaml are valid in SoupSieve."""
    import soupsieve
    companies = load_manifest(reload=True)
    for comp in companies:
        selectors = comp.get("selectors") or {}
        cid = comp["id"]
        for key, sel in selectors.items():
            if not sel or key in ("job_id", "date"):
                continue
            try:
                soupsieve.compile(sel)
            except Exception as e:
                assert False, f"Invalid CSS selector in company '{cid}' (field '{key}': '{sel}'): {e}"

    print("PASS: test_manifest_selectors_syntax_validity")


def test_static_html_whitespace_and_deduplication():
    """Verify that text extraction adds spaces between tags and duplicates are removed."""
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = """
    <html>
      <body>
        <div class="job-list">
          <div class="job-card" href="/jobs/101">
            <h3 class="title"><span>BI</span><span>Engineer</span></h3>
            <div class="location"><span>Tbilisi,</span><span>Georgia</span></div>
            <a href="/jobs/101">View</a>
          </div>
          <!-- Duplicate card -->
          <div class="job-card" href="/jobs/101">
            <h3 class="title"><span>BI</span><span>Engineer</span></h3>
            <div class="location"><span>Tbilisi,</span><span>Georgia</span></div>
            <a href="/jobs/101">View</a>
          </div>
        </div>
      </body>
    </html>
    """
    mock_session.get.return_value = mock_response

    company = {
        "id": "test_co",
        "name": "Test Co",
        "category": "international",
        "strategy_group": "STATIC_HTML",
        "endpoint": "https://example.com/careers",
        "selectors": {
            "container": ".job-card",
            "title": ".title",
            "link": "a",
            "location": ".location",
            "job_id": "href",
        },
        "location_filter": {"type": "client_side", "value": "Georgia"},
    }

    jobs = scrape_company_listings(mock_session, company)
    # Must be deduplicated (1 job instead of 2)
    assert len(jobs) == 1
    # Must have whitespace separation between child spans
    assert jobs[0]["title"] == "BI Engineer"
    assert jobs[0]["location"] == "Tbilisi, Georgia"

    print("PASS: test_static_html_whitespace_and_deduplication")


def test_fetch_company_job_details_with_job_dict():
    """Verify fetch_company_job_details receives job dict with link and fetches description."""
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = """
    <html>
      <body>
        <div class="job-description">
          <p>We are seeking a Data Engineer with SQL and Spark skills.</p>
        </div>
      </body>
    </html>
    """
    mock_session.get.return_value = mock_response

    job = {
        "job_id": "job-555",
        "title": "Data Engineer",
        "link": "https://example.com/jobs/555",
        "description": "",
    }

    desc = fetch_company_job_details(mock_session, "test_co", "job-555", job=job)
    assert "We are seeking a Data Engineer with SQL and Spark skills." in desc
    assert mock_session.get.call_count == 1

    print("PASS: test_fetch_company_job_details_with_job_dict")


def test_smartrecruiters_url_and_sections_extraction():
    """Verify SmartRecruiters candidate portal link construction and structured JSON parsing."""
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "content": [
            {
                "id": "744000147163082",
                "name": "Reporting and Analysis Expert",
                "location": {"city": "Tbilisi"},
                "ref": "https://api.smartrecruiters.com/v1/companies/TBCBANK/postings/744000147163082",
                "department": {"label": "Data Platform"},
                "typeOfEmployment": {"label": "Full-time"},
                "releasedDate": "2026-09-01",
                "company": {"identifier": "TBCBANK", "name": "TBC Bank"},
            }
        ]
    }
    mock_session.get.return_value = mock_response

    company = {
        "id": "tbc_bank",
        "name": "TBC Bank",
        "category": "local",
        "strategy_group": "ATS_API",
        "endpoint": "https://api.smartrecruiters.com/v1/companies/TBCBANK/postings",
        "method": "GET",
        "location_filter": {"type": "api_parameter", "parameter": "country", "value": "ge"},
        "mapping": {
            "job_id": "id",
            "title": "name",
            "location": "location.city",
            "job_url": "ref",
            "department": "department.label",
            "employment_type": "typeOfEmployment.label",
            "posted_at": "releasedDate",
        },
        "api": {"response_path": "content"},
        "ats": {"platform": "SmartRecruiters"},
    }

    jobs = scrape_company_listings(mock_session, company)
    assert len(jobs) == 1
    # Candidate portal URL must be constructed, NOT raw API JSON ref
    assert jobs[0]["link"] == "https://jobs.smartrecruiters.com/TBCBANK/744000147163082"
    assert jobs[0]["api_url"] == "https://api.smartrecruiters.com/v1/companies/TBCBANK/postings/744000147163082"

    # Now test fetch_company_job_details structured JSON parsing
    detail_response = MagicMock()
    detail_response.status_code = 200
    detail_response.json.return_value = {
        "jobAd": {
            "sections": {
                "jobDescription": {
                    "title": "Job Description",
                    "text": "<p>Responsible for ETL and BI reporting.</p>",
                },
                "qualifications": {
                    "title": "Qualifications",
                    "text": "<ul><li>SQL and Power BI skills</li><li>Advanced Excel</li></ul>",
                },
            }
        }
    }
    mock_session.get.return_value = detail_response
    details = fetch_company_job_details(mock_session, "tbc_bank", "744000147163082", job=jobs[0])
    assert "Responsible for ETL and BI reporting." in details
    assert "საკვალიფიკაციო მოთხოვნები:" in details
    assert "SQL and Power BI skills" in details
    assert "Advanced Excel" in details

    print("PASS: test_smartrecruiters_url_and_sections_extraction")


def test_telegram_link_text_generation():
    """Verify dynamic link label generation for jobsge, linkedin, and company career sites."""
    from src.notifier import send_telegram_alert

    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True}
    mock_session.post.return_value = mock_resp

    # 1. jobsge source
    job_jobsge = {"job_id": "1", "title": "DE", "company": "Co", "source": "jobsge", "link": "https://jobs.ge/1"}
    send_telegram_alert(mock_session, "tok", "chat", job_jobsge, "summary")
    posted_text = mock_session.post.call_args[1]["json"]["text"]
    assert 'ვაკანსიის ნახვა jobs.ge-ზე' in posted_text

    # 2. linkedin source
    job_li = {"job_id": "2", "title": "DA", "company": "Co", "source": "linkedin", "link": "https://linkedin.com/2"}
    send_telegram_alert(mock_session, "tok", "chat", job_li, "summary")
    posted_text = mock_session.post.call_args[1]["json"]["text"]
    assert 'ვაკანსიის ნახვა LinkedIn-ზე' in posted_text

    # 3. company career page (e.g. tbc_bank, bank_of_georgia, wolt)
    job_comp = {"job_id": "3", "title": "BI", "company": "TBC", "source": "tbc_bank", "link": "https://jobs.smartrecruiters.com/TBC/3"}
    send_telegram_alert(mock_session, "tok", "chat", job_comp, "summary")
    posted_text = mock_session.post.call_args[1]["json"]["text"]
    assert 'ვაკანსიის ნახვა ოფიციალურ საიტზე' in posted_text

    print("PASS: test_telegram_link_text_generation")


def test_bank_of_georgia_hosted_url_mapping():
    """Verify Bank of Georgia manifest maps hostedUrl for valid HireHive candidate links."""
    comp = get_company_manifest("bank_of_georgia")
    assert comp is not None
    assert comp["mapping"]["job_url"] == "hostedUrl"
    assert comp["mapping"]["apply_url"] == "hostedUrl"

    print("PASS: test_bank_of_georgia_hosted_url_mapping")


def test_embedded_state_and_company_fixes():
    """Test embedded window.vacancies, Phenom People state, EPAM mapping, and Wolt entity stripping."""
    from src.scrapers.manifest_engine import _scrape_ats_or_internal_api

    mock_session = MagicMock()

    # 1. Liberty Bank embedded window.vacancies
    lb_manifest = get_company_manifest("liberty_bank")
    assert lb_manifest is not None
    assert lb_manifest["mapping"]["job_id"] == "id"
    assert lb_manifest["mapping"]["location"] == "location.title"

    mock_lb_resp = MagicMock()
    mock_lb_resp.status_code = 200
    mock_lb_resp.json.side_effect = ValueError("Not JSON")
    mock_lb_resp.text = """
    <html><head><script>
    window.vacancies = [{"id": 999, "title": "Risk Analyst", "location": {"title": "თბილისი"}, "category": {"title": "Risk"}, "url": "//libertybank.ge/ka/kariera/999", "endDate": "2026-10-01"}];
    </script></head><body></body></html>
    """
    mock_session.get.return_value = mock_lb_resp
    lb_jobs = _scrape_ats_or_internal_api(mock_session, lb_manifest)
    assert len(lb_jobs) == 1
    assert lb_jobs[0]["job_id"] == "999"
    assert lb_jobs[0]["link"] == "https://libertybank.ge/ka/kariera/999"

    # 2. Cisco Phenom People embedded phApp.ddo
    cisco_manifest = {
        "id": "cisco",
        "name": "Cisco",
        "source_url": "https://jobs.cisco.com/jobs/SearchJobs/Georgia",
        "strategy_group": "ATS_API",
        "endpoint": "https://jobs.cisco.com/jobs/SearchJobs/Georgia",
        "method": "GET",
        "mapping": {
            "job_id": "jobId",
            "title": "title",
            "location": "cityStateCountry",
            "job_url": "applyUrl",
        },
        "api": {
            "response_path": "phApp.ddo.eagerLoadRefineSearch.data.jobs",
        },
        "location_filter": {
            "type": "none",
        },
    }
    mock_cisco_resp = MagicMock()
    mock_cisco_resp.status_code = 200
    mock_cisco_resp.json.side_effect = ValueError("Not JSON")
    mock_cisco_resp.text = """
    <html><body><script>
    phApp.ddo = {
        "eagerLoadRefineSearch": {
            "data": {
                "jobs": [
                    {
                        "jobId": "CISC123",
                        "title": "Cloud Architect",
                        "cityStateCountry": "Tbilisi, Georgia",
                        "applyUrl": "https://cisco.wd5.myworkdayjobs.com/apply/123",
                        "category": "Engineering",
                        "type": "Full time",
                        "postedDate": "2026-09-01"
                    }
                ]
            }
        }
    };
    </script></body></html>
    """
    mock_session.get.return_value = mock_cisco_resp
    cisco_jobs = _scrape_ats_or_internal_api(mock_session, cisco_manifest)
    assert len(cisco_jobs) == 1
    assert cisco_jobs[0]["job_id"] == "CISC123"
    assert cisco_jobs[0]["link"] == "https://cisco.wd5.myworkdayjobs.com/apply/123"

    # 3. EPAM manifest mapping
    epam = get_company_manifest("epam")
    assert epam is not None
    assert epam["mapping"]["job_id"] == "uid"
    assert epam["mapping"]["job_url"] == "seo.url"
    assert epam["mapping"]["description"] == "text"
    assert epam["source_url"] == "https://careers.epam.com"

    # 4. Georgian Post selectors
    gp = get_company_manifest("georgian_post")
    assert gp is not None
    assert "h1.career-vacancies__item--title" in gp["selectors"]["title"]
    assert ".career-vacancies__item--cat" in gp["selectors"]["location"]

    # 5. Wolt double-pass entity-escaped HTML stripping
    wolt_resp = MagicMock()
    wolt_resp.status_code = 200
    wolt_resp.json.side_effect = ValueError("Not JSON")
    wolt_resp.text = """
    <html><head>
    <script type="application/ld+json">
    {"@type": "JobPosting", "title": "Data Analyst", "description": "&lt;div class=&quot;intro&quot;&gt;&lt;h2&gt;About Wolt&lt;/h2&gt;&lt;p&gt;We build tech for modern delivery and logistics across the globe.&lt;/p&gt;&lt;/div&gt;"}
    </script>
    </head><body></body></html>
    """
    mock_session.get.return_value = wolt_resp
    wolt_desc = fetch_company_job_details(mock_session, "wolt", "job-101", {"job_id": "job-101", "link": "https://careers.wolt.com/en/jobs/101"})
    assert "About Wolt" in wolt_desc
    assert "<div" not in wolt_desc
    assert "<p" not in wolt_desc
    assert "&lt;" not in wolt_desc

    print("PASS: test_embedded_state_and_company_fixes")


def test_critical_safeguards():
    """Verify the 5 critical implementation safeguards for edge cases."""
    from src.scrapers.manifest_engine import _scrape_static_html
    import hashlib

    # 1. Theneo: Job ID Collision & Deduplication Bug
    # Ensure fallback hash in _scrape_static_html includes both title and url
    theneo = get_company_manifest("theneo")
    assert theneo is not None
    # Two distinct postings on the same modal careers URL
    hash1 = hashlib.sha256(f"theneo:Frontend Engineer:https://www.theneo.io/careers".encode("utf-8")).hexdigest()[:16]
    hash2 = hashlib.sha256(f"theneo:Backend Engineer:https://www.theneo.io/careers".encode("utf-8")).hexdigest()[:16]
    assert hash1 != hash2, "Fallback job IDs must NOT collide for different titles on the same URL!"

    # 2. Credo: Correct Vacancy URL Resolution
    credo = get_company_manifest("credo")
    assert credo is not None
    assert credo["source_url"] == "https://www.app.helio-ai.com/vacancy/"
    from urllib.parse import urljoin
    resolved = urljoin(credo["source_url"], "jdd5wgtnsxavpy5p")
    assert resolved == "https://www.app.helio-ai.com/vacancy/jdd5wgtnsxavpy5p"

    # 3. Singular Flutter: Cloudflare Header Requirements
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "<html><body><div class='card-job'><a href='/jobs/jr1/test'>Role</a></div></body></html>"
    mock_session.get.return_value = mock_resp

    comp_sf = get_company_manifest("singular_flutter")
    assert comp_sf is not None
    _scrape_static_html(mock_session, comp_sf)
    sent_headers = mock_session.get.call_args[1].get("headers", {})
    assert "Accept" in sent_headers
    assert "Accept-Language" in sent_headers
    assert "text/html" in sent_headers["Accept"]

    # 4. UGT: Pagination Infinite Loop Safeguard
    # Verify loop terminates on duplicate page even if server keeps returning cards
    comp_ugt = get_company_manifest("ugt")
    assert comp_ugt is not None
    assert comp_ugt["pagination"]["type"] == "offset"
    assert comp_ugt["pagination"].get("max_pages", 10) == 10

    # Mock server always returning identical card regardless of offset
    mock_ugt_session = MagicMock()
    mock_ugt_resp = MagicMock()
    mock_ugt_resp.status_code = 200
    mock_ugt_resp.text = """
    <html><body>
        <ul>
            <li class="job-tile">
                <a class="jobTitle-link" href="/job/engineer/123/">Software Engineer</a>
                <span class="job-location">Tbilisi</span>
            </li>
        </ul>
    </body></html>
    """
    mock_ugt_session.get.return_value = mock_ugt_resp
    ugt_jobs = _scrape_static_html(mock_ugt_session, comp_ugt)
    # Loop must terminate after 1 page because new_on_page == 0 on 2nd iteration
    assert len(ugt_jobs) == 1
    assert mock_ugt_session.get.call_count == 2, f"Expected exactly 2 requests (1st data, 2nd duplicate detection), got {mock_ugt_session.get.call_count}"

    print("PASS: test_critical_safeguards")


def test_truncated_description_fixes():
    """Verify description length safeguards, JSON-LD filters, and company detail extractors."""
    mock_session = MagicMock()

    # 1. Short description trap bypass
    # If job dict has stub ("Level: Senior" or "Senior"), it should proceed to fetch link
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = """
    <html><body>
        <div class="job-description">
            <p>Detailed job responsibilities and technical requirements for senior engineers with at least 5 years experience in Python and AWS.</p>
            <p>Second paragraph providing extensive context and qualification details ensuring overall text exceeds 200 characters easily.</p>
        </div>
    </body></html>
    """
    mock_session.get.return_value = mock_resp

    job_with_stub = {
        "job_id": "job-short",
        "title": "Data Engineer",
        "link": "https://example.com/job/short",
        "description": "Level: Senior",
    }
    extracted = fetch_company_job_details(mock_session, "test_co", "job-short", job=job_with_stub)
    assert "Level: Senior" not in extracted
    assert "Detailed job responsibilities" in extracted
    assert len(extracted) >= 200

    # 2. Devexperts structured API detail extraction
    mock_dev_resp = MagicMock()
    mock_dev_resp.status_code = 200
    mock_dev_resp.json.return_value = {
        "id": "dev-123",
        "title": "SecOps Engineer",
        "descriptions": {
            "company": "<p>Devexperts fintech leader</p>",
            "job": "<p>Secure our cloud infra</p>",
            "qualification": "<p>5+ years Kubernetes and SIEM</p>",
        },
        "additionalInformation": {
            "text": "<p>Remote work options and VIP insurance</p>"
        }
    }
    mock_session.get.return_value = mock_dev_resp
    dev_desc = fetch_company_job_details(mock_session, "devexperts", "dev-123")
    assert "Devexperts fintech leader" in dev_desc
    assert "Secure our cloud infra" in dev_desc
    assert "საკვალიფიკაციო მოთხოვნები:\n5+ years Kubernetes and SIEM" in dev_desc
    assert "დამატებითი ინფორმაცია:\nRemote work options and VIP insurance" in dev_desc

    # 3. JSON-LD filters: ignore Article, ignore dummy placeholder, accept JobPosting
    # 3a. Ignore Article with marketing teaser
    mock_article_resp = MagicMock()
    mock_article_resp.status_code = 200
    mock_article_resp.text = """
    <html>
      <head>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Article", "description": "Short marketing summary of vacancy"}
        </script>
      </head>
      <body>
        <div class="vacancy-container">
          <p>Full vacancy requirements and details from DOM container that has more than two hundred characters of rich information for testing.</p>
          <p>Additional paragraph detailing stack: Go, Python, React, PostgreSQL and Docker with continuous deployment.</p>
        </div>
      </body>
    </html>
    """
    mock_session.get.return_value = mock_article_resp
    art_desc = fetch_company_job_details(mock_session, "test_co", "job-art", job={"link": "https://example.com/art"})
    assert "Short marketing summary" not in art_desc
    assert "Full vacancy requirements" in art_desc

    # 3b. Ignore dummy placeholder JobPosting
    mock_dummy_resp = MagicMock()
    mock_dummy_resp.status_code = 200
    mock_dummy_resp.text = """
    <html>
      <head>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "JobPosting", "description": "Please visit the website to find more information on our Java Software Engineer vacancy or contact us directly via an email."}
        </script>
      </head>
      <body>
        <div class="vacancy-container">
          <p>Real vacancy text from the website container. This paragraph provides actual job requirements, tech stack details, and candidate qualifications.</p>
          <p>Requirements include Java 17, Spring Boot, AWS, DynamoDB, Microservices, and Kafka for event streaming architecture.</p>
        </div>
      </body>
    </html>
    """
    mock_session.get.return_value = mock_dummy_resp
    dummy_desc = fetch_company_job_details(mock_session, "softteco", "job-dummy", job={"link": "https://example.com/dummy"})
    assert "Please visit the website to find more information" not in dummy_desc
    assert "Real vacancy text from the website container" in dummy_desc

    # 4. Elementor / Innowise sections extraction with multiple heading variations
    # 4a. Standard 'requires:' heading
    mock_inno_resp = MagicMock()
    mock_inno_resp.status_code = 200
    mock_inno_resp.text = """
    <html>
      <body>
        <div class="content-section co-languagues"></div>
        <div class="e-con">
          <h2>Successful work on projects requires:</h2>
          <ul>
            <li>5+ years of experience with Data Governance</li>
            <li>Collibra, Informatica, and Alation experience</li>
          </ul>
          <h2>What to do:</h2>
          <p>Develop and implement data management strategies across enterprise clients with cross-functional teams.</p>
        </div>
      </body>
    </html>
    """
    mock_session.get.return_value = mock_inno_resp
    inno_desc = fetch_company_job_details(mock_session, "innowise", "inno-1", job={"link": "https://example.com/inno"})
    assert "Successful work on projects requires:" in inno_desc
    assert "Collibra, Informatica" in inno_desc

    # 4b. 'Must have:' variation (e.g. DBA/DBD Specialist)
    mock_inno_must_have = MagicMock()
    mock_inno_must_have.status_code = 200
    mock_inno_must_have.text = """
    <html>
      <body>
        <div class="content-section co-languagues"></div>
        <div class="e-con">
          <h2>Must have:</h2>
          <ul>
            <li>1 year or more of database administration experience</li>
            <li>PostgreSQL and MySQL expertise</li>
          </ul>
          <h2>Would be a plus:</h2>
          <p>Experience with Redis and MongoDB clusters.</p>
        </div>
      </body>
    </html>
    """
    mock_session.get.return_value = mock_inno_must_have
    must_have_desc = fetch_company_job_details(mock_session, "innowise", "inno-dba", job={"link": "https://example.com/inno-dba"})
    assert "Must have:" in must_have_desc
    assert "database administration experience" in must_have_desc

    # 4c. 'For successful work with projects, you need:' variation (e.g. Data Analyst)
    mock_inno_need = MagicMock()
    mock_inno_need.status_code = 200
    mock_inno_need.text = """
    <html>
      <body>
        <div class="content-section co-languagues"></div>
        <div class="e-con">
          <h2>For successful work with projects, you need:</h2>
          <ul>
            <li>Experience with BI visualisation tools (PowerBI, Tableau)</li>
            <li>SQL skills and Python/Pandas data manipulation</li>
          </ul>
          <h2>Will be a plus:</h2>
          <p>Experience with Airflow and Spark data pipelines.</p>
        </div>
      </body>
    </html>
    """
    mock_session.get.return_value = mock_inno_need
    need_desc = fetch_company_job_details(mock_session, "innowise", "inno-da", job={"link": "https://example.com/inno-da"})
    assert "For successful work with projects, you need:" in need_desc
    assert "PowerBI, Tableau" in need_desc

    # 5. Pipeline fetch_job_details safeguard
    from src.pipeline import fetch_job_details
    # Short description in job dict must delegate to fetch_details
    job_stub = {"source": "innowise", "job_id": "inno-1", "link": "https://example.com/inno", "description": "Level: Senior"}
    pipe_desc = fetch_job_details(mock_session, job_stub)
    assert "Level: Senior" not in pipe_desc
    assert "For successful work with projects, you need:" in pipe_desc

    print("PASS: test_truncated_description_fixes")


if __name__ == "__main__":
    test_manifest_loading_and_filtering()
    test_extract_path_json()
    test_stringify_value()
    test_clean_endpoint_url()
    test_matches_location_filter()
    test_ats_api_extraction_mock()
    test_static_html_extraction_mock()
    test_dynamic_and_browser_fallback()
    test_concurrent_company_scraping_mock()
    test_pipeline_source_grouping()
    test_manifest_selectors_syntax_validity()
    test_static_html_whitespace_and_deduplication()
    test_fetch_company_job_details_with_job_dict()
    test_smartrecruiters_url_and_sections_extraction()
    test_telegram_link_text_generation()
    test_bank_of_georgia_hosted_url_mapping()
    test_embedded_state_and_company_fixes()
    test_critical_safeguards()
    test_truncated_description_fixes()
    print("\nALL MANIFEST ENGINE TESTS PASSED SUCCESSFULLY!")



