from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from src.sources.base import JobSource, SourceError
from src.sources.normalization import infer_location_parts, infer_remote_type
from src.utils.text import normalize_text


class ICIMSSource(JobSource):
    """Adapter for public iCIMS career portals without authentication."""

    source_name = "icims"

    def fetch_jobs(self) -> list:
        base_url = self.company.search_url or self.company.careers_url
        if not base_url:
            raise SourceError("iCIMS requires search_url or careers_url")
        max_pages = max(1, int(self.company.options.get("max_pages", 10)))
        title_keywords = self._option_keywords("include_title_keywords")
        location_keywords = self._option_keywords("location_keywords")
        summaries: dict[str, dict] = {}
        for page in range(max_pages):
            url = self._page_url(base_url, page)
            response = self.client.request("GET", url)
            soup = BeautifulSoup(response.text, "html.parser")
            items = soup.select(".iCIMS_JobsTable .iCIMS_JobCardItem")
            if not items:
                if page == 0:
                    raise SourceError("No public iCIMS job cards were found")
                break
            for node in items:
                raw = self._summary(node)
                title = str(raw.get("title") or "")
                if title_keywords and not any(keyword in normalize_text(title) for keyword in title_keywords):
                    continue
                listing_location = " ".join(str(value) for value in raw.get("fields", {}).values())
                if location_keywords and not any(
                    keyword in normalize_text(listing_location) for keyword in location_keywords
                ):
                    continue
                identity = str(raw.get("numeric_id") or raw.get("external_job_id") or raw.get("url"))
                summaries.setdefault(identity, raw)
            next_link = soup.select_one('link[rel="next"]') or soup.select_one('a[rel="next"]')
            if not next_link:
                break

        max_jobs = max(1, int(self.company.options.get("max_jobs", 200)))
        max_details = max(0, int(self.company.options.get("max_details", max_jobs)))
        result = []
        for index, raw in enumerate(list(summaries.values())[:max_jobs]):
            description = str(raw.get("description") or "")
            fields = dict(raw.get("fields") or {})
            if index < max_details:
                detail_url = self._with_iframe(str(raw["url"]))
                response = self.client.request("GET", detail_url)
                soup = BeautifulSoup(response.text, "html.parser")
                detail_fields = self._fields(soup.select_one(".iCIMS_JobHeaderGroup"))
                fields.update(detail_fields)
                descriptions = [
                    node.get_text(" ", strip=True)
                    for node in soup.select(".iCIMS_Expandable_Text")
                    if node.get_text(" ", strip=True)
                ]
                if descriptions:
                    description = "\n\n".join(dict.fromkeys(descriptions))
            location = ", ".join(
                item
                for item in (
                    fields.get("City", ""),
                    fields.get("State/Province", ""),
                    fields.get("Country", ""),
                )
                if item
            )
            city, province, country = infer_location_parts(location)
            result.append(
                self.candidate(
                    external_job_id=fields.get("Job ID") or raw.get("external_job_id"),
                    title=str(raw.get("title") or "Untitled job"),
                    location=location,
                    city=city,
                    province=province,
                    country=country,
                    remote_type=infer_remote_type(location, description),
                    description=description,
                    job_url=str(raw["url"]).replace("?in_iframe=1", ""),
                    source_url=base_url,
                    employment_type=fields.get("Type", ""),
                    raw_data={**raw, "fields": fields},
                )
            )
        return result

    @staticmethod
    def _summary(node: Tag) -> dict:
        link = node.select_one(".title a[href]")
        if not link:
            return {}
        url = str(link.get("href") or "")
        numeric_match = re.search(r"/jobs/(\d+)/", url)
        fields = ICIMSSource._fields(node.select_one(".iCIMS_JobHeaderGroup"))
        return {
            "numeric_id": numeric_match.group(1) if numeric_match else "",
            "external_job_id": fields.get("Job ID", ""),
            "title": (link.select_one("h1, h2, h3") or link).get_text(" ", strip=True),
            "url": url,
            "description": (node.select_one(".description") or node).get_text(" ", strip=True),
            "fields": fields,
        }

    @staticmethod
    def _fields(group: Tag | None) -> dict[str, str]:
        if not group:
            return {}
        result: dict[str, str] = {}
        for row in group.select(".iCIMS_JobHeaderTag"):
            label_node = row.select_one(".iCIMS_JobHeaderField")
            value_node = row.select_one(".iCIMS_JobHeaderData")
            if not label_node or not value_node:
                continue
            label = label_node.get_text(" ", strip=True)
            if not label:
                hidden = label_node.select_one(".field-label")
                label = hidden.get_text(" ", strip=True) if hidden else ""
            value = value_node.get_text(" ", strip=True)
            if label and value:
                result[label] = value
        return result

    def _option_keywords(self, key: str) -> list[str]:
        value = self.company.options.get(key, [])
        values = [value] if isinstance(value, str) else value
        return [normalize_text(str(item)) for item in values if str(item).strip()]

    @staticmethod
    def _with_iframe(url: str) -> str:
        parsed = urlsplit(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["in_iframe"] = "1"
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))

    @staticmethod
    def _page_url(base_url: str, page: int) -> str:
        parsed = urlsplit(base_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["ss"] = "1"
        query["in_iframe"] = "1"
        if page:
            query["pr"] = str(page)
        else:
            query.pop("pr", None)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
