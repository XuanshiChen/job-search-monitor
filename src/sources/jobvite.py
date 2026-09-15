from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from src.models.job import JobCandidate
from src.sources.base import JobSource, SourceError
from src.sources.normalization import infer_location_parts, infer_remote_type, location_string
from src.utils.dates import parse_datetime
from src.utils.text import html_to_text, normalize_text


class JobviteSource(JobSource):
    """Read public Jobvite career-site listings and JobPosting metadata."""

    source_name = "jobvite"

    def fetch_jobs(self) -> list[JobCandidate]:
        listing_url = self.company.search_url or self.company.careers_url
        if not listing_url:
            raise SourceError("Jobvite requires search_url or careers_url")

        soup = BeautifulSoup(self.client.request("GET", listing_url).text, "html.parser")
        title_keywords = self._option_strings("include_title_keywords")
        location_keywords = self._option_strings("location_keywords")
        max_jobs = max(1, int(self.company.options.get("max_jobs", 100)))
        max_details = max(0, int(self.company.options.get("max_details", max_jobs)))

        summaries: list[dict[str, str]] = []
        for row in soup.select("table.jv-job-list tbody tr"):
            link = row.select_one(".jv-job-list-name a[href]")
            if not isinstance(link, Tag):
                continue
            title = " ".join(link.get_text(" ", strip=True).split())
            location_node = row.select_one(".jv-job-list-location")
            location = (
                " ".join(location_node.get_text(" ", strip=True).split())
                if location_node
                else ""
            )
            if title_keywords and not any(
                keyword in normalize_text(title) for keyword in title_keywords
            ):
                continue
            if location_keywords and not any(
                keyword in normalize_text(location) for keyword in location_keywords
            ):
                continue
            job_url = urljoin(listing_url, str(link.get("href", "")))
            summaries.append({"title": title, "location": location, "job_url": job_url})
            if len(summaries) >= max_jobs:
                break

        result: list[JobCandidate] = []
        for index, summary in enumerate(summaries):
            posting: dict[str, Any] = {}
            if index < max_details:
                posting = self._fetch_posting(summary["job_url"])
            location = location_string(posting.get("jobLocation")) or summary["location"]
            city, province, country = infer_location_parts(location)
            description = html_to_text(posting.get("description"))
            identifier = posting.get("identifier")
            if isinstance(identifier, dict):
                identifier = identifier.get("value") or identifier.get("name")
            external_id = str(identifier or summary["job_url"].rstrip("/").rsplit("/", 1)[-1])
            result.append(
                self.candidate(
                    external_job_id=external_id,
                    title=str(posting.get("title") or summary["title"]),
                    location=location,
                    city=city,
                    province=province,
                    country=country,
                    remote_type=infer_remote_type(
                        str(posting.get("jobLocationType", "")), location, description
                    ),
                    description=description,
                    job_url=str(posting.get("url") or summary["job_url"]),
                    source_url=listing_url,
                    date_posted=parse_datetime(posting.get("datePosted")),
                    employment_type=self._join(posting.get("employmentType")),
                    raw_data=posting or summary,
                )
            )
        return result

    def _fetch_posting(self, job_url: str) -> dict[str, Any]:
        soup = BeautifulSoup(self.client.request("GET", job_url).text, "html.parser")
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                value = json.loads(script.string or script.get_text())
            except (json.JSONDecodeError, TypeError):
                continue
            posting = self._find_posting(value)
            if posting:
                return posting
        description = soup.select_one(".jv-job-detail-description")
        return {"description": str(description) if description else ""}

    @classmethod
    def _find_posting(cls, value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            kind = value.get("@type")
            if kind == "JobPosting" or isinstance(kind, list) and "JobPosting" in kind:
                return value
            for child in value.values():
                posting = cls._find_posting(child)
                if posting:
                    return posting
        elif isinstance(value, list):
            for child in value:
                posting = cls._find_posting(child)
                if posting:
                    return posting
        return None

    def _option_strings(self, key: str) -> list[str]:
        value = self.company.options.get(key, [])
        values = [value] if isinstance(value, str) else value
        return [normalize_text(str(item)) for item in values if str(item).strip()]

    @staticmethod
    def _join(value: Any) -> str:
        if isinstance(value, list):
            return ", ".join(str(item) for item in value)
        return str(value or "")
