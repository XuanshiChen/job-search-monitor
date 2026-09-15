from __future__ import annotations

from urllib.parse import urlsplit

from src.sources.base import JobSource, SourceError
from src.sources.normalization import infer_location_parts, infer_remote_type
from src.utils.dates import parse_datetime
from src.utils.text import html_to_text, normalize_text


class SmartRecruitersSource(JobSource):
    source_name = "smartrecruiters"

    def fetch_jobs(self) -> list:
        identifier = str(self.company.options.get("company_identifier", "")).strip()
        identifier = identifier or self._infer_identifier()
        if not identifier:
            raise SourceError(
                "SmartRecruiters requires options.company_identifier or a recognizable careers URL"
            )
        base_url = f"https://api.smartrecruiters.com/v1/companies/{identifier}/postings"
        limit = 100
        offset = 0
        postings = []
        while True:
            payload = self.client.get_json(base_url, params={"limit": limit, "offset": offset})
            batch = payload.get("content", []) if isinstance(payload, dict) else []
            postings.extend(batch)
            total = int(payload.get("totalFound", len(postings))) if isinstance(payload, dict) else 0
            if not batch or len(postings) >= total:
                break
            offset += len(batch)

        title_keywords = self._option_strings("include_title_keywords")
        country_codes = set(self._option_strings("country_codes"))
        postings = [
            posting
            for posting in postings
            if (
                not title_keywords
                or any(
                    keyword in normalize_text(str(posting.get("name") or ""))
                    for keyword in title_keywords
                )
            )
            and (
                not country_codes
                or normalize_text(str((posting.get("location") or {}).get("country") or ""))
                in country_codes
            )
        ]
        postings = postings[: max(1, int(self.company.options.get("max_jobs", 500)))]

        result = []
        fetch_details = bool(self.company.options.get("fetch_details", True))
        max_details = max(0, int(self.company.options.get("max_details", 100)))
        for index, summary in enumerate(postings):
            posting_id = summary.get("id")
            raw = summary
            if fetch_details and posting_id and index < max_details:
                raw = self.client.get_json(f"{base_url}/{posting_id}")
            location_data = raw.get("location") or {}
            location = ", ".join(
                str(item)
                for item in (
                    location_data.get("city"),
                    location_data.get("region"),
                    location_data.get("country"),
                )
                if item
            )
            city, province, country = infer_location_parts(location, location_data)
            sections = raw.get("jobAd", {}).get("sections", {})
            description = " ".join(
                html_to_text((sections.get(key) or {}).get("text", ""))
                for key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation")
            ).strip()
            result.append(
                self.candidate(
                    external_job_id=posting_id,
                    title=str(raw.get("name", "Untitled job")),
                    location=location,
                    city=city,
                    province=province,
                    country=country,
                    remote_type=(
                        "remote"
                        if location_data.get("remote") is True
                        else "hybrid"
                        if location_data.get("hybrid") is True
                        else infer_remote_type(location, description)
                    ),
                    description=description,
                    job_url=str(
                        raw.get("applyUrl")
                        or raw.get("jobAdUrl")
                        or summary.get("applyUrl")
                        or summary.get("jobAdUrl")
                        or raw.get("ref")
                        or summary.get("ref")
                        or ""
                    ),
                    source_url=base_url,
                    date_posted=parse_datetime(raw.get("releasedDate")),
                    employment_type=str((raw.get("typeOfEmployment") or {}).get("label", "")),
                    experience_level=str((raw.get("experienceLevel") or {}).get("label", "")),
                    raw_data=raw,
                )
            )
        return result

    def _option_strings(self, key: str) -> list[str]:
        value = self.company.options.get(key, [])
        values = [value] if isinstance(value, str) else value
        return [normalize_text(str(item)) for item in values if str(item).strip()]

    def _infer_identifier(self) -> str:
        url = self.company.search_url or self.company.careers_url
        parts = [part for part in urlsplit(url).path.split("/") if part]
        if "company" in parts:
            index = parts.index("company")
            return parts[index + 1] if len(parts) > index + 1 else ""
        return parts[0] if parts else ""
