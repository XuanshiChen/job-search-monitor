from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import streamlit as st
from sqlalchemy import select
from streamlit.testing.v1 import AppTest

from src.config.loader import AppConfig
from src.models.job import Job
from src.services.dashboard_refresh import (
    DashboardRefreshResult,
    DashboardRefreshService,
)
from src.services.email_sync import EmailSyncResult
from src.services.reporter import Reporter
from src.services.scanner import ScanResult, Scanner
from src.utils.dates import utcnow


def test_daily_report_can_be_generated_from_dashboard(tmp_path: Path) -> None:
    st.cache_resource.clear()
    now = utcnow()
    refresh = DashboardRefreshResult(
        started_at=now,
        finished_at=now,
        email=EmailSyncResult(enabled=False),
        scan=ScanResult(run_id="dashboard-test"),
    )
    report_path = tmp_path / "daily-2026-09-01.md"
    report_content = "# Test daily report\n\nApplied today: **3 / 3**"
    app_path = Path(__file__).resolve().parents[1] / "src" / "dashboard" / "app.py"

    with (
        patch.object(DashboardRefreshService, "run", return_value=refresh),
        patch.object(
            Reporter,
            "write",
            return_value=(report_path, report_content),
        ) as write_report,
    ):
        app = AppTest.from_file(str(app_path), default_timeout=30).run()
        app.button(key="generate_daily_report").click().run()

    assert not app.exception
    assert write_report.call_count == 1
    assert any("Report saved as" in item.value for item in app.success)
    assert len(app.get("download_button")) == 1
    assert any("Test daily report" in item.value for item in app.markdown)
    st.cache_resource.clear()


def test_dashboard_job_link_and_status_update_work(
    app_config: AppConfig, database
) -> None:
    Scanner(app_config, database).scan()
    now = utcnow()
    refresh = DashboardRefreshResult(
        started_at=now,
        finished_at=now,
        email=EmailSyncResult(enabled=False),
        scan=ScanResult(run_id="dashboard-status-test"),
    )
    app_path = Path(__file__).resolve().parents[1] / "src" / "dashboard" / "app.py"
    st.cache_resource.clear()

    with (
        patch("src.config.loader.load_config", return_value=app_config),
        patch.object(DashboardRefreshService, "run", return_value=refresh),
    ):
        app = AppTest.from_file(str(app_path), default_timeout=30).run()
        table = app.dataframe[0].value
        assert "Open job" in table.columns
        assert table["Open job"].str.startswith("https://").all()

        status_box = next(item for item in app.selectbox if item.label == "Status")
        save_button = next(
            item for item in app.button if "Save status and notes" in item.label
        )
        selected_job_id = app.session_state["selected_job_id"]
        status_box.select("APPLIED")
        save_button.click().run()

    assert not app.exception
    with database.session() as session:
        job = session.scalar(select(Job).where(Job.internal_id == selected_job_id))
        assert job is not None
        assert job.status == "APPLIED"
        assert job.applied_date is not None
    st.cache_resource.clear()
