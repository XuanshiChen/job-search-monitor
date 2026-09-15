from __future__ import annotations

import logging
import re
from email import policy
from email.message import Message
from email.parser import BytesParser
from html import unescape
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote

from bs4 import BeautifulSoup, Tag

from src.config.loader import AppConfig
from src.models.job import JobCandidate
from src.sources.base import JobSource, SourceError
from src.sources.normalization import infer_location_parts, infer_remote_type
from src.utils.dates import parse_datetime


_JOB_PATH_PATTERN = re.compile(
    r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?#\s]*-)?(?P<id>\d+)", re.IGNORECASE
)
_CURRENT_JOB_PATTERN = re.compile(r"[?&]currentJobId=(?P<id>\d+)", re.IGNORECASE)
_INDEED_JOB_PATTERN = re.compile(r"[?&]jk=(?P<id>[a-z0-9]+)", re.IGNORECASE)
_GLASSDOOR_JOB_PATTERN = re.compile(
    r"[?&](?:jobListingId|jl)=(?P<id>\d+)", re.IGNORECASE
)
_JOB_BANK_PATTERN = re.compile(
    r"jobbank\.gc\.ca/jobsearch/jobposting/(?P<id>\d+)", re.IGNORECASE
)
_URL_PATTERN = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)
_GENERIC_LINK_TEXT = {
    "apply",
    "apply now",
    "see all jobs",
    "see job",
    "view job",
    "view jobs",
}
_NOISE_PHRASES = (
    "actively recruiting",
    "be among the first",
    "easy apply",
    "promoted",
    "see all jobs",
    "this email was intended",
    "unsubscribe",
    "view job",
)


class LinkedInEmailParser:
    """Convert public job-alert emails into normalized job candidates."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.logger = logging.getLogger("sources.linkedin_email.parser")

    def parse_file(self, path: Path) -> list[JobCandidate]:
        message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        return self.parse_message(message, source_label=path.name)

    def parse_message(
        self, message: Message, *, source_label: str = "linkedIn-alert.eml"
    ) -> list[JobCandidate]:
        subject = _clean(str(message.get("Subject", "LinkedIn job alert")))
        message_id = _clean(str(message.get("Message-ID", source_label)))
        received_at = parse_datetime(message.get("Date"))
        html_body, text_body = _message_bodies(message)
        candidates = self._from_html(
            html_body,
            subject=subject,
            message_id=message_id,
            received_at=received_at,
            source_label=source_label,
        )
        known_ids = {item.external_job_id for item in candidates}
        candidates.extend(
            self._from_text(
                text_body,
                subject=subject,
                message_id=message_id,
                received_at=received_at,
                source_label=source_label,
                skip_ids=known_ids,
            )
        )
        return candidates

    def _from_html(
        self,
        body: str,
        *,
        subject: str,
        message_id: str,
        received_at: object,
        source_label: str,
    ) -> list[JobCandidate]:
        if not body:
            return []
        soup = BeautifulSoup(body, "html.parser")
        result: list[JobCandidate] = []
        seen: set[str] = set()
        for link in soup.find_all("a", href=True):
            href = unescape(str(link.get("href", "")))
            identity = job_alert_identity(href)
            if not identity or identity[1] in seen:
                continue
            provider, job_id, job_url = identity
            seen.add(job_id)
            card_lines = _card_lines(link)
            link_lines = _meaningful_lines(link.stripped_strings)
            title = next(
                (line for line in link_lines if line.casefold() not in _GENERIC_LINK_TEXT),
                "",
            )
            title, company, location = _infer_fields(title, card_lines)
            result.append(
                self._candidate(
                    job_id=job_id,
                    provider=provider,
                    job_url=job_url,
                    title=title,
                    company=company,
                    location=location,
                    description=" · ".join(card_lines),
                    subject=subject,
                    message_id=message_id,
                    received_at=received_at,
                    source_label=source_label,
                )
            )
        return result

    def _from_text(
        self,
        body: str,
        *,
        subject: str,
        message_id: str,
        received_at: object,
        source_label: str,
        skip_ids: set[str | None],
    ) -> list[JobCandidate]:
        if not body:
            return []
        lines = _meaningful_lines(body.splitlines())
        result: list[JobCandidate] = []
        seen = {item for item in skip_ids if item}
        for index, line in enumerate(lines):
            for raw_url in _URL_PATTERN.findall(line):
                identity = job_alert_identity(raw_url.rstrip(".,);"))
                if not identity or identity[1] in seen:
                    continue
                provider, job_id, job_url = identity
                seen.add(job_id)
                context = lines[max(0, index - 4) : min(len(lines), index + 4)]
                context = [item for item in context if "http" not in item.casefold()]
                title, company, location = _infer_fields("", context)
                result.append(
                    self._candidate(
                        job_id=job_id,
                        provider=provider,
                        job_url=job_url,
                        title=title,
                        company=company,
                        location=location,
                        description=" · ".join(context),
                        subject=subject,
                        message_id=message_id,
                        received_at=received_at,
                        source_label=source_label,
                    )
                )
        return result

    def _candidate(
        self,
        *,
        job_id: str,
        provider: str,
        job_url: str,
        title: str,
        company: str,
        location: str,
        description: str,
        subject: str,
        message_id: str,
        received_at: object,
        source_label: str,
    ) -> JobCandidate:
        provider_label = {
            "linkedin": "LinkedIn",
            "indeed": "Indeed",
            "glassdoor": "Glassdoor",
            "jobbank": "Job Bank",
        }.get(provider, provider.title())
        company = company or f"Unknown {provider_label} Employer"
        configured_company = self.config.resolve_company(company)
        if configured_company:
            company = configured_company.name
            priority = configured_company.priority
        else:
            priority = str(
                self.config.settings.get("linkedin_email", {}).get(
                    "unknown_company_priority", "tier_3"
                )
            )
        city, province, country = infer_location_parts(location)
        return JobCandidate(
            external_job_id=job_id,
            title=title or f"{provider_label} job alert",
            company=company,
            company_priority=priority,
            location=location,
            city=city,
            province=province,
            country=country,
            remote_type=infer_remote_type(title, location, description),
            description=f"{provider_label} email alert: {subject}. {description}".strip(),
            job_url=job_url,
            source=f"{provider}_email",
            source_url=f"job-alert-email:{source_label}",
            raw_data={
                "message_id": message_id,
                "email_subject": subject,
                "email_received_at": str(received_at or ""),
                "source_file": source_label,
            },
        )


class LinkedInEmailSource(JobSource):
    source_name = "linkedin_email"

    def fetch_jobs(self) -> list[JobCandidate]:
        configured = self.company.search_url or str(
            self.company.options.get("directory", "")
        )
        directory = Path(configured) if configured else self.config.linkedin_email_directory
        if not directory.is_absolute():
            directory = self.config.root / directory
        if not directory.exists():
            return []
        parser = LinkedInEmailParser(self.config)
        result: list[JobCandidate] = []
        failures: list[str] = []
        for path in sorted(directory.glob("*.eml")):
            try:
                result.extend(parser.parse_file(path))
            except Exception as exc:
                failures.append(f"{path.name}: {exc}")
                self.logger.warning(
                    "linkedin_email_parse_failed",
                    extra={"email_file": path.name},
                    exc_info=True,
                )
        if failures and not result:
            raise SourceError("No job-alert emails could be parsed: " + "; ".join(failures[:3]))
        return result


def linkedin_job_identity(value: str) -> tuple[str, str] | None:
    identity = job_alert_identity(value)
    if identity and identity[0] == "linkedin":
        return identity[1], identity[2]
    return None


def job_alert_identity(value: str) -> tuple[str, str, str] | None:
    decoded = unescape(value.strip())
    for _ in range(3):
        match = _JOB_PATH_PATTERN.search(decoded) or _CURRENT_JOB_PATTERN.search(decoded)
        if match:
            job_id = match.group("id")
            return "linkedin", job_id, f"https://www.linkedin.com/jobs/view/{job_id}"
        if re.search(r"(?:^|[./])indeed\.", decoded, re.IGNORECASE):
            match = _INDEED_JOB_PATTERN.search(decoded)
            if match:
                raw_id = match.group("id")
                return "indeed", f"indeed:{raw_id}", f"https://ca.indeed.com/viewjob?jk={raw_id}"
        if "glassdoor." in decoded.casefold():
            match = _GLASSDOOR_JOB_PATTERN.search(decoded)
            if match:
                raw_id = match.group("id")
                return (
                    "glassdoor",
                    f"glassdoor:{raw_id}",
                    f"https://www.glassdoor.ca/job-listing/j?jl={raw_id}",
                )
        match = _JOB_BANK_PATTERN.search(decoded)
        if match:
            raw_id = match.group("id")
            return (
                "jobbank",
                f"jobbank:{raw_id}",
                f"https://www.jobbank.gc.ca/jobsearch/jobposting/{raw_id}",
            )
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    return None


def _message_bodies(message: Message) -> tuple[str, str]:
    html_parts: list[str] = []
    text_parts: list[str] = []
    for part in message.walk() if message.is_multipart() else (message,):
        disposition = str(part.get("Content-Disposition", "")).casefold()
        if "attachment" in disposition:
            continue
        content_type = part.get_content_type()
        if content_type not in {"text/html", "text/plain"}:
            continue
        try:
            content = part.get_content()
        except (LookupError, UnicodeError):
            payload = part.get_payload(decode=True) or b""
            content = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if content_type == "text/html":
            html_parts.append(str(content))
        else:
            text_parts.append(str(content))
    return "\n".join(html_parts), "\n".join(text_parts)


def _card_lines(link: Tag) -> list[str]:
    fallback = _meaningful_lines(link.stripped_strings)
    for depth, parent in enumerate(link.parents):
        if depth > 7 or not isinstance(parent, Tag):
            break
        lines = _meaningful_lines(parent.stripped_strings)
        job_ids = {
            identity[1]
            for child in parent.find_all("a", href=True)
            if (identity := job_alert_identity(str(child.get("href", ""))))
        }
        if 3 <= len(lines) <= 30 and len(job_ids) <= 1:
            return lines
        if len(lines) <= 30 and len(lines) > len(fallback):
            fallback = lines
    return fallback


def _infer_fields(title: str, lines: list[str]) -> tuple[str, str, str]:
    values = [line for line in lines if not _is_noise(line)]
    title = _clean(title)
    if not title or title.casefold() in _GENERIC_LINK_TEXT:
        title = values[0] if values else "LinkedIn job alert"
    tail = [line for line in values if line.casefold() != title.casefold()]

    company = ""
    location = ""
    if tail and " · " in tail[0]:
        parts = [_clean(item) for item in tail[0].split(" · ") if _clean(item)]
        if parts:
            company = parts[0]
        if len(parts) > 1:
            location = parts[1]
    if not company and tail:
        company = tail[0]
    remaining = [line for line in tail if line.casefold() != company.casefold()]
    location = location or next((line for line in remaining if _looks_like_location(line)), "")
    if not location and remaining:
        location = remaining[0]
    return title, company, location


def _meaningful_lines(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        line = _clean(value)
        if line and (not result or result[-1].casefold() != line.casefold()):
            result.append(line)
    return result


def _is_noise(value: str) -> bool:
    normalized = value.casefold()
    return any(phrase in normalized for phrase in _NOISE_PHRASES)


def _looks_like_location(value: str) -> bool:
    normalized = value.casefold()
    location_terms = (
        "alberta",
        "british columbia",
        "canada",
        "hybrid",
        "manitoba",
        "new brunswick",
        "nova scotia",
        "ontario",
        "quebec",
        "remote",
        "saskatchewan",
    )
    return "," in value or any(term in normalized for term in location_terms)


def _clean(value: str) -> str:
    return " ".join(unescape(value).replace("\u200b", "").split())


# Backward-compatible names: existing installs and commands keep working while
# the parser now accepts LinkedIn, Indeed, Glassdoor, and Job Bank alerts.
JobAlertEmailParser = LinkedInEmailParser
JobAlertEmailSource = LinkedInEmailSource
