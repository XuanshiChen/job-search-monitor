from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.exceptions import MaxRetryError
from urllib3.util.retry import Retry

from src.config.loader import AppConfig, CompanyConfig
from src.models.job import JobCandidate


class SourceError(RuntimeError):
    """A recoverable failure affecting one configured job source."""


class LoggingRetry(Retry):
    def increment(
        self,
        method: str | None = None,
        url: str | None = None,
        response: Any = None,
        error: Exception | None = None,
        _pool: Any = None,
        _stacktrace: Any = None,
    ) -> Retry:
        logger = logging.getLogger("sources.http")
        try:
            updated = super().increment(
                method=method,
                url=url,
                response=response,
                error=error,
                _pool=_pool,
                _stacktrace=_stacktrace,
            )
        except MaxRetryError:
            logger.error(
                "http_retries_exhausted",
                extra={
                    "retry_method": method,
                    "retry_url": url,
                    "retry_status": getattr(response, "status", None),
                    "retry_error": str(error or ""),
                },
            )
            raise
        logger.warning(
            "http_retry_scheduled",
            extra={
                "retry_method": method,
                "retry_url": url,
                "retry_status": getattr(response, "status", None),
                "retry_error": str(error or ""),
                "retries_remaining": updated.total,
            },
        )
        return updated


class RespectfulHttpClient:
    def __init__(self, settings: dict[str, Any]):
        network = settings.get("network", {})
        self.timeout = float(network.get("timeout_seconds", 25))
        self.interval = max(0.0, float(network.get("request_interval_seconds", 0.5)))
        self._last_request = 0.0
        self.session = requests.Session()
        retry = LoggingRetry(
            total=int(network.get("retries", 3)),
            connect=int(network.get("retries", 3)),
            read=int(network.get("retries", 3)),
            backoff_factor=float(network.get("backoff_factor", 1.0)),
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(("GET", "POST")),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update(
            {
                "User-Agent": str(
                    network.get(
                        "user_agent",
                        "JobSearchMonitor/1.0 (+personal, respectful job monitoring)",
                    )
                ),
                "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
            }
        )

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        try:
            response = self.session.request(method, url, timeout=self.timeout, **kwargs)
            self._last_request = time.monotonic()
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            raise SourceError(f"{method.upper()} {url} failed: {exc}") from exc

    def get_json(self, url: str, **kwargs: Any) -> Any:
        response = self.request("GET", url, **kwargs)
        try:
            return response.json()
        except requests.JSONDecodeError as exc:
            raise SourceError(f"Expected JSON from {url}") from exc

    def post_json(self, url: str, **kwargs: Any) -> Any:
        response = self.request("POST", url, **kwargs)
        try:
            return response.json()
        except requests.JSONDecodeError as exc:
            raise SourceError(f"Expected JSON from {url}") from exc


class JobSource(ABC):
    source_name = "base"

    def __init__(self, company: CompanyConfig, config: AppConfig):
        self.company = company
        self.config = config
        self.client = RespectfulHttpClient(config.settings)
        self.logger = logging.getLogger(f"sources.{self.source_name}")

    @abstractmethod
    def fetch_jobs(self) -> list[JobCandidate]:
        """Fetch and normalize all current jobs exposed by this source."""

    def candidate(self, **kwargs: Any) -> JobCandidate:
        kwargs.setdefault("company", self.company.name)
        kwargs.setdefault("company_priority", self.company.priority)
        kwargs.setdefault("source", self.source_name)
        kwargs.setdefault("source_url", self.company.search_url or self.company.careers_url)
        return JobCandidate(**kwargs)
