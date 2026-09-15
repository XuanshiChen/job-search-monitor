from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path

from src.config.loader import CompanyConfig
from src.services.email_sync import LinkedInEmailSyncService
from src.services.notifier import Notifier
from src.services.scanner import Scanner
from src.models.job import Job
from src.sources.linkedin_email import (
    LinkedInEmailParser,
    job_alert_identity,
    linkedin_job_identity,
)


def html_alert() -> EmailMessage:
    message = EmailMessage()
    message["From"] = "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>"
    message["To"] = "candidate@example.test"
    message["Subject"] = "New jobs for Protection Engineer"
    message["Date"] = "Tue, 01 Sep 2026 08:00:00 -0700"
    message["Message-ID"] = "<linkedin-alert-1@example.test>"
    message.set_content("HTML version available")
    message.add_alternative(
        """
        <html><body>
          <table><tr><td>
            <a href="https://www.linkedin.com/comm/jobs/view/432100001?trackingId=abc">
              Protection &amp; Control Engineer
            </a>
            <div>Siemens Canada</div>
            <div>Oakville, Ontario, Canada</div>
          </td></tr></table>
          <table><tr><td>
            <a href="https://www.linkedin.com/jobs/collections/recommended/?currentJobId=432100002">
              Junior Substation Engineer
            </a>
            <div>Grid Consulting Inc.</div>
            <div>Burnaby, British Columbia, Canada</div>
          </td></tr></table>
        </body></html>
        """,
        subtype="html",
    )
    return message


def test_linkedin_job_urls_are_normalized() -> None:
    assert linkedin_job_identity(
        "https://www.linkedin.com/comm/jobs/view/protection-engineer-432100001?trackingId=x"
    ) == ("432100001", "https://www.linkedin.com/jobs/view/432100001")
    assert linkedin_job_identity(
        "https://www.linkedin.com/jobs/search/?currentJobId=432100002"
    ) == ("432100002", "https://www.linkedin.com/jobs/view/432100002")


def test_indeed_glassdoor_and_job_bank_urls_are_normalized() -> None:
    assert job_alert_identity("https://ca.indeed.com/viewjob?jk=abc123&utm_source=alert") == (
        "indeed",
        "indeed:abc123",
        "https://ca.indeed.com/viewjob?jk=abc123",
    )
    assert job_alert_identity(
        "https://www.glassdoor.ca/partner/jobListing.htm?jobListingId=987654"
    ) == (
        "glassdoor",
        "glassdoor:987654",
        "https://www.glassdoor.ca/job-listing/j?jl=987654",
    )
    assert job_alert_identity(
        "https://www.jobbank.gc.ca/jobsearch/jobposting/44556677"
    ) == (
        "jobbank",
        "jobbank:44556677",
        "https://www.jobbank.gc.ca/jobsearch/jobposting/44556677",
    )


def test_indeed_email_alert_is_parsed(app_config) -> None:
    message = EmailMessage()
    message["From"] = "Indeed Job Alerts <alerts@indeed.com>"
    message["Subject"] = "Electrical Engineer jobs"
    message.set_content("HTML version available")
    message.add_alternative(
        """
        <table><tr><td>
          <a href="https://ca.indeed.com/viewjob?jk=abc123&amp;from=ja">
            Protection Engineer
          </a>
          <div>Hydro One Networks Inc</div>
          <div>Toronto, Ontario, Canada</div>
        </td></tr></table>
        """,
        subtype="html",
    )

    job = LinkedInEmailParser(app_config).parse_message(message)[0]

    assert job.external_job_id == "indeed:abc123"
    assert job.source == "indeed_email"
    assert job.company == "Hydro One"
    assert job.company_priority == "critical"


def test_html_job_alert_is_parsed_and_company_alias_resolved(app_config) -> None:
    app_config.companies.append(
        CompanyConfig(
            name="Siemens",
            priority="critical",
            aliases=["Siemens Canada"],
            source_type="greenhouse",
            enabled=False,
        )
    )
    jobs = LinkedInEmailParser(app_config).parse_message(html_alert())

    assert len(jobs) == 2
    assert jobs[0].external_job_id == "432100001"
    assert jobs[0].title == "Protection & Control Engineer"
    assert jobs[0].company == "Siemens"
    assert jobs[0].company_priority == "critical"
    assert jobs[0].province == "Ontario"
    assert jobs[1].company == "Grid Consulting Inc."
    assert jobs[1].province == "British Columbia"


def test_plain_text_alert_is_supported(app_config) -> None:
    message = EmailMessage()
    message["From"] = "jobs-noreply@linkedin.com"
    message["Subject"] = "LinkedIn Job Alert"
    message.set_content(
        """Relay Protection Engineer
GE Vernova
Markham, Ontario, Canada
https://www.linkedin.com/jobs/view/432100003?trackingId=abc
"""
    )

    jobs = LinkedInEmailParser(app_config).parse_message(message)

    assert len(jobs) == 1
    assert jobs[0].title == "Relay Protection Engineer"
    assert jobs[0].company == "GE Vernova"
    assert jobs[0].location == "Markham, Ontario, Canada"


def test_manual_email_import_is_content_deduplicated(app_config, tmp_path: Path) -> None:
    app_config.settings["linkedin_email"]["directory"] = str(tmp_path / "inbox")
    source_file = tmp_path / "alert.eml"
    source_file.write_bytes(html_alert().as_bytes())
    service = LinkedInEmailSyncService(app_config)

    first = service.import_files([source_file])
    second = service.import_files([source_file])

    assert first.imported == 1
    assert first.skipped == 0
    assert second.imported == 0
    assert second.skipped == 1
    assert len(list((tmp_path / "inbox").glob("*.eml"))) == 1


class NoopNotifier(Notifier):
    def notify_job(self, job: Job) -> bool:
        return True


def test_linkedin_email_source_runs_through_scoring_and_deduplication(
    app_config, database, tmp_path: Path
) -> None:
    inbox = tmp_path / "linkedin"
    inbox.mkdir()
    (inbox / "alert.eml").write_bytes(html_alert().as_bytes())
    source = app_config.linkedin_email_source()
    source.search_url = str(inbox)

    scanner = Scanner(app_config, database, NoopNotifier())
    first = scanner.scan(source.name)
    second = scanner.scan(source.name)

    assert first.fetched == 2
    assert len(first.new_jobs) == 2
    assert all(job.source == "linkedin_email" for job in first.new_jobs)
    assert second.fetched == 2
    assert second.duplicates == 2


def test_imap_sync_saves_matching_messages_without_marking_read(
    app_config, tmp_path: Path, monkeypatch
) -> None:
    inbox = tmp_path / "imap-inbox"
    app_config.settings["linkedin_email"].update(
        {"directory": str(inbox), "imap_enabled": True, "mark_as_read": False}
    )
    raw = html_alert().as_bytes()

    class FakeImap:
        def __init__(self, **kwargs):
            self.store_called = False

        def login(self, username, password):
            return "OK", []

        def select(self, mailbox, readonly=True):
            assert readonly is True
            return "OK", [b"1"]

        def uid(self, command, *args):
            if command == "search":
                return "OK", [b"101"]
            if command == "fetch":
                return "OK", [(b"101 (BODY[] {1})", raw), b")"]
            if command == "store":
                self.store_called = True
                return "OK", []
            raise AssertionError(command)

        def logout(self):
            return "BYE", []

    monkeypatch.setenv("LINKEDIN_IMAP_HOST", "imap.example.test")
    monkeypatch.setenv("LINKEDIN_IMAP_USERNAME", "candidate@example.test")
    monkeypatch.setenv("LINKEDIN_IMAP_PASSWORD", "app-password")
    monkeypatch.setattr("src.services.email_sync.imaplib.IMAP4_SSL", FakeImap)

    result = LinkedInEmailSyncService(app_config).sync_imap()

    assert result.enabled is True
    assert result.examined == 1
    assert result.imported == 1
    assert len(list(inbox.glob("*.eml"))) == 1
