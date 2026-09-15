from __future__ import annotations

from urllib.parse import urljoin, urlsplit

from src.sources.base import JobSource, SourceError
from src.sources.normalization import infer_location_parts, infer_remote_type
from src.utils.dates import parse_datetime
from src.utils.text import html_to_text, normalize_text


class WorkdaySource(JobSource):
    source_name = "workday"

    def fetch_jobs(self) -> list:
        host, tenant, site, locale = self._settings()
        api_url = f"{host}/wday/cxs/{tenant}/{site}/jobs"
        # Workday CXS rejects page sizes above 20 for many tenants.
        limit = max(1, min(20, int(self.company.options.get("page_size", 20))))
        terms = self.company.options.get("search_terms", [""])
        if isinstance(terms, str):
            terms = [terms]
        summaries_by_id = {}
        for term in terms or [""]:
            offset = 0
            while True:
                body = {
                    "appliedFacets": {},
                    "limit": limit,
                    "offset": offset,
                    "searchText": str(term),
                }
                payload = self.client.post_json(api_url, json=body)
                batch = payload.get("jobPostings", []) if isinstance(payload, dict) else []
                for item in batch:
                    identity = str(
                        item.get("externalPath")
                        or item.get("jobReqId")
                        or (item.get("bulletFields") or [""])[0]
                    )
                    summaries_by_id.setdefault(identity, item)
                total = int(payload.get("total", offset + len(batch))) if isinstance(payload, dict) else 0
                if not batch or offset + len(batch) >= total:
                    break
                offset += len(batch)
        summaries = list(summaries_by_id.values())

        title_keywords = self._option_strings("include_title_keywords")
        if title_keywords:
            summaries = [
                item
                for item in summaries
                if any(
                    keyword in normalize_text(str(item.get("title") or ""))
                    for keyword in title_keywords
                )
            ]
        location_keywords = self._option_strings("location_keywords")
        if location_keywords:
            summaries = [
                item
                for item in summaries
                if not str(item.get("locationsText") or "").strip()
                or "location" in normalize_text(str(item.get("locationsText") or ""))
                or self._matches_location(
                    str(item.get("locationsText") or ""), location_keywords
                )
            ]
        summaries = summaries[: max(1, int(self.company.options.get("max_jobs", 500)))]

        result = []
        fetch_details = bool(self.company.options.get("fetch_details", True))
        for summary in summaries:
            external_path = str(summary.get("externalPath", ""))
            raw = summary
            if fetch_details and external_path:
                job_path = self._job_path(external_path)
                detail_url = f"{host}/wday/cxs/{tenant}/{site}/job/{job_path}"
                detail_payload = self.client.get_json(detail_url)
                raw = detail_payload.get("jobPostingInfo", detail_payload)
            location = str(raw.get("location") or summary.get("locationsText") or "")
            additional = raw.get("additionalLocations") or []
            if additional:
                location = "; ".join([location, *(str(item) for item in additional if item)]).strip("; ")
            if location_keywords and not self._matches_location(location, location_keywords):
                continue
            city, province, country = infer_location_parts(location)
            description = html_to_text(raw.get("jobDescription") or raw.get("description"))
            job_url = str(raw.get("externalUrl") or "")
            if not job_url and external_path:
                public_base = f"{host}/{locale}/{site}/job/"
                job_url = urljoin(public_base, self._job_path(external_path))
            result.append(
                self.candidate(
                    external_job_id=raw.get("jobReqId") or summary.get("bulletFields", [None])[0],
                    title=str(raw.get("title") or summary.get("title") or "Untitled job"),
                    location=location,
                    city=city,
                    province=province,
                    country=country,
                    remote_type=infer_remote_type(location, description),
                    description=description,
                    job_url=job_url,
                    source_url=api_url,
                    date_posted=parse_datetime(raw.get("startDate") or summary.get("postedOn")),
                    employment_type=str(raw.get("timeType", "")),
                    raw_data=raw,
                )
            )
        return result

    def _option_strings(self, key: str) -> list[str]:
        value = self.company.options.get(key, [])
        values = [value] if isinstance(value, str) else value
        return [normalize_text(str(item)) for item in values if str(item).strip()]

    @staticmethod
    def _matches_location(location: str, keywords: list[str]) -> bool:
        city, province, country = infer_location_parts(location)
        haystack = normalize_text(" ".join((location, city, province, country)))
        return any(keyword in haystack for keyword in keywords)

    def _settings(self) -> tuple[str, str, str, str]:
        options = self.company.options
        host = str(options.get("host", "")).rstrip("/")
        tenant = str(options.get("tenant", "")).strip()
        site = str(options.get("site", "")).strip()
        locale = str(options.get("locale", "en-US")).strip()
        url = self.company.search_url or self.company.careers_url
        parsed = urlsplit(url)
        if not host and parsed.netloc:
            host = f"{parsed.scheme or 'https'}://{parsed.netloc}"
        parts = [part for part in parsed.path.split("/") if part]
        if parts and parts[0].casefold() in {"en-us", "en-ca", "fr-ca"}:
            locale = parts.pop(0)
        if not site and parts:
            site = parts[0]
        if not host or not tenant or not site:
            raise SourceError("Workday requires options.host, options.tenant, and options.site")
        return host, tenant, site, locale

    @staticmethod
    def _job_path(external_path: str) -> str:
        path = external_path.lstrip("/")
        return path[4:] if path.casefold().startswith("job/") else path
