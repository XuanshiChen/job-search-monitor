from __future__ import annotations

from threading import Barrier, Event, Lock

from sqlalchemy import func, select

from src.config.loader import CompanyConfig
from src.models.job import Job, JobCandidate, ScanRun
from src.services.notifier import Notifier
from src.services.reporter import Reporter
from src.services.scanner import Scanner


class RecordingNotifier(Notifier):
    def __init__(self) -> None:
        self.job_ids: list[str] = []

    def notify_job(self, job: Job) -> bool:
        self.job_ids.append(job.internal_id)
        return True


def test_sample_source_runs_end_to_end_and_second_run_deduplicates(database, app_config) -> None:
    notifier = RecordingNotifier()
    scanner = Scanner(app_config, database, notifier)

    first = scanner.scan()
    second = scanner.scan()

    assert first.fetched == 3
    assert len(first.new_jobs) == 3
    assert second.fetched == 3
    assert len(second.new_jobs) == 0
    assert second.duplicates == 3
    with database.session() as session:
        assert session.scalar(select(func.count(Job.internal_id))) == 3
        assert session.scalar(select(func.count(ScanRun.id))) == 2


def test_report_contains_real_database_counts(database, app_config) -> None:
    Scanner(app_config, database, RecordingNotifier()).scan()
    reporter = Reporter(app_config, database)

    summary = reporter.summary()
    markdown = reporter.render_markdown(summary)
    path, written_markdown = reporter.write(summary.day)

    assert summary.new_jobs
    assert summary.applications_today == 0
    assert "Applied today: **0 / 3**" in markdown
    assert "Protection & Control Engineer-in-Training" in markdown
    assert path.exists()
    assert path.parent == app_config.report_directory
    assert written_markdown == markdown
    assert path.read_text(encoding="utf-8") == markdown


def test_scanner_reports_each_company_and_finalization(database, app_config) -> None:
    updates = []

    Scanner(app_config, database, RecordingNotifier()).scan(progress=updates.append)

    assert updates
    assert updates[0].phase == "fetching"
    assert any(update.phase == "company_complete" for update in updates)
    assert updates[-1].phase == "finalizing"
    assert updates[-1].current == updates[-1].total


def test_scanner_fetches_independent_sources_with_bounded_concurrency(
    database, app_config, monkeypatch
) -> None:
    app_config.companies = [
        CompanyConfig(name="Company A", priority="tier_1", source_type="sample"),
        CompanyConfig(name="Company B", priority="tier_2", source_type="sample"),
    ]
    app_config.settings.setdefault("scanner", {})["max_concurrent_sources"] = 2
    barrier = Barrier(2)
    state_lock = Lock()
    active = 0
    maximum_active = 0

    class BlockingSource:
        source_name = "blocking"

        def fetch_jobs(self):
            nonlocal active, maximum_active
            with state_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            try:
                barrier.wait(timeout=3)
            finally:
                with state_lock:
                    active -= 1
            return []

    monkeypatch.setattr(
        "src.services.scanner.create_source",
        lambda _company, _config: BlockingSource(),
    )
    updates = []

    result = Scanner(app_config, database, RecordingNotifier()).scan(
        progress=updates.append
    )

    assert maximum_active == 2
    assert result.failures == []
    assert updates[0].workers == 2
    assert updates[-1].phase == "finalizing"


def test_concurrent_scan_processes_official_source_before_email_aggregator(
    database, app_config, monkeypatch
) -> None:
    app_config.companies = [
        CompanyConfig(name="Official source", priority="tier_1", source_type="sample"),
        CompanyConfig(
            name="Email alerts", priority="tier_3", source_type="job_alert_email"
        ),
    ]
    app_config.settings.setdefault("scanner", {})["max_concurrent_sources"] = 2
    barrier = Barrier(2)
    email_finished = Event()

    class FakeSource:
        def __init__(self, company: CompanyConfig):
            self.company = company
            self.source_name = (
                "job_alert_email"
                if company.source_type == "job_alert_email"
                else "official_test"
            )

        def fetch_jobs(self):
            barrier.wait(timeout=3)
            if self.source_name == "job_alert_email":
                email_finished.set()
                job_url = "https://aggregator.example/jobs/123"
            else:
                assert email_finished.wait(timeout=3)
                job_url = "https://company.example/jobs/123"
            return [
                JobCandidate(
                    external_job_id="123",
                    title="Protection Engineer",
                    company="Example Utility",
                    company_priority=self.company.priority,
                    location="Toronto, Ontario, Canada",
                    job_url=job_url,
                    source=self.source_name,
                )
            ]

    monkeypatch.setattr(
        "src.services.scanner.create_source",
        lambda company, _config: FakeSource(company),
    )

    result = Scanner(app_config, database, RecordingNotifier()).scan()

    assert len(result.new_jobs) == 1
    assert result.duplicates == 1
    with database.session() as session:
        job = session.scalar(select(Job))
        assert job is not None
        assert job.source == "official_test"
        assert job.job_url == "https://company.example/jobs/123"
