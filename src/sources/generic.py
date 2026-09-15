from __future__ import annotations

import json
from typing import Any, Iterable
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from src.sources.base import JobSource, SourceError
from src.sources.normalization import infer_location_parts, infer_remote_type, location_string
from src.utils.dates import parse_datetime
from src.utils.text import html_to_text, normalize_text


class GenericHtmlSource(JobSource):
    source_name = "generic_html"

    def fetch_jobs(self) -> list:
        url = self.company.search_url or self.company.careers_url
        if not url:
            raise SourceError("Generic HTML source requires search_url or careers_url")
        result = []
        seen: set[str] = set()
        for page_url in self._page_urls(url):
            response = self.client.request("GET", page_url)
            soup = BeautifulSoup(response.text, "html.parser")
            page_jobs = self._json_ld_jobs(soup, page_url)
            if not page_jobs:
                page_jobs = self._selector_jobs(soup, page_url)
            if not page_jobs and len(self._page_urls(url)) > 1:
                break
            for job in page_jobs:
                identity = job.external_job_id or job.job_url
                if identity not in seen:
                    seen.add(identity)
                    result.append(job)
        if not result:
            raise SourceError(
                "No JobPosting JSON-LD or configured listing selectors were found; "
                "set options.selectors for this career page"
            )

        title_keywords = self._option_strings("include_title_keywords")
        if title_keywords:
            result = [
                job
                for job in result
                if any(keyword in normalize_text(job.title) for keyword in title_keywords)
            ]
        result = result[: max(1, int(self.company.options.get("max_jobs", 500)))]

        detail_selector = str(self.company.options.get("detail_description_selector", "")).strip()
        max_details = max(0, int(self.company.options.get("max_details", 100)))
        if detail_selector:
            for job in result[:max_details]:
                detail_response = self.client.request("GET", job.job_url)
                detail_soup = BeautifulSoup(detail_response.text, "html.parser")
                parts = detail_soup.select(detail_selector)
                if parts:
                    job.description = "\n\n".join(
                        filter(None, (html_to_text(str(part)) for part in parts))
                    )
        return result

    def _page_urls(self, base_url: str) -> list[str]:
        page_param = str(self.company.options.get("page_param", "")).strip()
        if not page_param:
            return [base_url]
        page_start = int(self.company.options.get("page_start", 1))
        max_pages = max(1, int(self.company.options.get("max_pages", 1)))
        parsed = urlsplit(base_url)
        original_query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        urls = []
        for page in range(page_start, page_start + max_pages):
            query = {**original_query, page_param: str(page)}
            urls.append(
                urlunsplit(
                    (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), "")
                )
            )
        return urls

    def _option_strings(self, key: str) -> list[str]:
        value = self.company.options.get(key, [])
        values = [value] if isinstance(value, str) else value
        return [normalize_text(str(item)) for item in values if str(item).strip()]

    def _json_ld_jobs(self, soup: BeautifulSoup, base_url: str) -> list:
        postings: list[dict[str, Any]] = []
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                value = json.loads(script.string or script.get_text())
            except (json.JSONDecodeError, TypeError):
                continue
            postings.extend(self._find_postings(value))

        result = []
        for raw in postings:
            location = location_string(raw.get("jobLocation")) or str(raw.get("jobLocationType", ""))
            city, province, country = infer_location_parts(location)
            description = html_to_text(raw.get("description"))
            identifier = raw.get("identifier")
            if isinstance(identifier, dict):
                identifier = identifier.get("value") or identifier.get("name")
            result.append(
                self.candidate(
                    external_job_id=identifier,
                    title=str(raw.get("title", "Untitled job")),
                    location=location,
                    city=city,
                    province=province,
                    country=country,
                    remote_type=infer_remote_type(
                        str(raw.get("jobLocationType", "")), location, description
                    ),
                    description=description,
                    job_url=urljoin(base_url, str(raw.get("url", base_url))),
                    date_posted=parse_datetime(raw.get("datePosted")),
                    employment_type=_join_value(raw.get("employmentType")),
                    raw_data=raw,
                )
            )
        return result

    def _find_postings(self, value: Any) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        if isinstance(value, dict):
            kind = value.get("@type")
            if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                result.append(value)
            for child in value.values():
                result.extend(self._find_postings(child))
        elif isinstance(value, list):
            for child in value:
                result.extend(self._find_postings(child))
        return result

    def _selector_jobs(self, soup: BeautifulSoup, base_url: str) -> list:
        selectors = self.company.options.get("selectors", {})
        item_selector = selectors.get("item")
        if not item_selector:
            return []
        result = []
        for index, node in enumerate(soup.select(str(item_selector))):
            title_node = node.select_one(str(selectors.get("title", "a")))
            link_node = node.select_one(str(selectors.get("link", "a")))
            if not title_node or not link_node:
                continue
            location_node = node.select_one(str(selectors.get("location", ".location")))
            description_node = node.select_one(str(selectors.get("description", ".description")))
            date_node = node.select_one(str(selectors.get("date_posted", ".date-posted")))
            employment_node = node.select_one(
                str(selectors.get("employment_type", ".employment-type"))
            )
            location = location_node.get_text(" ", strip=True) if location_node else ""
            description = description_node.get_text(" ", strip=True) if description_node else ""
            href = str(link_node.get("href", ""))
            job_url = urljoin(base_url, href)
            external_id = node.get(str(selectors.get("id_attribute", "data-job-id")))
            id_query_param = str(selectors.get("id_query_param", "")).strip()
            if not external_id and id_query_param:
                values = parse_qs(urlsplit(job_url).query).get(id_query_param, [])
                external_id = values[0] if values else None
            city, province, country = infer_location_parts(location)
            result.append(
                self.candidate(
                    external_job_id=external_id,
                    title=title_node.get_text(" ", strip=True),
                    location=location,
                    city=city,
                    province=province,
                    country=country,
                    remote_type=infer_remote_type(location, description),
                    description=description,
                    job_url=job_url,
                    date_posted=parse_datetime(
                        date_node.get_text(" ", strip=True) if date_node else None
                    ),
                    employment_type=(
                        employment_node.get_text(" ", strip=True) if employment_node else ""
                    ),
                    raw_data={"selector_index": index},
                )
            )
        return result


def _join_value(value: Any) -> str:
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
        return ", ".join(str(item) for item in value)
    return str(value or "")
