from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from src.models.job import JobCandidate
from src.sources.base import JobSource, SourceError
from src.sources.normalization import infer_location_parts, infer_remote_type
from src.utils.dates import parse_datetime
from src.utils.text import html_to_text, normalize_text


class AemJobListSource(JobSource):
    """Read public Adobe Experience Manager job-list JSON endpoints."""

    source_name = "aem_joblist"

    def fetch_jobs(self) -> list[JobCandidate]:
        api_url = str(self.company.options.get("api_url") or "").strip()
        if not api_url:
            raise SourceError("AEM job list requires options.api_url")
        query = dict(self.company.options.get("query", {}))
        page_size = max(1, int(self.company.options.get("page_size", 20)))
        max_pages = max(1, int(self.company.options.get("max_pages", 20)))
        title_keywords = self._option_strings("include_title_keywords")
        jobs: dict[str, dict[str, Any]] = {}

        for page in range(max_pages):
            payload = self.client.get_json(
                f"{api_url}?{urlencode({**query, 'offset': page * page_size})}"
            )
            batch = payload.get("items", []) if isinstance(payload, dict) else []
            for raw in batch:
                url = str(raw.get("url") or "")
                identity_match = re.search(r"/(JID[^/?#]+|R\d+)(?:[/?#]|$)", url, re.I)
                identity = identity_match.group(1) if identity_match else url
                if identity:
                    jobs.setdefault(identity, raw)
            total = int(payload.get("totalNumber") or len(batch)) if isinstance(payload, dict) else 0
            if not batch or (page + 1) * page_size >= total:
                break

        selected = []
        for identity, raw in jobs.items():
            title = str(raw.get("title") or "Untitled job")
            if title_keywords and not any(
                keyword in normalize_text(title) for keyword in title_keywords
            ):
                continue
            selected.append((identity, raw))
        selected = selected[: max(1, int(self.company.options.get("max_jobs", 200)))]

        detail_selector = str(
            self.company.options.get("detail_description_selector", "")
        ).strip()
        max_details = max(0, int(self.company.options.get("max_details", 100)))
        result: list[JobCandidate] = []
        for index, (identity, raw) in enumerate(selected):
            title = str(raw.get("title") or "Untitled job")
            location = str(raw.get("location") or raw.get("primaryLocation") or "")
            city, province, country = infer_location_parts(location)
            description = ""
            if detail_selector and index < max_details:
                response = self.client.request("GET", str(raw.get("url") or ""))
                soup = BeautifulSoup(response.text, "html.parser")
                detail = soup.select_one(detail_selector)
                description = html_to_text(str(detail)) if detail else ""
            result.append(
                self.candidate(
                    external_job_id=identity,
                    title=title,
                    location=location,
                    city=city,
                    province=province,
                    country=country,
                    remote_type=str(raw.get("remoteType") or "")
                    or infer_remote_type(location, description),
                    description=description,
                    job_url=str(raw.get("url") or ""),
                    source_url=api_url,
                    date_posted=parse_datetime(raw.get("publicationDate")),
                    employment_type=str(raw.get("jobType") or raw.get("contractType") or ""),
                    experience_level=str(raw.get("experience") or ""),
                    raw_data=raw,
                )
            )
        return result

    def _option_strings(self, key: str) -> list[str]:
        value = self.company.options.get(key, [])
        values = [value] if isinstance(value, str) else value
        return [normalize_text(str(item)) for item in values if str(item).strip()]
