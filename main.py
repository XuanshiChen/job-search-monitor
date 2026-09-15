from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.config.loader import ConfigurationError, load_config
from src.database.database import Database
from src.models.job import JobStatus
from src.services.reporter import Reporter
from src.services.repository import JobRepository
from src.services.deduplicator import Deduplicator
from src.services.email_sync import LinkedInEmailSyncService
from src.matching.scorer import JobScorer
from src.services.scanner import ScanResult, Scanner
from src.utils.logging import configure_logging


PROJECT_ROOT = Path(__file__).resolve().parent


def configure_console_encoding() -> None:
    """Keep Windows consoles from failing on emoji or accented job titles."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="Job Search Monitor",
        description="Local, configuration-driven job discovery and application tracking.",
    )
    parser.add_argument("--verbose", action="store_true", help="Show detailed logs")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="Initialize the SQLite database and directories")

    scan = subparsers.add_parser("scan", help="Scan all enabled company sources")
    scan.add_argument("--company", help="Scan one configured company by exact name")
    scan.add_argument(
        "--new-only", action="store_true", help="Only print newly discovered jobs"
    )

    report = subparsers.add_parser("report", help="Generate today's Markdown digest")
    report.add_argument("--print", action="store_true", dest="print_report")

    dashboard = subparsers.add_parser("dashboard", help="Launch the Streamlit dashboard")
    dashboard.add_argument("--port", type=int, default=8501)

    status = subparsers.add_parser("status", help="Update a job's review/application status")
    status.add_argument("job_id")
    status.add_argument("new_status", choices=[item.value for item in JobStatus])
    status.add_argument("--notes")

    email_import = subparsers.add_parser(
        "email-import", help="Import saved job-alert .eml files"
    )
    email_import.add_argument("paths", nargs="+", type=Path)

    email_sync = subparsers.add_parser(
        "email-sync", help="Synchronize job-alert emails through IMAP"
    )
    email_sync.add_argument(
        "--scan",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Scan imported job alerts after synchronization",
    )
    return parser


def print_scan_result(result: ScanResult, *, new_only: bool = False) -> int:
    print(
        f"Scan complete: {result.fetched} fetched, {len(result.new_jobs)} new, "
        f"{len(result.updated_jobs)} updated, {result.duplicates} duplicates, "
        f"{len(result.closed_jobs)} closed."
    )
    jobs = result.new_jobs if new_only else [*result.new_jobs, *result.updated_jobs]
    for job in sorted(jobs, key=lambda item: item.match_score, reverse=True):
        print(f"[{job.match_score:3}] {job.title} - {job.company} ({job.location})")
        print(f"      {job.job_url}")
    if result.failures:
        print("Source failures:", file=sys.stderr)
        for failure in result.failures:
            print(f"- {failure.company}: {failure.error}", file=sys.stderr)
    return 2 if result.companies and len(result.failures) == len(result.companies) else 0


def main(argv: list[str] | None = None) -> int:
    configure_console_encoding()
    load_dotenv(PROJECT_ROOT / ".env")
    args = build_parser().parse_args(argv)
    try:
        config = load_config(PROJECT_ROOT)
        configure_logging(config.log_path, args.verbose)
        database = Database(config.database_path)
        database.initialize()

        if args.command == "init":
            config.report_directory.mkdir(parents=True, exist_ok=True)
            config.linkedin_email_directory.mkdir(parents=True, exist_ok=True)
            print(f"Initialized Job Search Monitor at {PROJECT_ROOT}")
            print(f"Database: {config.database_path}")
            print(f"Enabled companies: {len(config.enabled_companies())}")
            return 0

        if args.command == "scan":
            result = Scanner(config, database).scan(args.company)
            return print_scan_result(result, new_only=args.new_only)

        if args.command == "email-import":
            service = LinkedInEmailSyncService(config)
            imported = service.import_files(args.paths)
            print(
                f"Email import complete: {imported.imported} saved, "
                f"{imported.skipped} already present."
            )
            source = config.linkedin_email_source()
            result = Scanner(config, database).scan(source.name)
            return print_scan_result(result, new_only=True)

        if args.command == "email-sync":
            service = LinkedInEmailSyncService(config)
            synced = service.sync_imap()
            if not synced.enabled:
                print(
                    "Job-alert IMAP sync is disabled. Set linkedin_email.imap_enabled "
                    "to true and add IMAP credentials to .env."
                )
                return 0
            print(
                f"Email sync complete: {synced.examined} examined, "
                f"{synced.imported} saved, {synced.skipped} skipped."
            )
            if args.scan:
                source = config.linkedin_email_source()
                result = Scanner(config, database).scan(source.name)
                return print_scan_result(result, new_only=True)
            return 0

        if args.command == "report":
            path, content = Reporter(config, database).write()
            print(f"Daily report written to {path}")
            if args.print_report:
                print()
                print(content)
            return 0

        if args.command == "dashboard":
            command = [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                str(PROJECT_ROOT / "src" / "dashboard" / "app.py"),
                "--server.port",
                str(args.port),
            ]
            return subprocess.call(command, cwd=PROJECT_ROOT)

        if args.command == "status":
            repository = JobRepository(
                Deduplicator(float(config.settings.get("dedup_similarity_threshold", 93))),
                JobScorer(config),
            )
            with database.session() as session:
                job = repository.update_status(
                    session, args.job_id, args.new_status, notes=args.notes
                )
            print(f"Updated {job.title} to {job.status}")
            if job.applied_date:
                print(f"Applied date: {job.applied_date.isoformat(timespec='seconds')}Z")
            return 0
    except (ConfigurationError, FileNotFoundError, LookupError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
