from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from src.models.job import JobCandidate
from src.sources.base import JobSource, SourceError
from src.sources.normalization import infer_location_parts, infer_remote_type
from src.utils.dates import parse_datetime
from src.utils.text import html_to_text, normalize_text


class TaleoBusinessSource(JobSource):
    """Read public Oracle Taleo Business Edition v2 career centers."""

    source_name = "taleo_business"

    def fetch_jobs(self) -> list[JobCandidate]:
        listing_url = self.company.search_url or self.company.careers_url
        if not listing_url:
            raise SourceError("Taleo Business Edition requires a public jobSearch URL")

        parsed = urlsplit(listing_url)
        query = parse_qs(parsed.query)
        org = str(self.company.options.get("org") or self._first(query.get("org"))).strip()
        cws = str(self.company.options.get("cws") or self._first(query.get("cws"))).strip()
        if not org or not cws:
            raise SourceError("Taleo Business Edition requires org and cws options or URL parameters")

        root_path = parsed.path.rsplit("/", 1)[0]
        results_url = urlunsplit(
            (parsed.scheme, parsed.netloc, f"{root_path}/searchResults", "", "")
        )
        response = self.client.request(
            "POST",
            results_url,
            params={"org": org, "cws": cws},
            data={
                "act": "search",
                "org": org,
                "cws": cws,
                "WebPage": "JSRCH_V2",
                "WebVersion": "0",
                "location": str(self.company.options.get("location_id", "-1")),
            },
        )

        maximum_pages = max(1, int(self.company.options.get("max_pages", 20)))
        rows: dict[str, dict[str, Any]] = {}
        for page in range(maximum_pages):
            soup = BeautifulSoup(response.text, "html.parser")
            batch = self._parse_listing(soup, results_url)
            for item in batch:
                rows.setdefault(str(item["external_job_id"]), item)
            if len(batch) < 10 or not soup.select_one("a[rel='next'], a[aria-label='next'], a[href*='rowFrom=']"):
                break
            response = self.client.request(
                "GET",
                results_url,
                params={
                    "next": "",
                    "rowFrom": (page + 1) * 10,
                    "act": "search",
                    "sortColumn": "null",
                    "sortOrder": "null",
                    "org": org,
                    "cws": cws,
                },
            )

        title_keywords = self._option_strings("include_title_keywords")
        selected = [
            item
            for item in rows.values()
            if not title_keywords
            or any(keyword in normalize_text(str(item["title"])) for keyword in title_keywords)
        ]
        selected = selected[: max(1, int(self.company.options.get("max_jobs", 250)))]

        fetch_details = bool(self.company.options.get("fetch_details", True))
        max_details = max(0, int(self.company.options.get("max_details", 100)))
        result: list[JobCandidate] = []
        for index, item in enumerate(selected):
            description = ""
            raw = dict(item)
            if fetch_details and index < max_details:
                detail_response = self.client.request("GET", str(item["job_url"]))
                detail_soup = BeautifulSoup(detail_response.text, "html.parser")
                description_tag = detail_soup.select_one(
                    ".col-md-8, [itemprop='description'], .job-description"
                )
                description = html_to_text(str(description_tag)) if description_tag else ""
                raw["description"] = description

            location = str(item.get("location", ""))
            city, province, country = infer_location_parts(location)
            result.append(
                self.candidate(
                    external_job_id=item["external_job_id"],
                    title=str(item["title"]),
                    location=location,
                    city=city,
                    province=province,
                    country=country,
                    remote_type=infer_remote_type(location, description),
                    description=description,
                    job_url=str(item["job_url"]),
                    source_url=listing_url,
                    date_posted=self._parse_posted_date(str(item.get("date_posted", ""))),
                    employment_type=str(item.get("employment_type", "")),
                    raw_data=raw,
                )
            )
        return result

    @staticmethod
    def _parse_listing(soup: BeautifulSoup, page_url: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in soup.select(".oracletaleocwsv2-accordion"):
            link = row.select_one("a.viewJobLink[href*='viewRequisition']")
            info = row.select_one(".oracletaleocwsv2-accordion-head-info")
            if not isinstance(link, Tag) or not isinstance(info, Tag):
                continue
            href = urljoin(page_url, str(link.get("href") or ""))
            rid = self_query_value(href, "rid")
            title = " ".join(link.get_text(" ", strip=True).split())
            values = [
                " ".join(tag.get_text(" ", strip=True).split())
                for tag in info.find_all("div", recursive=False)
            ]
            if not rid or not title:
                continue
            result.append(
                {
                    "external_job_id": rid,
                    "title": title,
                    "location": values[0] if values else "",
                    "employment_type": values[1] if len(values) > 1 else "",
                    "date_posted": values[2] if len(values) > 2 else "",
                    "job_url": href,
                }
            )
        return result

    def _option_strings(self, key: str) -> list[str]:
        value = self.company.options.get(key, [])
        values = [value] if isinstance(value, str) else value
        return [normalize_text(str(item)) for item in values if str(item).strip()]

    @staticmethod
    def _first(value: list[str] | None) -> str:
        return str(value[0]) if value else ""

    @staticmethod
    def _parse_posted_date(value: str) -> datetime | None:
        parsed = parse_datetime(value)
        if parsed is not None:
            return parsed
        for pattern in ("%d/%m/%Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(value, pattern)
            except ValueError:
                pass
        return None


def self_query_value(url: str, key: str) -> str:
    values = parse_qs(urlsplit(url).query).get(key, [])
    return str(values[0]) if values else ""
