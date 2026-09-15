from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from src.models.job import JobCandidate
from src.sources.base import JobSource, SourceError
from src.sources.normalization import infer_location_parts, infer_remote_type
from src.utils.dates import parse_datetime


_REQUISITION = re.compile(r"(?:Requisition\s*#?\s*)?(\d{5,})", re.IGNORECASE)


class SelectMindsSource(JobSource):
    """Read the public first page of Oracle SelectMinds career sites."""

    source_name = "selectminds"

    def fetch_jobs(self) -> list[JobCandidate]:
        url = self.company.search_url or self.company.careers_url
        if not url:
            raise SourceError("SelectMinds requires search_url or careers_url")
        options = self.company.options
        title_keywords = [
            str(keyword).casefold()
            for keyword in options.get("include_title_keywords", [])
            if str(keyword).strip()
        ]
        max_jobs = max(1, int(options.get("max_jobs", 100)))
        soup = BeautifulSoup(self.client.request("GET", url).text, "html.parser")
        result: list[JobCandidate] = []
        for row in soup.select(".job_list_row, [id^='job_list_']"):
            link = row.select_one("a.job_link[href]")
            if not isinstance(link, Tag):
                continue
            title = " ".join(link.get_text(" ", strip=True).split())
            if title_keywords and not any(keyword in title.casefold() for keyword in title_keywords):
                continue
            job_url = urljoin(url, str(link.get("href", "")))
            location_element = row.select_one("a.location, .location")
            location = (
                " ".join(location_element.get_text(" ", strip=True).split())
                if location_element
                else ""
            )
            description_element = row.select_one(".jlr_description")
            description = (
                " ".join(description_element.get_text(" ", strip=True).split())
                if description_element
                else ""
            )
            requisition_element = row.select_one(".job_external_id")
            requisition_text = requisition_element.get_text(" ", strip=True) if requisition_element else job_url
            match = _REQUISITION.search(requisition_text) or _REQUISITION.search(job_url)
            date_element = row.select_one(".job_date, [class*='date'] .field_value")
            date_posted = date_element.get_text(" ", strip=True) if date_element else ""
            city, province, country = infer_location_parts(location)
            result.append(
                self.candidate(
                    external_job_id=match.group(1) if match else job_url,
                    title=title,
                    location=location,
                    city=city,
                    province=province,
                    country=country,
                    remote_type=infer_remote_type(location, description),
                    description=description,
                    job_url=job_url,
                    source_url=url,
                    date_posted=parse_datetime(date_posted),
                    raw_data={"listing_text": " ".join(row.get_text(" ", strip=True).split())},
                )
            )
            if len(result) >= max_jobs:
                break
        return result
