from __future__ import annotations

from urllib.parse import urlsplit

from src.sources.base import JobSource, SourceError
from src.sources.normalization import infer_location_parts, infer_remote_type
from src.utils.dates import parse_datetime
from src.utils.text import html_to_text


class LeverSource(JobSource):
    source_name = "lever"

    def fetch_jobs(self) -> list:
        site = str(self.company.options.get("site", "")).strip() or self._infer_site()
        if not site:
            raise SourceError("Lever requires options.site or a recognizable careers URL")
        host = "api.eu.lever.co" if self._is_eu() else "api.lever.co"
        api_url = f"https://{host}/v0/postings/{site}"
        page_size = min(100, int(self.company.options.get("page_size", 100)))
        skip = 0
        payload = []
        while True:
            batch = self.client.get_json(
                api_url, params={"mode": "json", "skip": skip, "limit": page_size}
            )
            if not isinstance(batch, list):
                raise SourceError("Unexpected Lever response shape")
            payload.extend(batch)
            if len(batch) < page_size:
                break
            skip += len(batch)
        result = []
        for raw in payload:
            categories = raw.get("categories") or {}
            location = str(categories.get("location", ""))
            city, province, country = infer_location_parts(
                location, {"country": raw.get("country", "")}
            )
            description = " ".join(
                filter(
                    None,
                    (
                        html_to_text(raw.get("descriptionPlain") or raw.get("description")),
                        html_to_text(raw.get("additionalPlain") or raw.get("additional")),
                    ),
                )
            )
            salary = raw.get("salaryRange") or {}
            result.append(
                self.candidate(
                    external_job_id=raw.get("id"),
                    title=str(raw.get("text", "Untitled job")),
                    location=location,
                    city=city,
                    province=province,
                    country=country,
                    remote_type=str(raw.get("workplaceType") or infer_remote_type(location, description)),
                    description=description,
                    job_url=str(raw.get("hostedUrl") or raw.get("applyUrl") or ""),
                    source_url=api_url,
                    date_posted=parse_datetime(raw.get("createdAt")),
                    employment_type=str(categories.get("commitment", "")),
                    salary_min=_number(salary.get("min")),
                    salary_max=_number(salary.get("max")),
                    salary_currency=str(salary.get("currency", "")),
                    raw_data=raw,
                )
            )
        return result

    def _infer_site(self) -> str:
        url = self.company.search_url or self.company.careers_url
        path = [part for part in urlsplit(url).path.split("/") if part]
        return path[0] if path else ""

    def _is_eu(self) -> bool:
        region = str(self.company.options.get("region", "")).casefold()
        url = self.company.search_url or self.company.careers_url
        return region == "eu" or "jobs.eu.lever.co" in url.casefold()


def _number(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
