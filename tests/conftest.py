from __future__ import annotations

import copy
from pathlib import Path

import pytest

from src.config.loader import AppConfig, load_config
from src.database.database import Database


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    original = load_config()
    settings = copy.deepcopy(original.settings)
    companies = copy.deepcopy(original.companies)
    email_directory = tmp_path / "linkedin_emails"
    email_directory.mkdir()
    settings["database_path"] = str(tmp_path / "jobs.db")
    settings["log_path"] = str(tmp_path / "job_monitor.log")
    settings["report_directory"] = str(tmp_path / "reports")
    settings["notifications"]["desktop_enabled"] = False
    settings["linkedin_email"]["directory"] = str(email_directory)
    settings["linkedin_email"]["imap_enabled"] = False
    for company in companies:
        company.enabled = company.source_type in {"sample", "linkedin_email", "job_alert_email"}
        if company.source_type in {"linkedin_email", "job_alert_email"}:
            company.search_url = str(email_directory)
    return AppConfig(
        root=original.root,
        settings=settings,
        keywords=copy.deepcopy(original.keywords),
        companies=companies,
    )


@pytest.fixture
def database(app_config: AppConfig) -> Database:
    database = Database(app_config.database_path)
    database.initialize()
    yield database
    database.dispose()
