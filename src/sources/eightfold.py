from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urlsplit

from src.sources.base import JobSource, SourceError
from src.sources.normalization import infer_location_parts, infer_remote_type
from src.utils.text import html_to_text, normalize_text


class EightfoldSource(JobSource):
    """Adapter for the public Eightfold career search used by several employers."""

    source_name = "eightfold"

    def fetch_jobs(self) -> list:
        host, domain = self._settings()
        terms = self._option_values("search_terms") or [""]
        location_term = str(self.company.options.get("location", "Canada"))
        max_pages = max(1, int(self.company.options.get("max_pages", 10)))
        summaries: dict[str, dict[str, Any]] = {}
        search_url = f"{host}/api/pcsx/search"

        for term in terms:
            start = 0
            for _ in range(max_pages):
                params = {
                    "domain": domain,
                    "query": term,
                    "location": location_term,
                    "start": start,
                }
                payload = self.client.get_json(f"{search_url}?{urlencode(params)}")
                data = payload.get("data", payload) if isinstance(payload, dict) else {}
                batch = data.get("positions", []) if isinstance(data, dict) else []
                for raw in batch:
                    identity = str(raw.get("id") or raw.get("atsJobId") or "")
                    if identity:
                        summaries.setdefault(identity, raw)
                total = int(data.get("count") or len(batch)) if isinstance(data, dict) else 0
                if not batch or start + len(batch) >= total:
                    break
                start += len(batch)

        title_keywords = [normalize_text(item) for item in self._option_values("include_title_keywords")]
        if title_keywords:
            summaries = {
                identity: raw
                for identity, raw in summaries.items()
                if any(
                    keyword in normalize_text(str(raw.get("name") or ""))
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
                    f"{host}/api/pcsx/position_details?"
                    f"{urlencode({'position_id': identity, 'domain': domain})}"
                )
                payload = self.client.get_json(detail_url)
                detail = payload.get("data", payload) if isinstance(payload, dict) else {}
                if isinstance(detail, dict):
                    raw = {**summary, **detail}
            locations = raw.get("standardizedLocations") or raw.get("locations") or []
            if isinstance(locations, str):
                locations = [locations]
            location = "; ".join(str(item) for item in locations if item)
            city, province, country = infer_location_parts(location)
            description = html_to_text(raw.get("jobDescription") or raw.get("description"))
            position_url = str(raw.get("publicUrl") or raw.get("positionUrl") or "")
            job_url = position_url if position_url.startswith("http") else f"{host}{position_url}"
            result.append(
                self.candidate(
                    external_job_id=raw.get("displayJobId") or raw.get("atsJobId") or identity,
                    title=str(raw.get("name") or "Untitled job"),
                    location=location,
                    city=city,
                    province=province,
                    country=country,
                    remote_type=str(raw.get("workLocationOption") or "")
                    or infer_remote_type(location, description),
                    description=description,
                    job_url=job_url,
                    source_url=search_url,
                    date_posted=self._timestamp(raw.get("postedTs") or raw.get("creationTs")),
                    employment_type=str(raw.get("employmentType") or ""),
                    raw_data=raw,
                )
            )
        return result

    def _settings(self) -> tuple[str, str]:
        parsed = urlsplit(self.company.search_url or self.company.careers_url)
        host = str(self.company.options.get("host") or f"{parsed.scheme}://{parsed.netloc}").rstrip("/")
        domain = str(self.company.options.get("domain") or "").strip()
        if not parsed.netloc or not host or not domain:
            raise SourceError("Eightfold requires an Eightfold URL and options.domain")
        return host, domain

    def _option_values(self, key: str) -> list[str]:
        value = self.company.options.get(key, [])
        values = [value] if isinstance(value, str) else value
        return [str(item).strip() for item in values if str(item).strip()]

    @staticmethod
    def _timestamp(value: Any) -> datetime | None:
        try:
            return (
                datetime.fromtimestamp(float(value), tz=UTC).replace(tzinfo=None)
                if value
                else None
            )
        except (TypeError, ValueError, OSError):
            return None
