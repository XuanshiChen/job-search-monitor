from src.config.loader import CompanyConfig
from src.sources.avature import AvatureSource
from src.sources.aem_joblist import AemJobListSource
from src.sources.cornerstone import CornerstoneSource
from src.sources.eightfold import EightfoldSource
from src.sources.greenhouse import GreenhouseSource
from src.sources.generic import GenericHtmlSource
from src.sources.icims import ICIMSSource
from src.sources.jobsyn import JobsynSource
from src.sources.jobvite import JobviteSource
from src.sources.jibe import JibeSource
from src.sources.lever import LeverSource
from src.sources.paradox import ParadoxSource
from src.sources.registry import detect_source_type
from src.sources.selectminds import SelectMindsSource
from src.sources.smartrecruiters import SmartRecruitersSource
from src.sources.successfactors import SuccessFactorsSource
from src.sources.taleo_business import TaleoBusinessSource
from src.sources.workable import WorkableSource
from src.sources.workday import WorkdaySource


def test_source_auto_detection() -> None:
    assert detect_source_type("https://boards.greenhouse.io/acme") == "greenhouse"
    assert detect_source_type("https://jobs.lever.co/acme") == "lever"
    assert detect_source_type("https://acme.wd5.myworkdayjobs.com/Careers") == "workday"
    assert detect_source_type("https://apply.workable.com/acme") == "workable"
    assert detect_source_type("https://acme.csod.com/ux/ats/careersite/1/home") == "cornerstone"
    assert detect_source_type("https://acme.eightfold.ai/careers") == "eightfold"
    assert detect_source_type("https://acme.icims.com/jobs/search") == "icims"
    assert detect_source_type("https://tre.tbe.taleo.net/tre01/ats/careers/v2/jobSearch") == "taleo_business"
    assert detect_source_type("https://acme.referrals.selectminds.com/jobs") == "selectminds"
    assert detect_source_type("https://jobs.jobvite.com/acme/jobs") == "jobvite"
    assert detect_source_type("https://careers.example.com/jobs") == "generic_html"


def test_greenhouse_response_is_normalized_without_network(app_config, monkeypatch) -> None:
    company = CompanyConfig(
        name="Acme Grid",
        priority="tier_2",
        source_type="greenhouse",
        options={"board_token": "acme"},
    )
    source = GreenhouseSource(company, app_config)
    monkeypatch.setattr(
        source.client,
        "get_json",
        lambda *args, **kwargs: {
            "jobs": [
                {
                    "id": 42,
                    "title": "Protection Engineer",
                    "location": {"name": "Ottawa, ON, Canada"},
                    "content": "<p>IEC 61850 and relay testing</p>",
                    "absolute_url": "https://boards.greenhouse.io/acme/jobs/42",
                    "updated_at": "2026-09-01T10:00:00Z",
                }
            ]
        },
    )

    jobs = source.fetch_jobs()

    assert len(jobs) == 1
    assert jobs[0].external_job_id == "42"
    assert jobs[0].province == "Ontario"
    assert jobs[0].company_priority == "tier_2"
    assert jobs[0].description == "IEC 61850 and relay testing"
    assert jobs[0].date_posted is None


def test_lever_paginates_and_parses_epoch_without_network(app_config, monkeypatch) -> None:
    company = CompanyConfig(
        name="Lever Grid",
        priority="tier_1",
        source_type="lever",
        options={"site": "lever-grid"},
    )
    source = LeverSource(company, app_config)
    monkeypatch.setattr(
        source.client,
        "get_json",
        lambda *args, **kwargs: [
            {
                "id": "lever-1",
                "text": "Relay Engineer",
                "categories": {"location": "Toronto, ON", "commitment": "Full-time"},
                "descriptionPlain": "Protective relay testing",
                "hostedUrl": "https://jobs.lever.co/lever-grid/lever-1",
                "createdAt": 1_725_192_000_000,
                "workplaceType": "hybrid",
            }
        ],
    )

    job = source.fetch_jobs()[0]

    assert job.date_posted is not None
    assert job.date_posted.year == 2024
    assert job.country == "Canada"
    assert job.remote_type == "hybrid"


def test_smartrecruiters_prefers_public_apply_url(app_config, monkeypatch) -> None:
    company = CompanyConfig(
        name="Smart Grid",
        priority="tier_2",
        source_type="smartrecruiters",
        options={"company_identifier": "SmartGrid"},
    )
    source = SmartRecruitersSource(company, app_config)

    def response(url, **kwargs):
        if url.endswith("/postings"):
            return {"totalFound": 1, "content": [{"id": "smart-1"}]}
        return {
            "id": "smart-1",
            "name": "Substation Engineer",
            "releasedDate": "2026-09-01T10:00:00Z",
            "location": {"city": "Ottawa", "region": "ON", "country": "CA", "remote": True},
            "applyUrl": "https://jobs.smartrecruiters.com/SmartGrid/smart-1",
            "jobAd": {"sections": {"jobDescription": {"text": "IEC 61850 work"}}},
        }

    monkeypatch.setattr(source.client, "get_json", response)
    job = source.fetch_jobs()[0]

    assert job.job_url == "https://jobs.smartrecruiters.com/SmartGrid/smart-1"
    assert job.country == "Canada"
    assert job.remote_type == "remote"


def test_workday_external_paths_do_not_duplicate_job_segment() -> None:
    assert WorkdaySource._job_path("/job/Toronto/Protection-Engineer_R1") == (
        "Toronto/Protection-Engineer_R1"
    )
    assert WorkdaySource._job_path("Toronto/Protection-Engineer_R1") == (
        "Toronto/Protection-Engineer_R1"
    )


def test_workday_uses_tenant_safe_page_size(app_config, monkeypatch) -> None:
    company = CompanyConfig(
        name="Workday Utility",
        priority="critical",
        search_url="https://utility.wd1.myworkdayjobs.com/Careers",
        source_type="workday",
        options={
            "host": "https://utility.wd1.myworkdayjobs.com",
            "tenant": "utility",
            "site": "Careers",
            "fetch_details": False,
        },
    )
    source = WorkdaySource(company, app_config)
    bodies = []

    def response(url, **kwargs):
        bodies.append(kwargs["json"])
        return {
            "total": 1,
            "jobPostings": [
                {
                    "title": "Protection Engineer",
                    "externalPath": "/job/Toronto/Protection-Engineer_R1",
                    "locationsText": "Toronto, ON, Canada",
                    "bulletFields": ["R1"],
                }
            ],
        }

    monkeypatch.setattr(source.client, "post_json", response)

    jobs = source.fetch_jobs()

    assert len(jobs) == 1
    assert bodies[0]["limit"] == 20


class HtmlResponse:
    def __init__(self, text: str):
        self.text = text


def test_taleo_business_listing_and_detail_are_normalized(app_config, monkeypatch) -> None:
    company = CompanyConfig(
        name="Nuclear Utility",
        priority="tier_1",
        search_url=(
            "https://tre.tbe.taleo.net/tre01/ats/careers/v2/"
            "jobSearch?cws=37&org=UTILITY"
        ),
        source_type="taleo_business",
        options={"include_title_keywords": ["electrical"], "max_pages": 1},
    )
    source = TaleoBusinessSource(company, app_config)
    listing = """
    <div class="oracletaleocwsv2-accordion">
      <div class="oracletaleocwsv2-accordion-head-info">
        <h4><a class="viewJobLink" href="/tre01/ats/careers/v2/viewRequisition?org=UTILITY&amp;cws=37&amp;rid=42">Electrical Engineer EIT</a></h4>
        <div>Chalk River, ON</div><div>Full-Time</div><div>01/09/2026</div>
      </div>
    </div>
    """
    detail = '<div class="col-md-8"><p>Substation relay testing and IEC 61850.</p></div>'
    responses = iter([HtmlResponse(listing), HtmlResponse(detail)])
    monkeypatch.setattr(source.client, "request", lambda *args, **kwargs: next(responses))

    job = source.fetch_jobs()[0]

    assert job.external_job_id == "42"
    assert job.province == "Ontario"
    assert job.country == "Canada"
    assert job.date_posted is not None
    assert job.date_posted.day == 1
    assert "IEC 61850" in job.description


def test_generic_source_supports_pagination_filters_and_details(app_config, monkeypatch) -> None:
    company = CompanyConfig(
        name="Engineering Consultant",
        priority="tier_2",
        search_url="https://example.com/careers?pagination=1",
        source_type="generic_html",
        options={
            "page_param": "pagination",
            "max_pages": 2,
            "include_title_keywords": ["electrical"],
            "detail_description_selector": ".description, .qualifications",
            "selectors": {
                "item": "ul.job",
                "title": "a.title",
                "link": "a.title",
                "location": ".location",
                "date_posted": ".date",
                "id_query_param": "post",
            },
        },
    )
    source = GenericHtmlSource(company, app_config)
    pages = {
        "pagination=1": """
          <ul class="job"><a class="title" href="/detail?post=42">Electrical Engineer</a>
          <li class="location">Toronto, ON</li><li class="date">2026-09-01</li></ul>
        """,
        "pagination=2": """
          <ul class="job"><a class="title" href="/detail?post=43">Civil Engineer</a>
          <li class="location">Ottawa, ON</li></ul>
        """,
        "/detail?post=42": """
          <div class="description">Substation design.</div>
          <div class="qualifications">Relay testing.</div>
        """,
    }

    def response(method, url, **kwargs):
        return HtmlResponse(next(value for key, value in pages.items() if key in url))

    monkeypatch.setattr(source.client, "request", response)
    jobs = source.fetch_jobs()

    assert len(jobs) == 1
    assert jobs[0].external_job_id == "42"
    assert jobs[0].province == "Ontario"
    assert jobs[0].date_posted is not None
    assert "Substation design" in jobs[0].description
    assert "Relay testing" in jobs[0].description


def test_jibe_filters_country_and_normalizes_jobs(app_config, monkeypatch) -> None:
    company = CompanyConfig(
        name="Power OEM",
        priority="tier_1",
        search_url="https://careers.example.com/jobs?location=Canada",
        source_type="jibe",
        options={
            "country": "Canada",
            "include_title_keywords": ["application engineer"],
            "query": {"location": "Canada"},
        },
    )
    source = JibeSource(company, app_config)
    monkeypatch.setattr(
        source.client,
        "get_json",
        lambda *args, **kwargs: {
            "totalCount": 2,
            "jobs": [
                {
                    "data": {
                        "slug": "R1",
                        "req_id": "R1",
                        "title": "Protection Application Engineer",
                        "city": "Toronto",
                        "state": "Ontario",
                        "country": "Canada",
                        "description": "IEC 61850 and relay testing",
                        "employment_type": "FULL_TIME",
                        "update_date": "2026-09-01T12:00:00Z",
                    }
                },
                {
                    "data": {
                        "slug": "R2",
                        "req_id": "R2",
                        "title": "Application Engineer",
                        "city": "Boston",
                        "state": "Massachusetts",
                        "country": "United States",
                    }
                },
            ],
        },
    )

    jobs = source.fetch_jobs()

    assert len(jobs) == 1
    assert jobs[0].external_job_id == "R1"
    assert jobs[0].province == "Ontario"
    assert jobs[0].country == "Canada"
    assert jobs[0].date_posted is not None


def test_aem_job_list_paginates_and_fetches_description(app_config, monkeypatch) -> None:
    company = CompanyConfig(
        name="Grid Manufacturer",
        priority="tier_1",
        search_url="https://example.com/careers/open-jobs",
        source_type="aem_joblist",
        options={
            "api_url": "https://example.com/jobs.json",
            "include_title_keywords": ["power"],
            "page_size": 20,
            "max_pages": 2,
            "detail_description_selector": ".job-description",
        },
    )
    source = AemJobListSource(company, app_config)
    monkeypatch.setattr(
        source.client,
        "get_json",
        lambda *args, **kwargs: {
            "totalNumber": 1,
            "items": [
                {
                    "title": "Power Systems Engineer",
                    "url": "https://example.com/careers/open-jobs/details/JID3-42",
                    "location": "Richmond, British Columbia, Canada",
                    "publicationDate": "2026-09-01T00:00:00Z",
                    "remoteType": "Hybrid",
                    "experience": "Entry Level",
                }
            ],
        },
    )
    monkeypatch.setattr(
        source.client,
        "request",
        lambda *args, **kwargs: HtmlResponse(
            '<div class="job-description">Substation automation and protection.</div>'
        ),
    )

    job = source.fetch_jobs()[0]

    assert job.external_job_id == "JID3-42"
    assert job.province == "British Columbia"
    assert job.remote_type == "hybrid"
    assert "Substation automation" in job.description


def test_cornerstone_public_search_and_detail_are_normalized(app_config, monkeypatch) -> None:
    company = CompanyConfig(
        name="BBA Consultants",
        priority="tier_1",
        search_url="https://bba.csod.com/ux/ats/careersite/5/home?c=bba&lang=en-US",
        source_type="cornerstone",
        options={
            "career_site_id": 5,
            "corp": "bba",
            "search_terms": ["electrical"],
            "country_codes": ["CA"],
            "include_title_keywords": ["engineer"],
        },
    )
    source = CornerstoneSource(company, app_config)
    monkeypatch.setattr(
        source.client,
        "request",
        lambda method, url: HtmlResponse('csod.context={"token":"public-token"};'),
    )
    monkeypatch.setattr(
        source.client,
        "post_json",
        lambda *args, **kwargs: {
            "data": {
                "totalCount": 1,
                "requisitions": [{"requisitionId": 3136, "displayTitle": "Electrical Engineer"}],
            }
        },
    )
    monkeypatch.setattr(
        source.client,
        "get_json",
        lambda *args, **kwargs: {
            "data": {
                "requisitionId": 3136,
                "ref": "R-3136",
                "displayTitle": "Electrical Engineer - Protection",
                "externalDescription": "<p>Relay and substation studies.</p>",
                "primaryLocation": {"city": "Toronto", "state": "ON", "country": "Canada"},
                "openDate": "2026-09-01T00:00:00Z",
            }
        },
    )

    job = source.fetch_jobs()[0]

    assert job.external_job_id == "R-3136"
    assert job.title == "Electrical Engineer - Protection"
    assert job.description == "Relay and substation studies."
    assert job.country == "Canada"
    assert job.job_url.endswith("/home/requisition/3136?c=bba&lang=en-US")


def test_eightfold_public_search_and_detail_are_normalized(app_config, monkeypatch) -> None:
    company = CompanyConfig(
        name="Eaton",
        priority="tier_1",
        search_url="https://eaton.eightfold.ai/careers",
        source_type="eightfold",
        options={"domain": "eaton.com", "search_terms": ["power"]},
    )
    source = EightfoldSource(company, app_config)

    def response(url, **kwargs):
        if "position_details" in url:
            return {
                "data": {
                    "id": 123,
                    "displayJobId": "R123",
                    "name": "Power Systems Engineer",
                    "standardizedLocations": ["Burlington, ON, CA"],
                    "jobDescription": "<p>Protection and relay testing.</p>",
                    "positionUrl": "/careers/job/123",
                    "postedTs": 1_788_200_000,
                    "workLocationOption": "hybrid",
                }
            }
        return {
            "data": {
                "count": 1,
                "positions": [{"id": 123, "name": "Power Systems Engineer"}],
            }
        }

    monkeypatch.setattr(source.client, "get_json", response)
    job = source.fetch_jobs()[0]

    assert job.external_job_id == "R123"
    assert job.province == "Ontario"
    assert job.remote_type == "hybrid"
    assert job.description == "Protection and relay testing."


def test_icims_public_cards_and_detail_are_normalized(app_config, monkeypatch) -> None:
    company = CompanyConfig(
        name="Sargent & Lundy",
        priority="tier_1",
        search_url="https://international-sargentlundy.icims.com/jobs/search",
        source_type="icims",
        options={"include_title_keywords": ["electrical"]},
    )
    source = ICIMSSource(company, app_config)
    listing = """
    <ul class="iCIMS_JobsTable"><li class="iCIMS_JobCardItem">
      <div class="title"><a href="https://example.icims.com/jobs/25457/electrical-engineer/job">
        <h3>Electrical Engineer</h3></a></div>
      <div class="description">Power engineering work.</div>
      <dl class="iCIMS_JobHeaderGroup">
        <div class="iCIMS_JobHeaderTag"><dt class="iCIMS_JobHeaderField">City</dt>
          <dd class="iCIMS_JobHeaderData">North York</dd></div>
        <div class="iCIMS_JobHeaderTag"><dt class="iCIMS_JobHeaderField">State/Province</dt>
          <dd class="iCIMS_JobHeaderData">ON</dd></div>
        <div class="iCIMS_JobHeaderTag"><dt class="iCIMS_JobHeaderField">Country</dt>
          <dd class="iCIMS_JobHeaderData">Canada</dd></div>
        <div class="iCIMS_JobHeaderTag"><dt class="iCIMS_JobHeaderField">Job ID</dt>
          <dd class="iCIMS_JobHeaderData">2026-25457</dd></div>
      </dl>
    </li></ul>
    """
    detail = """
    <div class="iCIMS_JobContent"><dl class="iCIMS_JobHeaderGroup">
      <div class="iCIMS_JobHeaderTag"><dt class="iCIMS_JobHeaderField">Type</dt>
        <dd class="iCIMS_JobHeaderData">Full Time</dd></div>
    </dl><div class="iCIMS_Expandable_Text">IEC 61850 and substation protection.</div></div>
    """
    monkeypatch.setattr(
        source.client,
        "request",
        lambda method, url: HtmlResponse(detail if "/25457/" in url else listing),
    )

    job = source.fetch_jobs()[0]

    assert job.external_job_id == "2026-25457"
    assert job.province == "Ontario"
    assert job.description == "IEC 61850 and substation protection."


def test_successfactors_listing_and_detail_are_normalized(app_config, monkeypatch) -> None:
    company = CompanyConfig(
        name="Ontario Utility",
        priority="critical",
        search_url="https://jobs.example.test/search/",
        source_type="successfactors",
        options={"include_title_keywords": ["engineer"]},
    )
    source = SuccessFactorsSource(company, app_config)
    listing = """
    <table><tr class="data-row">
      <td><a class="jobTitle-link" href="/job/Toronto/Protection-Engineer/1425597900/">
        Protection Engineer</a></td>
      <td class="jobLocation">Toronto, ON, Canada</td>
      <td class="jobDate">Sep 1, 2026</td>
    </tr></table>
    """
    detail = """
    <meta itemprop="datePosted" content="2026-09-01" />
    <div itemprop="description"><p>IEC 61850 relay testing.</p></div>
    """
    monkeypatch.setattr(
        source.client,
        "request",
        lambda method, url: HtmlResponse(detail if "/job/" in url else listing),
    )

    job = source.fetch_jobs()[0]

    assert job.external_job_id == "1425597900"
    assert job.province == "Ontario"
    assert job.description == "IEC 61850 relay testing."
    assert job.date_posted is not None


def test_successfactors_filters_non_canadian_locations(app_config, monkeypatch) -> None:
    company = CompanyConfig(
        name="Kiewit",
        priority="tier_2",
        search_url="https://jobs.example.test/search/",
        source_type="successfactors",
        options={
            "include_title_keywords": ["electrical"],
            "location_keywords": ["ontario", "british columbia", "canada"],
            "fetch_details": False,
        },
    )
    source = SuccessFactorsSource(company, app_config)
    listing = """
    <table>
      <tr class="data-row"><td><a class="jobTitle-link" href="/job/one/11111/">
        Electrical Engineer</a></td><td class="jobLocation">Oakville, ON, CA</td></tr>
      <tr class="data-row"><td><a class="jobTitle-link" href="/job/two/22222/">
        Electrical Engineer</a></td><td class="jobLocation">Denver, CO, US</td></tr>
    </table>
    """
    monkeypatch.setattr(source.client, "request", lambda method, url: HtmlResponse(listing))

    jobs = source.fetch_jobs()

    assert len(jobs) == 1
    assert jobs[0].province == "Ontario"


def test_avature_public_results_are_normalized(app_config, monkeypatch) -> None:
    company = CompanyConfig(
        name="Siemens",
        priority="critical",
        search_url="https://jobs.example.test/SearchJobs/?folderId=1",
        source_type="avature",
        options={"search_terms": ["protection"], "fetch_details": False},
    )
    source = AvatureSource(company, app_config)
    html = """
    <article class="article article--result">
      <h3><a class="link" href="/JobDetail/508940">Protection Engineer</a></h3>
      <span class="list-item-location">Oakville Ontario Canada</span>
      <span class="list-item-jobId">Job ID: 508940</span>
    </article>
    """
    monkeypatch.setattr(source.client, "request", lambda method, url: HtmlResponse(html))

    job = source.fetch_jobs()[0]

    assert job.external_job_id == "508940"
    assert job.company == "Siemens"
    assert job.province == "Ontario"


def test_workable_public_api_is_normalized(app_config, monkeypatch) -> None:
    company = CompanyConfig(
        name="AltaLink",
        priority="tier_1",
        source_type="workable",
        options={"account": "altalink", "include_title_keywords": ["engineer"]},
    )
    source = WorkableSource(company, app_config)
    monkeypatch.setattr(
        source.client,
        "post_json",
        lambda *args, **kwargs: {
            "results": [{"id": 1, "shortcode": "ABC123", "title": "Protection Engineer"}]
        },
    )
    monkeypatch.setattr(
        source.client,
        "get_json",
        lambda *args, **kwargs: {
            "id": 1,
            "shortcode": "ABC123",
            "title": "Protection Engineer",
            "location": {"city": "Calgary", "region": "Alberta", "country": "Canada"},
            "description": "<p>Date Posted: September 1, 2026</p><p>Substation work</p>",
            "type": "Full-time",
        },
    )

    job = source.fetch_jobs()[0]

    assert job.job_url == "https://apply.workable.com/altalink/j/ABC123"
    assert job.province == "Alberta"
    assert job.date_posted is not None


def test_selectminds_public_landing_page_is_normalized(app_config, monkeypatch) -> None:
    company = CompanyConfig(
        name="Tetra Tech",
        priority="tier_1",
        search_url="https://example.selectminds.com/page/canada",
        source_type="selectminds",
    )
    source = SelectMindsSource(company, app_config)
    html = """
    <div id="job_list_1" class="job_list_row">
      <a class="job_link" href="/jobs/electrical-engineer-123">Electrical Engineer</a>
      <a class="location">Vancouver, British Columbia, Canada</a>
      <p class="jlr_description">Power systems consulting.</p>
      <p class="job_external_id"><span class="field_value">71500003706</span> Requisition #</p>
    </div>
    """
    monkeypatch.setattr(source.client, "request", lambda method, url: HtmlResponse(html))

    job = source.fetch_jobs()[0]

    assert job.external_job_id == "71500003706"
    assert job.province == "British Columbia"
    assert job.description == "Power systems consulting."


def test_selectminds_filters_unrelated_titles(app_config, monkeypatch) -> None:
    company = CompanyConfig(
        name="Arup",
        priority="tier_2",
        search_url="https://jobs.example.test/page/jobs-in-canada",
        source_type="selectminds",
        options={"include_title_keywords": ["electrical", "power"], "max_jobs": 1},
    )
    source = SelectMindsSource(company, app_config)
    html = """
    <div class="job_list_row">
      <a class="job_link" href="/jobs/marketing-11111">Marketing Manager</a>
    </div>
    <div class="job_list_row">
      <a class="job_link" href="/jobs/power-engineer-22222">Power Systems Engineer</a>
      <a class="location">Toronto, Ontario, Canada</a>
    </div>
    <div class="job_list_row">
      <a class="job_link" href="/jobs/electrical-engineer-33333">Electrical Engineer</a>
    </div>
    """
    monkeypatch.setattr(source.client, "request", lambda method, url: HtmlResponse(html))

    jobs = source.fetch_jobs()

    assert len(jobs) == 1
    assert jobs[0].title == "Power Systems Engineer"
    assert jobs[0].external_job_id == "22222"


def test_jobvite_public_listing_and_detail_are_normalized(app_config, monkeypatch) -> None:
    company = CompanyConfig(
        name="EPCOR",
        priority="tier_2",
        search_url="https://jobs.jobvite.com/epcor/jobs",
        source_type="jobvite",
        options={
            "include_title_keywords": ["engineer"],
            "location_keywords": ["ontario", "alberta"],
        },
    )
    source = JobviteSource(company, app_config)
    listing = """
    <table class="jv-job-list"><tbody>
      <tr><td class="jv-job-list-name"><a href="/epcor/job/abc123">Protection Engineer</a></td>
      <td class="jv-job-list-location">Edmonton, Alberta</td></tr>
      <tr><td class="jv-job-list-name"><a href="/epcor/job/us456">Electrical Engineer</a></td>
      <td class="jv-job-list-location">Phoenix, Arizona</td></tr>
    </tbody></table>
    """
    detail = """
    <script type="application/ld+json">
    {"@type":"JobPosting","identifier":{"value":"R-123"},
     "title":"Protection Engineer","datePosted":"2026-09-01",
     "employmentType":"FULL_TIME","description":"<p>Relay testing and IEC 61850.</p>",
     "jobLocation":{"address":{"addressLocality":"Edmonton",
       "addressRegion":"Alberta","addressCountry":"Canada"}}}
    </script>
    """
    monkeypatch.setattr(
        source.client,
        "request",
        lambda method, url: HtmlResponse(detail if "/job/" in url else listing),
    )

    jobs = source.fetch_jobs()

    assert len(jobs) == 1
    assert jobs[0].external_job_id == "R-123"
    assert jobs[0].province == "Alberta"
    assert jobs[0].description == "Relay testing and IEC 61850."
    assert jobs[0].date_posted is not None


def test_jobsyn_public_api_is_normalized(app_config, monkeypatch) -> None:
    company = CompanyConfig(
        name="Burns & McDonnell",
        priority="tier_1",
        source_type="jobsyn",
        options={
            "origin": "burnsmcd.jobs",
            "business_unit_ids": [27184],
            "search_terms": ["substation"],
        },
    )
    source = JobsynSource(company, app_config)
    monkeypatch.setattr(
        source.client,
        "get_json",
        lambda *args, **kwargs: {
            "jobs": [
                {
                    "guid": "GUID123",
                    "reqid": "263388",
                    "title_exact": "Staff Substation Engineer",
                    "title_slug": "staff-substation-engineer",
                    "location_exact": "Calgary, AB",
                    "city_exact": "Calgary",
                    "state_short": "AB",
                    "country_exact": "Canada",
                    "description": "Relay and substation design",
                    "date_new": "2026-09-01T10:00:00Z",
                    "job_type": "Full-time",
                }
            ],
            "pagination": {"has_more_pages": False},
        },
    )

    job = source.fetch_jobs()[0]

    assert job.external_job_id == "263388"
    assert job.company == "Burns & McDonnell"
    assert job.country == "Canada"
    assert job.date_posted is not None


def test_paradox_listing_and_json_ld_are_normalized(app_config, monkeypatch) -> None:
    company = CompanyConfig(
        name="GE Vernova",
        priority="critical",
        search_url="https://careers.example.test/jobs?filter[country][0]=Canada",
        source_type="paradox",
        options={"include_title_keywords": ["substation"]},
    )
    source = ParadoxSource(company, app_config)
    listing = """
    <ul><li class="results-list__item">
      <a class="results-list__item-title--link" href="/substation-engineer/job/R5001">
        Substation Engineer</a>
      <span class="results-list__item-street--label">Markham, ON, CA</span>
      <span class="results-list__custom2--label">2026-09-02</span>
      <span class="results-list__req-id--label">R5001</span>
    </li></ul>
    """
    detail = """
    <script type="application/ld+json">
    {"@type":"JobPosting","title":"Substation Engineer","datePosted":"2026-09-02",
     "description":"<p>IEC 61850 and protection work.</p>",
     "jobLocation":{"address":{"addressLocality":"Markham","addressRegion":"ON",
     "addressCountry":"CA"}}}
    </script>
    """

    class Response:
        def __init__(self, text):
            self.text = text
            self.content = text.encode("utf-8")

    monkeypatch.setattr(
        source.client,
        "request",
        lambda method, url: Response(detail if "/job/" in url else listing),
    )

    job = source.fetch_jobs()[0]

    assert job.external_job_id == "R5001"
    assert job.province == "Ontario"
    assert job.description == "IEC 61850 and protection work."
    assert job.date_posted is not None
