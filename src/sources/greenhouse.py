from __future__ import annotations

from urllib.parse import urlsplit

from src.sources.base import JobSource, SourceError
from src.sources.normalization import infer_location_parts, infer_remote_type
from src.utils.dates import parse_datetime
from src.utils.text import html_to_text


class GreenhouseSource(JobSource):
    source_name = "greenhouse"

    def fetch_jobs(self) -> list:
        token = str(self.company.options.get("board_token", "")).strip() or self._infer_token()
        if not token:
            raise SourceError("Greenhouse requires options.board_token or a recognizable careers URL")
        api_url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
        payload = self.client.get_json(api_url, params={"content": "true"})
        jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
        result = []
        for raw in jobs:
            if bool(self.company.options.get("fetch_details", False)) and raw.get("id"):
                raw = self.client.get_json(f"{api_url}/{raw['id']}")
            location = str((raw.get("location") or {}).get("name", ""))
            city, province, country = infer_location_parts(location)
            description = html_to_text(raw.get("content"))
            result.append(
                self.candidate(
                    external_job_id=raw.get("id"),
                    title=str(raw.get("title", "Untitled job")),
                    location=location,
                    city=city,
                    province=province,
                    country=country,
                    remote_type=infer_remote_type(location, description),
                    description=description,
                    job_url=str(raw.get("absolute_url", "")),
                    source_url=api_url,
                    date_posted=parse_datetime(raw.get("first_published")),
                    raw_data=raw,
                )
            )
        return result

    def _infer_token(self) -> str:
        url = self.company.search_url or self.company.careers_url
        path = [part for part in urlsplit(url).path.split("/") if part]
        return path[0] if path else ""
