from __future__ import annotations

import json
import logging
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy.orm import Session

from src.config.loader import AppConfig, CompanyConfig
from src.database.database import Database
from src.matching.scorer import JobScorer
from src.models.job import Job, JobCandidate, ScanRun, SourceRun
from src.services.deduplicator import Deduplicator
from src.services.notifier import Notifier, build_notifier
from src.services.repository import JobRepository
from src.sources import create_source
from src.utils.dates import utcnow
from src.utils.text import normalize_text


@dataclass(slots=True)
class CompanyScanResult:
    company: str
    source: str
    fetched: int = 0
    new_jobs: int = 0
    updated_jobs: int = 0
    duplicates: int = 0
    error: str = ""


@dataclass(frozen=True, slots=True)
class ScanProgress:
    phase: str
    message: str
    current: int
    total: int
    company: str = ""
    workers: int = 1


ScanProgressCallback = Callable[[ScanProgress], None]


@dataclass(slots=True)
class _FetchedCompany:
    company: CompanyConfig
    source: str
    candidates: list[JobCandidate]
    started_at: datetime
    error: str = ""


@dataclass(slots=True)
class ScanResult:
    run_id: str
    fetched: int = 0
    new_jobs: list[Job] = field(default_factory=list)
    updated_jobs: list[Job] = field(default_factory=list)
    duplicates: int = 0
    closed_jobs: list[Job] = field(default_factory=list)
    companies: list[CompanyScanResult] = field(default_factory=list)

    @property
    def failures(self) -> list[CompanyScanResult]:
        return [item for item in self.companies if item.error]


class Scanner:
    def __init__(
        self,
        config: AppConfig,
        database: Database,
        notifier: Notifier | None = None,
    ):
        self.config = config
        self.database = database
        self.logger = logging.getLogger("services.scanner")
        scorer = JobScorer(config)
        threshold = float(config.settings.get("dedup_similarity_threshold", 93))
        self.repository = JobRepository(Deduplicator(threshold), scorer)
        self.notifier = notifier or build_notifier(config)

    def scan(
        self,
        company_name: str | None = None,
        progress: ScanProgressCallback | None = None,
    ) -> ScanResult:
        companies = self.config.enabled_companies(company_name)
        with self.database.session() as session:
            run = ScanRun(companies_attempted=len(companies))
            session.add(run)
            session.flush()
            run_id = run.id
        result = ScanResult(run_id=run_id)
        successful_companies: list[str] = []

        self.logger.info("scan_started", extra={"run_id": run_id, "companies": len(companies)})
        total_companies = len(companies)
        workers = self._worker_count(total_companies)
        self._emit_progress(
            progress,
            ScanProgress(
                phase="fetching",
                message=(
                    f"Scanning {total_companies} sources with up to "
                    f"{workers} concurrent workers"
                ),
                current=0,
                total=total_companies,
                workers=workers,
            ),
        )

        completed = 0
        if workers == 1:
            fetched_results = (
                self._fetch_company(run_id, company) for company in companies
            )
            for fetched in fetched_results:
                completed += 1
                company_result = self._process_fetched_company(
                    run_id, fetched, result, successful_companies
                )
                self._emit_company_complete(
                    progress, company_result, completed, total_companies, workers
                )
        elif companies:
            deferred_aggregators: list[_FetchedCompany] = []
            with ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix="job-source",
            ) as executor:
                futures: dict[Future[_FetchedCompany], CompanyConfig] = {
                    executor.submit(self._fetch_company, run_id, company): company
                    for company in companies
                }
                for future in as_completed(futures):
                    fetched = future.result()
                    if fetched.source in {"linkedin_email", "job_alert_email"}:
                        deferred_aggregators.append(fetched)
                        continue
                    completed += 1
                    company_result = self._process_fetched_company(
                        run_id, fetched, result, successful_companies
                    )
                    self._emit_company_complete(
                        progress, company_result, completed, total_companies, workers
                    )
            # Process alert aggregators last so an official company posting remains
            # the canonical job when both sources discover the same vacancy.
            for fetched in deferred_aggregators:
                completed += 1
                company_result = self._process_fetched_company(
                    run_id, fetched, result, successful_companies
                )
                self._emit_company_complete(
                    progress, company_result, completed, total_companies, workers
                )

        company_order = {
            company.name.casefold(): index for index, company in enumerate(companies)
        }
        result.companies.sort(
            key=lambda item: company_order.get(item.company.casefold(), len(company_order))
        )

        self._emit_progress(
            progress,
            ScanProgress(
                phase="finalizing",
                message="Finalizing job updates, closures, and notifications",
                current=total_companies,
                total=total_companies,
            ),
        )
        close_days = int(self.config.settings.get("close_missing_after_days", 14))
        cutoff = utcnow() - timedelta(days=close_days)
        with self.database.session() as session:
            result.closed_jobs = self.repository.close_stale_jobs(
                session, successful_companies, cutoff
            )

        self._finish_run(result)
        self._notify_new_jobs(result.new_jobs)
        self.logger.info(
            "scan_finished",
            extra={
                "run_id": run_id,
                "new_jobs": len(result.new_jobs),
                "updated_jobs": len(result.updated_jobs),
                "duplicates": result.duplicates,
                "closed_jobs": len(result.closed_jobs),
                "failures": len(result.failures),
            },
        )
        return result

    def _worker_count(self, company_count: int) -> int:
        scanner_settings = self.config.settings.get("scanner", {})
        configured = int(scanner_settings.get("max_concurrent_sources", 4))
        return max(1, min(configured, company_count or 1))

    def _fetch_company(self, run_id: str, company: CompanyConfig) -> _FetchedCompany:
        started = utcnow()
        source_name = company.source_type
        try:
            source = create_source(company, self.config)
            source_name = source.source_name
            candidates = source.fetch_jobs()
            self.logger.info(
                "source_fetched",
                extra={
                    "run_id": run_id,
                    "company": company.name,
                    "source": source_name,
                    "fetched": len(candidates),
                },
            )
            return _FetchedCompany(
                company=company,
                source=source_name,
                candidates=candidates,
                started_at=started,
            )
        except Exception as exc:
            self.logger.error(
                "source_failed",
                extra={
                    "run_id": run_id,
                    "company": company.name,
                    "source": source_name,
                },
                exc_info=True,
            )
            return _FetchedCompany(
                company=company,
                source=source_name,
                candidates=[],
                started_at=started,
                error=str(exc),
            )

    def _process_fetched_company(
        self,
        run_id: str,
        fetched: _FetchedCompany,
        result: ScanResult,
        successful_companies: list[str],
    ) -> CompanyScanResult:
        company = fetched.company
        company_result = CompanyScanResult(
            company=company.name,
            source=fetched.source,
            fetched=len(fetched.candidates),
            error=fetched.error,
        )
        result.fetched += len(fetched.candidates)

        if not fetched.error:
            for candidate in fetched.candidates:
                try:
                    self._validate_candidate(candidate)
                    with self.database.session() as session:
                        upsert = self.repository.upsert(session, candidate)
                    if upsert.action == "NEW":
                        result.new_jobs.append(upsert.job)
                        company_result.new_jobs += 1
                    elif upsert.action == "UPDATED":
                        result.updated_jobs.append(upsert.job)
                        company_result.updated_jobs += 1
                    else:
                        result.duplicates += 1
                        company_result.duplicates += 1
                except Exception as exc:
                    self.logger.error(
                        "job_processing_failed",
                        extra={"company": company.name, "title": candidate.title},
                        exc_info=True,
                    )
                    company_result.error += (
                        f"Job processing error for {candidate.title}: {exc}; "
                    )
            if not company_result.error:
                successful_companies.append(normalize_text(company.name))

        result.companies.append(company_result)
        self._record_source_run(run_id, company_result, fetched.started_at)
        return company_result

    def _emit_company_complete(
        self,
        progress: ScanProgressCallback | None,
        company_result: CompanyScanResult,
        completed: int,
        total: int,
        workers: int,
    ) -> None:
        outcome = (
            "failed" if company_result.error else f"{company_result.fetched} jobs"
        )
        self._emit_progress(
            progress,
            ScanProgress(
                phase="company_complete",
                message=(
                    f"Completed {completed}/{total}: "
                    f"{company_result.company} ({outcome})"
                ),
                current=completed,
                total=total,
                company=company_result.company,
                workers=workers,
            ),
        )

    def _emit_progress(
        self,
        callback: ScanProgressCallback | None,
        update: ScanProgress,
    ) -> None:
        if callback is None:
            return
        try:
            callback(update)
        except Exception:
            self.logger.warning("scan_progress_callback_failed", exc_info=True)

    def _record_source_run(
        self, run_id: str, result: CompanyScanResult, started: object
    ) -> None:
        with self.database.session() as session:
            session.add(
                SourceRun(
                    scan_run_id=run_id,
                    company=result.company,
                    source=result.source,
                    status="FAILED" if result.error else "SUCCESS",
                    fetched=result.fetched,
                    new_jobs=result.new_jobs,
                    updated_jobs=result.updated_jobs,
                    duplicates=result.duplicates,
                    error=result.error,
                    started_at=started,
                    finished_at=utcnow(),
                )
            )

    def _finish_run(self, result: ScanResult) -> None:
        with self.database.session() as session:
            run = session.get(ScanRun, result.run_id)
            if not run:
                return
            run.finished_at = utcnow()
            run.status = "PARTIAL" if result.failures else "SUCCESS"
            run.companies_succeeded = len(result.companies) - len(result.failures)
            run.fetched = result.fetched
            run.new_jobs = len(result.new_jobs)
            run.updated_jobs = len(result.updated_jobs)
            run.duplicates = result.duplicates
            run.closed_jobs = len(result.closed_jobs)
            run.failures = json.dumps(
                [{"company": item.company, "error": item.error} for item in result.failures]
            )

    def _notify_new_jobs(self, jobs: list[Job]) -> None:
        threshold = int(self.config.settings.get("urgent_notification_score", 85))
        limit = int(
            self.config.settings.get("notifications", {}).get(
                "max_immediate_notifications_per_scan", 5
            )
        )
        qualifying = sorted(
            (job for job in jobs if job.match_score >= threshold),
            key=lambda item: item.match_score,
            reverse=True,
        )[:limit]
        for job in qualifying:
            self.notifier.notify_job(job)

    @staticmethod
    def _validate_candidate(candidate: JobCandidate) -> None:
        if not candidate.title.strip() or not candidate.company.strip():
            raise ValueError("Normalized jobs require a title and company")
        if not candidate.external_job_id and not candidate.job_url.strip():
            raise ValueError("Normalized jobs require an external ID or job URL")
