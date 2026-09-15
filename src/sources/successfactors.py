from __future__ import annotations

import re
from collections import deque
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from src.models.job import JobCandidate
from src.sources.base import JobSource, SourceError
from src.sources.normalization import infer_location_parts, infer_remote_type
from src.utils.dates import parse_datetime
from src.utils.text import html_to_text, normalize_text


_NUMBER_PATTERN = re.compile(r"(?<!\d)(\d{5,})(?!\d)")


class SuccessFactorsSource(JobSource):
    """Read public SAP SuccessFactors Recruiting Marketing job pages."""

    source_name = "successfactors"

    def fetch_jobs(self) -> list[JobCandidate]:
        listing_url = self.company.search_url or self.company.careers_url
        if not listing_url:
            raise SourceError("SuccessFactors requires search_url or careers_url")

        maximum_pages = max(1, int(self.company.options.get("max_pages", 20)))
        queue: deque[str] = deque([listing_url])
        visited: set[str] = set()
        listings: dict[str, dict[str, Any]] = {}

        while queue and len(visited) < maximum_pages:
            page_url = queue.popleft()
            if page_url in visited:
                continue
            visited.add(page_url)
            response = self.client.request("GET", page_url)
            soup = BeautifulSoup(response.text, "html.parser")
            for item in self._parse_listing(soup, page_url):
                identity = str(item.get("external_job_id") or item["job_url"])
                listings.setdefault(identity, item)
            for link in soup.find_all("a", href=True):
                href = str(link.get("href", ""))
                if "startrow=" not in href.casefold():
                    continue
                candidate = urljoin(page_url, href)
                if candidate not in visited and candidate not in queue:
                    queue.append(candidate)

        title_keywords = self._option_strings("include_title_keywords")
        location_keywords = self._option_strings("location_keywords")
        selected = [
            item
            for item in listings.values()
            if not title_keywords
            or any(keyword in normalize_text(str(item["title"])) for keyword in title_keywords)
        ]
        if location_keywords:
            selected = [
                item
                for item in selected
                if self._matches_location(str(item.get("location", "")), location_keywords)
            ]
        selected = selected[: max(1, int(self.company.options.get("max_jobs", 500)))]

        fetch_details = bool(self.company.options.get("fetch_details", True))
        max_details = max(0, int(self.company.options.get("max_details", 100)))
        result: list[JobCandidate] = []
        for index, item in enumerate(selected):
            description = ""
            detail: dict[str, str] = {}
            raw: dict[str, Any] = dict(item)
            date_posted = parse_datetime(item.get("date_posted"))
            employment_type = ""
            if fetch_details and index < max_details:
                detail = self._fetch_detail(str(item["job_url"]))
                raw["detail"] = detail
                description = str(detail.get("description", ""))
                date_posted = parse_datetime(detail.get("date_posted")) or date_posted
                employment_type = str(detail.get("employment_type", ""))

            location = str(detail.get("location") or item.get("location", ""))
            city, province, country = infer_location_parts(location)
            result.append(
                self.candidate(
                    external_job_id=item.get("external_job_id"),
                    title=str(item["title"]),
                    location=location,
                    city=city,
                    province=province,
                    country=country,
                    remote_type=infer_remote_type(location, description),
                    description=description,
                    job_url=str(item["job_url"]),
                    source_url=listing_url,
                    date_posted=date_posted,
                    employment_type=employment_type,
                    raw_data=raw,
                )
            )
        return result

    def _parse_listing(self, soup: BeautifulSoup, page_url: str) -> list[dict[str, Any]]:
        rows = soup.select("tr.data-row, li.job-tile, article.job-tile")
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            link = row.select_one("a.jobTitle-link, a.job-title, h2 a, h3 a")
            if not isinstance(link, Tag):
                continue
            title = " ".join(link.get_text(" ", strip=True).split())
            href = str(link.get("href") or row.get("data-url") or "").strip()
            if not title or not href:
                continue
            job_url = urljoin(page_url, href)
            if job_url in seen:
                continue
            seen.add(job_url)
            location = self._text(
                row,
                ".jobLocation, .job-location, [id$='-location-value'], [class*='location']",
            )
            date_posted = self._text(
                row,
                ".jobDate, [id$='-date-value'], [itemprop='datePosted']",
            )
            external_id = self._text(
                row,
                ".jobFacility, .jobReqId, [id$='-facility-value'], "
                "[id$='-reqid-value'], [id$='-adcode-value']",
            )
            id_match = _NUMBER_PATTERN.search(external_id) or _NUMBER_PATTERN.search(job_url)
            result.append(
                {
                    "external_job_id": id_match.group(1) if id_match else job_url,
                    "title": title,
                    "location": location,
                    "date_posted": date_posted,
                    "job_url": job_url,
                }
            )
        return result

    def _fetch_detail(self, job_url: str) -> dict[str, str]:
        response = self.client.request("GET", job_url)
        soup = BeautifulSoup(response.text, "html.parser")
        description = soup.select_one(
            "[itemprop='description'], .jobdescription, .job-description"
        )
        posted = soup.select_one("[itemprop='datePosted']")
        employment = soup.select_one("[itemprop='employmentType']")
        description_text = html_to_text(str(description)) if description else ""
        labeled = self._description_fields(description)
        return {
            "description": description_text,
            "date_posted": self._tag_value(posted),
            "employment_type": self._tag_value(employment) or labeled.get("status", ""),
            "location": labeled.get("location", ""),
        }

    @staticmethod
    def _description_fields(description: Tag | None) -> dict[str, str]:
        if not description:
            return {}
        result: dict[str, str] = {}
        for block in description.select("p, li, div"):
            value = " ".join(block.get_text(" ", strip=True).split())
            if ":" not in value:
                continue
            label, item = value.split(":", 1)
            key = label.strip().casefold()
            if key in {"location", "status", "employment type"} and item.strip():
                result.setdefault(key, item.strip())
        return result

    def _option_strings(self, key: str) -> list[str]:
        value = self.company.options.get(key, [])
        values = [value] if isinstance(value, str) else value
        return [normalize_text(str(item)) for item in values if str(item).strip()]

    @staticmethod
    def _matches_location(location: str, keywords: list[str]) -> bool:
        city, province, country = infer_location_parts(location)
        haystack = normalize_text(" ".join((location, city, province, country)))
        return any(keyword in haystack for keyword in keywords)

    @staticmethod
    def _text(row: Tag, selector: str) -> str:
        element = row.select_one(selector)
        return " ".join(element.get_text(" ", strip=True).split()) if element else ""

    @staticmethod
    def _tag_value(tag: Tag | None) -> str:
        if not tag:
            return ""
        return str(tag.get("content") or tag.get("datetime") or tag.get_text(" ", strip=True))
