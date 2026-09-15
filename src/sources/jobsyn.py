from __future__ import annotations

from typing import Any
from urllib.parse import quote

from src.models.job import JobCandidate
from src.sources.base import JobSource, SourceError
from src.sources.normalization import infer_location_parts, infer_remote_type
from src.utils.dates import parse_datetime
from src.utils.text import html_to_text, normalize_text


class JobsynSource(JobSource):
    """Read public Jobsyn/NLX job search results used by DEJobs microsites."""

    source_name = "jobsyn"

    def fetch_jobs(self) -> list[JobCandidate]:
        origin = str(self.company.options.get("origin", "")).strip()
        business_units = self.company.options.get("business_unit_ids", [])
        if isinstance(business_units, str):
            business_units = [business_units]
        if not origin or not business_units:
            raise SourceError("Jobsyn requires options.origin and options.business_unit_ids")
        api_url = str(
            self.company.options.get(
                "api_url", "https://prod-search-api.jobsyn.org/api/v1/solr/search"
            )
        )
        terms = self.company.options.get("search_terms", [""])
        if isinstance(terms, str):
            terms = [terms]
        location = str(self.company.options.get("location", "Canada"))
        page_size = max(1, min(50, int(self.company.options.get("page_size", 25))))
        max_pages = max(1, int(self.company.options.get("max_pages", 10)))
        jobs: dict[str, dict[str, Any]] = {}
        headers = {"X-Origin": origin}
        successful_requests = 0
        last_error: SourceError | None = None

        for term in terms or [""]:
            for page in range(1, max_pages + 1):
                try:
                    payload = self.client.get_json(
                        api_url,
                        headers=headers,
                        params={
                            "q": str(term),
                            "location": location,
                            "page": page,
                            "num_items": page_size,
                            "buids": ",".join(str(item) for item in business_units),
                        },
                    )
                    successful_requests += 1
                except SourceError as exc:
                    last_error = exc
                    self.logger.warning(
                        "jobsyn_page_failed",
                        extra={"company": self.company.name, "term": str(term), "page": page},
                    )
                    break
                batch = payload.get("jobs", []) if isinstance(payload, dict) else []
                for raw in batch:
                    identity = str(raw.get("guid") or raw.get("reqid") or raw.get("id"))
                    jobs.setdefault(identity, raw)
                pagination = payload.get("pagination", {}) if isinstance(payload, dict) else {}
                if not batch or not bool(pagination.get("has_more_pages", False)):
                    break

        if not successful_requests and last_error is not None:
            raise last_error

        result: list[JobCandidate] = []
        title_keywords = self._option_strings("include_title_keywords")
        for raw in jobs.values():
            title = self._first(raw.get("title_exact")) or "Untitled job"
            if title_keywords and not any(
                keyword in normalize_text(title) for keyword in title_keywords
            ):
                continue
            location_text = self._first(raw.get("location_exact"))
            if not location_text:
                location_text = ", ".join(
                    filter(None, (self._first(raw.get("city_exact")), self._first(raw.get("state_short"))))
                )
            country_value = self._first(raw.get("country_exact"))
            full_location = ", ".join(filter(None, (location_text, country_value)))
            city, province, country = infer_location_parts(
                full_location,
                {
                    "city": self._first(raw.get("city_exact")),
                    "region": self._first(raw.get("state_short")),
                    "country": country_value,
                },
            )
            guid = self._first(raw.get("guid"))
            title_slug = self._first(raw.get("title_slug")) or "job"
            location_slug = "-".join(part.casefold() for part in location_text.replace(",", "").split()) or "canada"
            job_url = f"https://{origin}/{quote(location_slug)}/{quote(title_slug)}/{guid}/job/"
            description = html_to_text(self._first(raw.get("description")))
            result.append(
                self.candidate(
                    external_job_id=self._first(raw.get("reqid")) or guid,
                    title=title,
                    location=full_location,
                    city=city,
                    province=province,
                    country=country,
                    remote_type=infer_remote_type(full_location, description),
                    description=description,
                    job_url=job_url,
                    source_url=api_url,
                    date_posted=parse_datetime(raw.get("date_new") or raw.get("date_added")),
                    employment_type=self._first(raw.get("job_type")),
                    experience_level=self._first(raw.get("job_type")),
                    raw_data=raw,
                )
            )
        return result

    def _option_strings(self, key: str) -> list[str]:
        value = self.company.options.get(key, [])
        values = [value] if isinstance(value, str) else value
        return [normalize_text(str(item)) for item in values if str(item).strip()]

    @staticmethod
    def _first(value: Any) -> str:
        if isinstance(value, list):
            return str(value[0]) if value else ""
        return str(value or "")
