from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from src.sources.base import JobSource, SourceError
from src.sources.normalization import infer_location_parts, infer_remote_type
from src.utils.dates import parse_datetime


class SampleSource(JobSource):
    source_name = "sample"

    def fetch_jobs(self) -> list:
        configured = self.company.search_url or str(self.company.options.get("path", ""))
        path = Path(configured)
        if not path.is_absolute():
            path = self.config.root / path
        if not path.exists():
            raise SourceError(f"Sample source file not found: {path}")
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = yaml.safe_load(handle) or {}
        except yaml.YAMLError as exc:
            raise SourceError(f"Invalid sample source YAML: {exc}") from exc

        jobs = payload.get("jobs", [])
        if not isinstance(jobs, list):
            raise SourceError("Sample source requires a top-level jobs list")
        result = []
        for raw in jobs:
            if not isinstance(raw, dict):
                continue
            location = str(raw.get("location", ""))
            city, province, country = infer_location_parts(location, raw)
            result.append(
                self.candidate(
                    external_job_id=raw.get("id"),
                    title=str(raw.get("title", "Untitled job")),
                    location=location,
                    city=city,
                    province=province,
                    country=country,
                    remote_type=str(raw.get("remote_type") or infer_remote_type(location)),
                    description=str(raw.get("description", "")),
                    job_url=str(raw.get("job_url", "")),
                    date_posted=parse_datetime(raw.get("date_posted")),
                    employment_type=str(raw.get("employment_type", "")),
                    experience_level=str(raw.get("experience_level", "")),
                    salary_min=_float_or_none(raw.get("salary_min")),
                    salary_max=_float_or_none(raw.get("salary_max")),
                    salary_currency=str(raw.get("salary_currency", "")),
                    raw_data=raw,
                )
            )
        return result


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
