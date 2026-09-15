from __future__ import annotations

from typing import Any
from urllib.parse import urlencode, urlsplit

from src.models.job import JobCandidate
from src.sources.base import JobSource, SourceError
from src.sources.normalization import infer_location_parts, infer_remote_type
from src.utils.dates import parse_datetime
from src.utils.text import html_to_text, normalize_text


class JibeSource(JobSource):
    """Read public Jibe career-site `/api/jobs` endpoints."""

    source_name = "jibe"

    def fetch_jobs(self) -> list[JobCandidate]:
        public_url = self.company.search_url or self.company.careers_url
        parsed = urlsplit(public_url)
        if not parsed.netloc:
            raise SourceError("Jibe requires a public career-site URL")
        host = str(self.company.options.get("host") or f"{parsed.scheme}://{parsed.netloc}").rstrip("/")
        api_url = str(self.company.options.get("api_url") or f"{host}/api/jobs")
        query = dict(self.company.options.get("query", {}))
        query.setdefault("lang", str(self.company.options.get("locale", "en-US")))
        page_size = max(1, min(100, int(self.company.options.get("page_size", 20))))
        query["limit"] = page_size
        max_pages = max(1, int(self.company.options.get("max_pages", 20)))
        title_keywords = self._option_strings("include_title_keywords")
        required_country = normalize_text(str(self.company.options.get("country", "")))

        jobs: dict[str, dict[str, Any]] = {}
        page = 1
        while page <= max_pages:
            payload = self.client.get_json(
                f"{api_url}?{urlencode({**query, 'page': page})}"
            )
            batch = payload.get("jobs", []) if isinstance(payload, dict) else []
            total = int(payload.get("totalCount") or payload.get("count") or len(batch))
            for wrapper in batch:
                raw = wrapper.get("data", wrapper) if isinstance(wrapper, dict) else {}
                identity = str(raw.get("req_id") or raw.get("slug") or "")
                if identity:
                    jobs.setdefault(identity, raw)
            if not batch or page * page_size >= total:
                break
            page += 1

        result: list[JobCandidate] = []
        for identity, raw in jobs.items():
            title = str(raw.get("title") or "Untitled job")
            if title_keywords and not any(
                keyword in normalize_text(title) for keyword in title_keywords
            ):
                continue
            city = str(raw.get("city") or "")
            province = str(raw.get("state") or "")
            country = str(raw.get("country") or "")
            if required_country and required_country != normalize_text(country):
                continue
            location = str(raw.get("full_location") or raw.get("location_name") or "")
            if not location:
                location = ", ".join(filter(None, (city, province, country)))
            city, province, country = infer_location_parts(
                location, {"city": city, "region": province, "country": country}
            )
            description = "\n\n".join(
                filter(
                    None,
                    (
                        html_to_text(raw.get("description")),
                        html_to_text(raw.get("responsibilities")),
                        html_to_text(raw.get("qualifications")),
                    ),
                )
            )
            language = str(raw.get("language") or query["lang"])
            job_url = str(raw.get("canonical_url") or f"{host}/jobs/{raw.get('slug')}?lang={language}")
            result.append(
                self.candidate(
                    external_job_id=identity,
                    title=title,
                    location=location,
                    city=city,
                    province=province,
                    country=country,
                    remote_type=infer_remote_type(
                        str(raw.get("workplace_type") or ""), location, description
                    ),
                    description=description,
                    job_url=job_url,
                    source_url=api_url,
                    date_posted=parse_datetime(raw.get("update_date") or raw.get("create_date")),
                    employment_type=str(raw.get("employment_type") or ""),
                    raw_data=raw,
                )
            )
        return result[: max(1, int(self.company.options.get("max_jobs", 200)))]

    def _option_strings(self, key: str) -> list[str]:
        value = self.company.options.get(key, [])
        values = [value] if isinstance(value, str) else value
        return [normalize_text(str(item)) for item in values if str(item).strip()]
