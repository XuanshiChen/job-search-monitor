from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from src.sources.base import JobSource, SourceError
from src.sources.normalization import infer_location_parts, infer_remote_type, location_string
from src.utils.dates import parse_datetime
from src.utils.text import html_to_text, normalize_text


class CornerstoneSource(JobSource):
    """Public Cornerstone OnDemand (CSOD) career-site adapter."""

    source_name = "cornerstone"

    def fetch_jobs(self) -> list:
        host, career_site_id, corp, culture_id, culture_name = self._settings()
        landing_url = (
            f"{host}/ux/ats/careersite/{career_site_id}/home"
            f"?c={corp}&lang={culture_name}"
        )
        response = self.client.request("GET", landing_url)
        context = self._context(response.text)
        token = str(context.get("token") or "")
        if not token:
            raise SourceError("Cornerstone public career page did not provide a session token")
        headers = {"Authorization": f"Bearer {token}"}

        search_url = f"{host}/services/x/career-site/v1/search"
        page_size = max(1, min(100, int(self.company.options.get("page_size", 50))))
        max_pages = max(1, int(self.company.options.get("max_pages", 10)))
        terms = self._option_values("search_terms") or [""]
        country_codes = self._option_values("country_codes")
        summaries: dict[str, dict[str, Any]] = {}
        for term in terms:
            for page_number in range(1, max_pages + 1):
                body = {
                    "careerSiteId": career_site_id,
                    "careerSitePageId": career_site_id,
                    "pageNumber": page_number,
                    "pageSize": page_size,
                    "cultureId": culture_id,
                    "searchText": term,
                    "cultureName": culture_name,
                    "states": [],
                    "countryCodes": country_codes,
                    "cities": [],
                    "placeID": "",
                    "radius": "",
                    "postingsWithinDays": "",
                    "customFieldCheckboxKeys": [],
                    "customFieldDropdowns": [],
                    "customFieldRadios": [],
                }
                payload = self.client.post_json(search_url, headers=headers, json=body)
                data = payload.get("data", payload) if isinstance(payload, dict) else {}
                batch = data.get("requisitions", []) if isinstance(data, dict) else []
                for raw in batch:
                    identity = str(raw.get("requisitionId") or raw.get("id") or raw.get("ref") or "")
                    if identity:
                        summaries.setdefault(identity, raw)
                total = int(data.get("totalCount") or len(batch)) if isinstance(data, dict) else 0
                if not batch or page_number * page_size >= total:
                    break

        title_keywords = [normalize_text(value) for value in self._option_values("include_title_keywords")]
        if title_keywords:
            summaries = {
                identity: raw
                for identity, raw in summaries.items()
                if any(
                    keyword
                    in normalize_text(
                        str(
                            raw.get("displayTitle")
                            or raw.get("displayJobTitle")
                            or raw.get("title")
                            or ""
                        )
                    )
                    for keyword in title_keywords
                )
            }

        max_jobs = max(1, int(self.company.options.get("max_jobs", 200)))
        max_details = max(0, int(self.company.options.get("max_details", max_jobs)))
        result = []
        for index, (identity, summary) in enumerate(list(summaries.items())[:max_jobs]):
            raw = summary
            if index < max_details:
                detail_url = (
                    f"{host}/services/x/job-requisition/v2/requisitions/{identity}/jobDetails"
                    f"?cultureId={culture_id}"
                )
                payload = self.client.get_json(detail_url, headers=headers)
                detail = payload.get("data", payload) if isinstance(payload, dict) else {}
                if isinstance(detail, dict):
                    raw = {**summary, **detail}

            location = self._location(raw)
            city, province, country = infer_location_parts(location)
            description = html_to_text(
                raw.get("externalDescription")
                or raw.get("description")
                or raw.get("jobDescription")
            )
            public_url = str(raw.get("companyApplyUrl") or raw.get("applyUrl") or "")
            if not public_url:
                public_url = (
                    f"{host}/ux/ats/careersite/{career_site_id}/home/requisition/{identity}"
                    f"?c={corp}&lang={culture_name}"
                )
            result.append(
                self.candidate(
                    external_job_id=raw.get("ref") or raw.get("requisitionId") or identity,
                    title=str(
                        raw.get("displayTitle")
                        or raw.get("displayJobTitle")
                        or raw.get("title")
                        or "Untitled job"
                    ),
                    location=location,
                    city=city,
                    province=province,
                    country=country,
                    remote_type=infer_remote_type(location, description),
                    description=description,
                    job_url=public_url,
                    source_url=search_url,
                    date_posted=parse_datetime(
                        raw.get("openDate")
                        or raw.get("postingEffectiveDate")
                        or raw.get("postedDate")
                    ),
                    employment_type=str(raw.get("employmentType") or raw.get("jobType") or ""),
                    raw_data=raw,
                )
            )
        return result

    def _settings(self) -> tuple[str, int, str, int, str]:
        options = self.company.options
        parsed = urlsplit(self.company.search_url or self.company.careers_url)
        host = str(options.get("host") or f"{parsed.scheme}://{parsed.netloc}").rstrip("/")
        career_site_id = int(options.get("career_site_id", 0))
        corp = str(options.get("corp") or "").strip()
        culture_id = int(options.get("culture_id", 1))
        culture_name = str(options.get("culture_name", "en-US")).strip()
        if not host or not parsed.netloc or not career_site_id or not corp:
            raise SourceError(
                "Cornerstone requires options.career_site_id and options.corp on a CSOD URL"
            )
        return host, career_site_id, corp, culture_id, culture_name

    @staticmethod
    def _context(html: str) -> dict[str, Any]:
        match = re.search(r"csod\.context\s*=\s*(\{.*?\})\s*;", html, re.DOTALL)
        if not match:
            raise SourceError("Cornerstone public context was not found")
        try:
            value = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise SourceError("Cornerstone public context was invalid JSON") from exc
        return value if isinstance(value, dict) else {}

    def _option_values(self, key: str) -> list[str]:
        value = self.company.options.get(key, [])
        values = [value] if isinstance(value, str) else value
        return [str(item).strip() for item in values if str(item).strip()]

    @staticmethod
    def _location(raw: dict[str, Any]) -> str:
        primary = raw.get("primaryLocation") or raw.get("location") or ""
        if not primary and raw.get("locations"):
            locations = raw.get("locations")
            primary = locations[0] if isinstance(locations, list) and locations else locations
        locations = [CornerstoneSource._location_value(primary)]
        additional = raw.get("additionalLocations") or []
        if not additional and isinstance(raw.get("locations"), list):
            additional = raw["locations"][1:]
        if not isinstance(additional, list):
            additional = [additional]
        locations.extend(CornerstoneSource._location_value(item) for item in additional)
        return "; ".join(dict.fromkeys(item for item in locations if item))

    @staticmethod
    def _location_value(value: Any) -> str:
        if isinstance(value, dict):
            parts = [
                value.get("city") or value.get("addressLocality"),
                value.get("state") or value.get("region") or value.get("addressRegion"),
                value.get("country") or value.get("countryName") or value.get("addressCountry"),
            ]
            rendered = ", ".join(str(item) for item in parts if item)
            return rendered or location_string(value)
        return location_string(value)
