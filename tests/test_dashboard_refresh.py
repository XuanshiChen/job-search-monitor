from __future__ import annotations

from src.services.dashboard_refresh import DashboardRefreshService
from src.services.email_sync import EmailSyncResult, LinkedInEmailSyncService
from src.services.scanner import ScanProgress, ScanResult, Scanner


def test_dashboard_refresh_runs_email_sync_before_scan(
    app_config, database, monkeypatch
) -> None:
    app_config.settings["linkedin_email"]["imap_enabled"] = True
    calls: list[str] = []

    def sync_email(_service: LinkedInEmailSyncService) -> EmailSyncResult:
        calls.append("email")
        return EmailSyncResult(enabled=True, imported=2, examined=3)

    def scan_jobs(_scanner: Scanner) -> ScanResult:
        calls.append("scan")
        return ScanResult(run_id="test-run", duplicates=4)

    monkeypatch.setattr(LinkedInEmailSyncService, "sync_imap", sync_email)
    monkeypatch.setattr(Scanner, "scan", scan_jobs)

    result = DashboardRefreshService(app_config, database).run()

    assert calls == ["email", "scan"]
    assert result.email is not None
    assert result.email.imported == 2
    assert result.scan is not None
    assert result.scan.duplicates == 4
    assert result.errors == []


def test_dashboard_refresh_continues_scan_when_email_sync_fails(
    app_config, database, monkeypatch
) -> None:
    app_config.settings["linkedin_email"]["imap_enabled"] = True
    scan_calls = 0

    def fail_email(_service: LinkedInEmailSyncService) -> EmailSyncResult:
        raise RuntimeError("mailbox unavailable")

    def scan_jobs(_scanner: Scanner) -> ScanResult:
        nonlocal scan_calls
        scan_calls += 1
        return ScanResult(run_id="test-run")

    monkeypatch.setattr(LinkedInEmailSyncService, "sync_imap", fail_email)
    monkeypatch.setattr(Scanner, "scan", scan_jobs)

    result = DashboardRefreshService(app_config, database).run()

    assert scan_calls == 1
    assert result.scan is not None
    assert result.email is None
    assert result.errors == ["Email sync: mailbox unavailable"]


def test_dashboard_refresh_reports_stage_and_company_progress(
    app_config, database, monkeypatch
) -> None:
    app_config.settings["linkedin_email"]["imap_enabled"] = True
    updates = []

    monkeypatch.setattr(
        LinkedInEmailSyncService,
        "sync_imap",
        lambda _service: EmailSyncResult(enabled=True),
    )

    def scan_jobs(_scanner: Scanner, *, progress=None) -> ScanResult:
        assert progress is not None
        progress(
            ScanProgress(
                phase="fetching",
                message="Scanning 1/2: Hydro One",
                current=0,
                total=2,
                company="Hydro One",
            )
        )
        progress(
            ScanProgress(
                phase="company_complete",
                message="Completed 1/2: Hydro One (3 jobs)",
                current=1,
                total=2,
                company="Hydro One",
            )
        )
        return ScanResult(run_id="progress-test")

    monkeypatch.setattr(Scanner, "scan", scan_jobs)

    DashboardRefreshService(app_config, database).run(progress=updates.append)

    assert updates[0].phase == "email"
    assert updates[0].percent == 2
    assert any("Hydro One" in update.message for update in updates)
    assert updates[-1].phase == "complete"
    assert updates[-1].percent == 100


def test_dashboard_refresh_skips_email_service_in_official_only_mode(
    app_config, database, monkeypatch
) -> None:
    app_config.settings["linkedin_email"]["imap_enabled"] = False
    updates = []

    def unexpected_email_call(_service: LinkedInEmailSyncService) -> EmailSyncResult:
        raise AssertionError("Email service must not run in official-only mode")

    monkeypatch.setattr(LinkedInEmailSyncService, "sync_imap", unexpected_email_call)
    monkeypatch.setattr(
        Scanner,
        "scan",
        lambda _scanner, *, progress=None: ScanResult(run_id="official-only"),
    )

    result = DashboardRefreshService(app_config, database).run(progress=updates.append)

    assert result.email is not None
    assert result.email.enabled is False
    assert result.errors == []
    assert updates[0].phase == "official_only"
    assert "已关闭" in updates[0].message
