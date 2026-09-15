from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from src.models.job import JobCandidate
from src.sources.base import JobSource, SourceError
from src.sources.normalization import infer_location_parts, infer_remote_type, location_string
from src.utils.dates import parse_datetime
from src.utils.text import html_to_text, normalize_text


_PAGE_NUMBER = re.compile(r"/page/(\d+)")


class ParadoxSource(JobSource):
    """Read public, server-rendered Paradox career-site listings and JSON-LD."""

    source_name = "paradox"

    def fetch_jobs(self) -> list[JobCandidate]:
        search_url = self.company.search_url or self.company.careers_url
        if not search_url:
            raise SourceError("Paradox requires search_url or careers_url")
        first_soup = self._get_soup(search_url)
        maximum_pages = max(1, int(self.company.options.get("max_pages", 20)))
        page_count = min(maximum_pages, self._page_count(first_soup))
        listings: dict[str, dict[str, str]] = {}
        for page in range(1, page_count + 1):
            soup = first_soup if page == 1 else self._get_soup(self._page_url(search_url, page))
            for item in self._parse_listing(soup, search_url):
                listings.setdefault(item["external_job_id"], item)

        title_keywords = self._option_strings("include_title_keywords")
        selected = [
            item
            for item in listings.values()
            if not title_keywords
            or any(keyword in normalize_text(item["title"]) for keyword in title_keywords)
        ]
        selected = selected[: max(1, int(self.company.options.get("max_jobs", 250)))]
        max_details = max(0, int(self.company.options.get("max_details", 150)))
        fetch_details = bool(self.company.options.get("fetch_details", True))

        result: list[JobCandidate] = []
        for index, listing in enumerate(selected):
            detail: dict[str, Any] = {}
            if fetch_details and index < max_details:
                detail = self._detail(listing["job_url"])
            location = location_string(detail.get("jobLocation")) or listing["location"]
            city, province, country = infer_location_parts(location)
            description = html_to_text(str(detail.get("description") or ""))
            result.append(
                self.candidate(
                    external_job_id=listing["external_job_id"],
                    title=str(detail.get("title") or listing["title"]),
                    location=location,
                    city=city,
                    province=province,
                    country=country,
                    remote_type=infer_remote_type(
                        str(detail.get("jobLocationType") or ""), location, description
                    ),
                    description=description,
                    job_url=listing["job_url"],
                    source_url=search_url,
                    date_posted=parse_datetime(
                        detail.get("datePosted") or listing.get("date_posted")
                    ),
                    employment_type=self._join(detail.get("employmentType")),
                    raw_data={"listing": listing, "detail": detail},
                )
            )
        return result

    def _get_soup(self, url: str) -> BeautifulSoup:
        response = self.client.request("GET", url)
        content = getattr(response, "content", b"")
        text = content.decode("utf-8", errors="replace") if content else response.text
        return BeautifulSoup(text, "html.parser")

    @staticmethod
    def _parse_listing(soup: BeautifulSoup, page_url: str) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for row in soup.select("li.results-list__item"):
            link = row.select_one("a.results-list__item-title--link[href]")
            if not isinstance(link, Tag):
                continue
            job_url = urljoin(page_url, str(link.get("href", "")))
            req = row.select_one(".results-list__req-id--label")
            external_id = req.get_text(" ", strip=True) if req else job_url
            location = row.select_one(".results-list__item-street--label")
            date_posted = row.select_one(".results-list__custom2--label")
            result.append(
                {
                    "external_job_id": external_id,
                    "title": " ".join(link.get_text(" ", strip=True).split()),
                    "location": location.get_text(" ", strip=True) if location else "",
                    "date_posted": date_posted.get_text(" ", strip=True) if date_posted else "",
                    "job_url": job_url,
                }
            )
        return result

    def _detail(self, url: str) -> dict[str, Any]:
        soup = self._get_soup(url)
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                value = json.loads(script.string or script.get_text())
            except (json.JSONDecodeError, TypeError):
                continue
            posting = self._find_posting(value)
            if posting:
                return posting
        description = soup.select_one(".job-description")
        return {"description": str(description) if description else ""}

    @classmethod
    def _find_posting(cls, value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            kind = value.get("@type")
            if kind == "JobPosting" or isinstance(kind, list) and "JobPosting" in kind:
                return value
            for child in value.values():
                found = cls._find_posting(child)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = cls._find_posting(child)
                if found:
                    return found
        return None

    @staticmethod
    def _page_count(soup: BeautifulSoup) -> int:
        last = soup.select_one("a.page-link-last[href]")
        match = _PAGE_NUMBER.search(str(last.get("href", ""))) if last else None
        return int(match.group(1)) if match else 1

    @staticmethod
    def _page_url(search_url: str, page: int) -> str:
        parsed = urlsplit(search_url)
        path = re.sub(r"/page/\d+/?$", "", parsed.path.rstrip("/"))
        path = f"{path}/page/{page}"
        return urlunsplit(
            (parsed.scheme, parsed.netloc, path, urlencode(parse_qsl(parsed.query)), "")
        )

    def _option_strings(self, key: str) -> list[str]:
        value = self.company.options.get(key, [])
        values = [value] if isinstance(value, str) else value
        return [normalize_text(str(item)) for item in values if str(item).strip()]

    @staticmethod
    def _join(value: Any) -> str:
        if isinstance(value, list):
            return ", ".join(str(item) for item in value)
        return str(value or "")
