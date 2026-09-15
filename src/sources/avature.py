from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit, parse_qsl

from bs4 import BeautifulSoup, Tag

from src.models.job import JobCandidate
from src.sources.base import JobSource, SourceError
from src.sources.normalization import infer_location_parts, infer_remote_type
from src.utils.dates import parse_datetime
from src.utils.text import html_to_text, normalize_text


_JOB_ID = re.compile(r"(?:Job\s*ID\s*:?\s*)?(\d{4,})", re.IGNORECASE)


class AvatureSource(JobSource):
    """Read public Avature search results, including Siemens' career portal."""

    source_name = "avature"

    def fetch_jobs(self) -> list[JobCandidate]:
        base_url = self.company.search_url or self.company.careers_url
        if not base_url:
            raise SourceError("Avature requires search_url or careers_url")
        terms = self.company.options.get("search_terms", [""])
        if isinstance(terms, str):
            terms = [terms]
        location_term = str(self.company.options.get("location_term", "")).strip()
        page_size = max(1, min(100, int(self.company.options.get("page_size", 100))))
        max_pages = max(1, int(self.company.options.get("max_pages", 3)))
        listings: dict[str, dict[str, str]] = {}

        for term in terms or [""]:
            query = " ".join(part for part in (str(term).strip(), location_term) if part)
            for page in range(max_pages):
                url = self._search_url(base_url, query, page * page_size, page_size)
                soup = BeautifulSoup(self.client.request("GET", url).text, "html.parser")
                batch = self._parse_results(soup, url)
                added = 0
                for item in batch:
                    if item["job_url"] not in listings:
                        listings[item["job_url"]] = item
                        added += 1
                if not batch or (page > 0 and added == 0) or len(batch) < page_size:
                    break

        title_keywords = self._option_strings("include_title_keywords")
        selected = [
            item
            for item in listings.values()
            if not title_keywords
            or any(keyword in normalize_text(item["title"]) for keyword in title_keywords)
        ]
        location_keywords = self._option_strings("location_keywords")
        fetch_details = bool(self.company.options.get("fetch_details", True))
        max_details = max(0, int(self.company.options.get("max_details", 100)))
        result: list[JobCandidate] = []
        for index, item in enumerate(selected):
            detail: dict[str, str] = {}
            if fetch_details and index < max_details:
                detail = self._fetch_detail(item["job_url"])
            description = detail.get("description", "")
            location = detail.get("location") or item["location"]
            if location_keywords and not any(
                keyword in normalize_text(location) for keyword in location_keywords
            ):
                continue
            city, province, country = infer_location_parts(location)
            result.append(
                self.candidate(
                    external_job_id=item["external_job_id"],
                    title=item["title"],
                    location=location,
                    city=city,
                    province=province,
                    country=country,
                    remote_type=infer_remote_type(location, description, detail.get("work_mode", "")),
                    description=description,
                    job_url=item["job_url"],
                    source_url=base_url,
                    date_posted=parse_datetime(detail.get("date_posted")),
                    employment_type=detail.get("employment_type", ""),
                    experience_level=detail.get("experience_level", ""),
                    raw_data={"listing": item, "detail": detail},
                )
            )
        return result

    def _option_strings(self, key: str) -> list[str]:
        value = self.company.options.get(key, [])
        values = [value] if isinstance(value, str) else value
        return [normalize_text(str(item)) for item in values if str(item).strip()]

    @staticmethod
    def _search_url(base_url: str, search: str, offset: int, page_size: int) -> str:
        parsed = urlsplit(base_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query.update(
            {
                "search": search,
                "folderRecordsPerPage": str(page_size),
                "folderOffset": str(offset),
            }
        )
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))

    @staticmethod
    def _parse_results(soup: BeautifulSoup, page_url: str) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for article in soup.select("article.article--result, article.article"):
            link = article.select_one("h2 a[href], h3 a[href], a.link[href]")
            if not isinstance(link, Tag):
                continue
            title = " ".join(link.get_text(" ", strip=True).split())
            job_url = urljoin(page_url, str(link.get("href", "")))
            id_element = article.select_one(".list-item-jobId, [class*='jobId']")
            id_text = id_element.get_text(" ", strip=True) if id_element else job_url
            match = _JOB_ID.search(id_text) or _JOB_ID.search(job_url)
            location_element = article.select_one(".list-item-location, [class*='location']")
            location = (
                " ".join(location_element.get_text(" ", strip=True).split())
                if location_element
                else ""
            )
            if title and job_url:
                result.append(
                    {
                        "external_job_id": match.group(1) if match else job_url,
                        "title": title,
                        "location": location,
                        "job_url": job_url,
                    }
                )
        return result

    def _fetch_detail(self, job_url: str) -> dict[str, str]:
        soup = BeautifulSoup(self.client.request("GET", job_url).text, "html.parser")
        content = soup.select_one(".article__content, .job-detail, main")
        text = html_to_text(str(content)) if content else ""
        fields = self._labeled_fields(content)
        return {
            "description": text,
            "date_posted": fields.get("posted since", ""),
            "employment_type": fields.get("employment type", "") or fields.get("job type", ""),
            "experience_level": fields.get("experience level", ""),
            "work_mode": fields.get("work mode", ""),
            "location": fields.get("location(s)", ""),
        }

    @staticmethod
    def _labeled_fields(container: Tag | None) -> dict[str, str]:
        if not container:
            return {}
        result: dict[str, str] = {}
        lines = [" ".join(value.split()) for value in container.stripped_strings if value.strip()]
        labels = {
            "posted since",
            "employment type",
            "experience level",
            "job type",
            "location(s)",
            "work mode",
        }
        for index, line in enumerate(lines[:-1]):
            key = line.rstrip(":").casefold()
            if key in labels:
                result[key] = lines[index + 1]
        return result
