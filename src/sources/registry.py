from __future__ import annotations

from urllib.parse import urlsplit

from src.config.loader import AppConfig, CompanyConfig
from src.sources.base import JobSource, SourceError
from src.sources.aem_joblist import AemJobListSource
from src.sources.cornerstone import CornerstoneSource
from src.sources.eightfold import EightfoldSource
from src.sources.avature import AvatureSource
from src.sources.generic import GenericHtmlSource
from src.sources.greenhouse import GreenhouseSource
from src.sources.icims import ICIMSSource
from src.sources.jobsyn import JobsynSource
from src.sources.jobvite import JobviteSource
from src.sources.jibe import JibeSource
from src.sources.lever import LeverSource
from src.sources.linkedin_email import LinkedInEmailSource
from src.sources.paradox import ParadoxSource
from src.sources.sample import SampleSource
from src.sources.selectminds import SelectMindsSource
from src.sources.smartrecruiters import SmartRecruitersSource
from src.sources.successfactors import SuccessFactorsSource
from src.sources.taleo_business import TaleoBusinessSource
from src.sources.workable import WorkableSource
from src.sources.workday import WorkdaySource


SOURCE_TYPES: dict[str, type[JobSource]] = {
    "aem_joblist": AemJobListSource,
    "avature": AvatureSource,
    "cornerstone": CornerstoneSource,
    "csod": CornerstoneSource,
    "eightfold": EightfoldSource,
    "generic": GenericHtmlSource,
    "generic_html": GenericHtmlSource,
    "greenhouse": GreenhouseSource,
    "icims": ICIMSSource,
    "lever": LeverSource,
    "job_alert_email": LinkedInEmailSource,
    "jobsyn": JobsynSource,
    "jobvite": JobviteSource,
    "jibe": JibeSource,
    "linkedin_email": LinkedInEmailSource,
    "paradox": ParadoxSource,
    "sample": SampleSource,
    "selectminds": SelectMindsSource,
    "smartrecruiters": SmartRecruitersSource,
    "successfactors": SuccessFactorsSource,
    "taleo_business": TaleoBusinessSource,
    "taleo_be": TaleoBusinessSource,
    "workable": WorkableSource,
    "workday": WorkdaySource,
}


def create_source(company: CompanyConfig, config: AppConfig) -> JobSource:
    source_type = company.source_type
    if source_type == "auto":
        source_type = detect_source_type(company.search_url or company.careers_url)
    source_class = SOURCE_TYPES.get(source_type)
    if not source_class:
        supported = ", ".join(sorted(SOURCE_TYPES))
        raise SourceError(f"Unsupported source_type {source_type!r}; supported: {supported}")
    return source_class(company, config)


def detect_source_type(url: str) -> str:
    host = urlsplit(url).netloc.casefold()
    if "greenhouse.io" in host:
        return "greenhouse"
    if "lever.co" in host:
        return "lever"
    if "smartrecruiters.com" in host:
        return "smartrecruiters"
    if "myworkdayjobs.com" in host or "workday.com" in host:
        return "workday"
    if "apply.workable.com" in host:
        return "workable"
    if "eightfold.ai" in host:
        return "eightfold"
    if "csod.com" in host:
        return "cornerstone"
    if "icims.com" in host:
        return "icims"
    if "tbe.taleo.net" in host:
        return "taleo_business"
    if "successfactors" in host or "jobs." in host and "/go/" in url:
        return "successfactors"
    if "selectminds.com" in host:
        return "selectminds"
    if "jobvite.com" in host:
        return "jobvite"
    return "generic_html"
