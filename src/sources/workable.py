from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from src.models.job import JobCandidate
from src.sources.base import JobSource, SourceError
from src.sources.normalization import infer_location_parts, infer_remote_type, location_string
from src.utils.dates import parse_datetime
from src.utils.text import html_to_text, normalize_text


_POSTED_DATE = re.compile(r"Date Posted:\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})", re.IGNORECASE)


class WorkableSource(JobSource):
    """Read Workable's public account job endpoints."""

    source_name = "workable"

    def fetch_jobs(self) -> list[JobCandidate]:
        account = str(self.company.options.get("account", "")).strip()
        if not account:
            raise SourceError("Workable requires options.account")
        api_base = str(
            self.company.options.get("api_base", "https://apply.workable.com/api")
        ).rstrip("/")
        payload = self.client.post_json(f"{api_base}/v3/accounts/{account}/jobs", json={})
        summaries = payload.get("results", []) if isinstance(payload, dict) else []
        title_keywords = self._option_strings("include_title_keywords")
        result: list[JobCandidate] = []
        for summary in summaries:
            title = str(summary.get("title") or "Untitled job")
            if title_keywords and not any(keyword in normalize_text(title) for keyword in title_keywords):
                continue
            shortcode = str(summary.get("shortcode") or summary.get("id") or "")
            raw: dict[str, Any] = summary
            if bool(self.company.options.get("fetch_details", True)) and shortcode:
                raw = self.client.get_json(f"{api_base}/v2/accounts/{account}/jobs/{shortcode}")
            description_html = " ".join(
                str(raw.get(key) or "") for key in ("description", "requirements", "benefits")
            )
            description = html_to_text(description_html)
            location_data = raw.get("location") or summary.get("location") or {}
            location = location_string(location_data)
            if not location and isinstance(location_data, dict):
                location = ", ".join(
                    str(location_data.get(key) or "")
                    for key in ("city", "region", "country")
                    if location_data.get(key)
                )
            city, province, country = infer_location_parts(location, location_data)
            posted = raw.get("published") or summary.get("published")
            if not posted:
                match = _POSTED_DATE.search(description)
                posted = match.group(1) if match else None
            workplace = str(raw.get("workplace") or "")
            public_url = f"https://apply.workable.com/{account}/j/{shortcode}/"
            result.append(
                self.candidate(
                    external_job_id=raw.get("id") or shortcode,
                    title=title,
                    location=location,
                    city=city,
                    province=province,
                    country=country,
                    remote_type=infer_remote_type(workplace, location, description),
                    description=description,
                    job_url=public_url,
                    source_url=f"{api_base}/v3/accounts/{account}/jobs",
                    date_posted=self._parse_posted(posted),
                    employment_type=str(raw.get("type") or ""),
                    raw_data=raw,
                )
            )
        return result

    def _option_strings(self, key: str) -> list[str]:
        value = self.company.options.get(key, [])
        values = [value] if isinstance(value, str) else value
        return [normalize_text(str(item)) for item in values if str(item).strip()]

    @staticmethod
    def _parse_posted(value: object) -> datetime | None:
        parsed = parse_datetime(value)
        if parsed or not value:
            return parsed
        for format_string in ("%B %d, %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(str(value).strip(), format_string)
            except ValueError:
                continue
        return None
