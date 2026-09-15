from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path
from threading import Lock
from typing import Any

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import func, select


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from src.config.loader import AppConfig, load_config  # noqa: E402
from src.database.database import Database  # noqa: E402
from src.matching.scorer import JobScorer  # noqa: E402
from src.models.job import Job, JobStatus  # noqa: E402
from src.services.deduplicator import Deduplicator  # noqa: E402
from src.services.dashboard_refresh import (  # noqa: E402
    DashboardRefreshProgress,
    DashboardRefreshResult,
    DashboardRefreshService,
)
from src.services.reporter import Reporter  # noqa: E402
from src.services.repository import JobRepository  # noqa: E402
from src.utils.dates import (  # noqa: E402
    local_today,
    utc_bounds_for_local_day,
    utcnow,
)


st.set_page_config(page_title="Job Search Monitor", page_icon="⚡", layout="wide")


@st.cache_resource
def resources() -> tuple:
    config = load_config(PROJECT_ROOT)
    database = Database(config.database_path)
    database.initialize()
    repository = JobRepository(
        Deduplicator(float(config.settings.get("dedup_similarity_threshold", 93))),
        JobScorer(config),
    )
    return config, database, repository


@st.cache_resource
def startup_refresh_guard() -> dict[str, Any]:
    """Coordinate one automatic refresh per local calendar day."""
    return {"lock": Lock(), "day": None, "result": None}


def refresh_summary(result: DashboardRefreshResult) -> str:
    parts: list[str] = []
    if result.email is None:
        parts.append("Gmail sync did not complete")
    elif result.email.enabled:
        parts.append(
            f"Gmail: {result.email.imported} new, {result.email.skipped} already imported/skipped"
        )
    else:
        parts.append("official company sites only")

    if result.scan is None:
        parts.append("job scan did not complete")
    else:
        parts.append(
            f"scan: {len(result.scan.new_jobs)} new, "
            f"{len(result.scan.updated_jobs)} updated, {result.scan.duplicates} duplicates"
        )
        if result.scan.failures:
            parts.append(f"{len(result.scan.failures)} source failures")
    return " · ".join(parts)


def show_refresh_result(result: DashboardRefreshResult, *, sidebar: bool = False) -> None:
    target = st.sidebar if sidebar else st
    message = refresh_summary(result)
    if result.errors:
        target.warning(message + "\n\n" + "\n\n".join(result.errors))
    else:
        target.success(message)


def run_refresh_with_progress(
    config: AppConfig,
    database: Database,
    *,
    initial_label: str,
) -> DashboardRefreshResult:
    status = st.status(initial_label, expanded=True)
    progress_bar = status.progress(0, text="正在准备刷新…")

    def update_progress(update: DashboardRefreshProgress) -> None:
        progress_bar.progress(update.percent, text=update.message)
        status.update(label=update.message, state="running", expanded=True)

    result = DashboardRefreshService(config, database).run(progress=update_progress)
    final_label = refresh_summary(result)
    status.update(
        label=(
            f"刷新完成 · {final_label}"
            if not result.errors
            else f"刷新完成，但有错误 · {final_label}"
        ),
        state="complete" if not result.errors else "error",
        expanded=bool(result.errors),
    )
    return result


def startup_refresh(
    config: AppConfig,
    database: Database,
) -> DashboardRefreshResult | None:
    """Run on the first Dashboard visit of each local calendar day."""
    dashboard_settings = config.settings.get("dashboard", {})
    if not bool(dashboard_settings.get("refresh_on_start", True)):
        return None
    today = local_today(
        str(config.settings.get("timezone", "America/Vancouver"))
    ).isoformat()
    state = startup_refresh_guard()
    if state["day"] == today:
        return state["result"]
    with state["lock"]:
        if state["day"] != today:
            state["result"] = run_refresh_with_progress(
                config,
                database,
                initial_label="正在启动求职监控…",
            )
            state["day"] = today
    return state["result"]


def load_jobs(database: Database) -> list[Job]:
    with database.session() as session:
        return list(
            session.scalars(
                select(Job).order_by(Job.match_score.desc(), Job.date_first_seen.desc())
            ).all()
        )


def json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
        return [str(item) for item in parsed] if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def overview(database: Database, daily_goal: int, timezone_name: str) -> None:
    today, tomorrow = utc_bounds_for_local_day(local_today(timezone_name), timezone_name)
    with database.session() as session:
        new_today = int(
            session.scalar(
                select(func.count(Job.internal_id)).where(
                    Job.date_first_seen >= today, Job.date_first_seen < tomorrow
                )
            )
            or 0
        )
        high_today = int(
            session.scalar(
                select(func.count(Job.internal_id)).where(
                    Job.date_first_seen >= today,
                    Job.date_first_seen < tomorrow,
                    Job.match_score >= 75,
                )
            )
            or 0
        )
        applications = int(
            session.scalar(
                select(func.count(Job.internal_id)).where(
                    Job.applied_date >= today, Job.applied_date < tomorrow
                )
            )
            or 0
        )
        active = int(
            session.scalar(
                select(func.count(Job.internal_id)).where(Job.is_active.is_(True))
            )
            or 0
        )
    columns = st.columns(5)
    columns[0].metric("New jobs today", new_today)
    columns[1].metric("High priority today", high_today)
    columns[2].metric("Applications today", applications)
    columns[3].metric("Daily goal", f"{applications} / {daily_goal}")
    columns[4].metric("Active jobs", active)
    progress = min(1.0, applications / daily_goal) if daily_goal else 1.0
    st.progress(progress, text=f"{max(0, daily_goal - applications)} applications remaining today")


def filter_jobs(jobs: list[Job], timezone_name: str) -> list[Job]:
    st.sidebar.header("Filters")
    companies = sorted({job.company for job in jobs})
    selected_companies = st.sidebar.multiselect("Company", companies)
    tiers = sorted({job.company_priority for job in jobs})
    selected_tiers = st.sidebar.multiselect("Company tier", tiers)
    provinces = sorted({job.province for job in jobs if job.province})
    selected_provinces = st.sidebar.multiselect("Province", provinces)
    locations = sorted({job.location for job in jobs if job.location})
    selected_locations = st.sidebar.multiselect("Location", locations)
    categories = sorted({job.category for job in jobs})
    selected_categories = st.sidebar.multiselect("Job category", categories)
    selected_statuses = st.sidebar.multiselect(
        "Status", [item.value for item in JobStatus]
    )
    minimum_score = st.sidebar.slider("Minimum score", 0, 100, 45)
    found_within = st.sidebar.selectbox(
        "Date discovered", ["Any time", "Today", "Last 7 days", "Last 30 days"]
    )
    active_only = st.sidebar.checkbox("Active jobs only", value=True)

    cutoff = None
    if found_within == "Today":
        cutoff, _ = utc_bounds_for_local_day(local_today(timezone_name), timezone_name)
    elif found_within == "Last 7 days":
        cutoff = utcnow() - timedelta(days=7)
    elif found_within == "Last 30 days":
        cutoff = utcnow() - timedelta(days=30)

    return [
        job
        for job in jobs
        if (not selected_companies or job.company in selected_companies)
        and (not selected_tiers or job.company_priority in selected_tiers)
        and (not selected_provinces or job.province in selected_provinces)
        and (not selected_locations or job.location in selected_locations)
        and (not selected_categories or job.category in selected_categories)
        and (not selected_statuses or job.status in selected_statuses)
        and job.match_score >= minimum_score
        and (not active_only or job.is_active)
        and (cutoff is None or job.date_first_seen >= cutoff)
    ]


def jobs_table(jobs: list[Job]) -> str | None:
    st.subheader("New jobs and opportunities")
    if not jobs:
        st.info("No jobs match the current filters.")
        return None
    st.caption(
        "点击任意职位行可在下方查看详情并修改状态；点击 **Open job** 可直接打开公司申请页面。"
    )
    data = pd.DataFrame(
        [
            {
                "_job_id": job.internal_id,
                "Score": job.match_score,
                "Job Title": job.title,
                "Open job": job.job_url,
                "Company": job.company,
                "Tier": job.company_priority,
                "Location": job.location,
                "Category": job.category,
                "Date Posted": job.date_posted.date() if job.date_posted else None,
                "Date Found": job.date_first_seen.date(),
                "Status": job.status,
            }
            for job in jobs
        ]
    )
    event = st.dataframe(
        data,
        hide_index=True,
        width="stretch",
        height=460,
        key="jobs_table",
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "_job_id": None,
            "Score": st.column_config.ProgressColumn(
                "Score", min_value=0, max_value=100, width="small"
            ),
            "Job Title": st.column_config.TextColumn("Job title", pinned=True),
            "Open job": st.column_config.LinkColumn(
                "Open job",
                display_text=":material/open_in_new:",
                help="Open the employer's application page",
                width="small",
            ),
            "Date Posted": st.column_config.DateColumn(
                "Date posted", format="YYYY-MM-DD"
            ),
            "Date Found": st.column_config.DateColumn(
                "Date found", format="YYYY-MM-DD"
            ),
        },
    )
    selected_rows = event.selection.rows
    if not selected_rows:
        return None
    return str(data.iloc[selected_rows[0]]["_job_id"])


def job_detail(
    jobs: list[Job],
    database: Database,
    repository: JobRepository,
    selected_job_id: str | None = None,
) -> None:
    st.subheader("Job detail and status")
    if not jobs:
        return
    labels = {
        job.internal_id: f"{job.match_score:03} · {job.title} — {job.company}" for job in jobs
    }
    if (
        selected_job_id
        and st.session_state.get("last_table_selected_job") != selected_job_id
    ):
        st.session_state["selected_job_id"] = selected_job_id
        st.session_state["last_table_selected_job"] = selected_job_id
    if st.session_state.get("selected_job_id") not in labels:
        st.session_state["selected_job_id"] = next(iter(labels))
    job_id = st.selectbox(
        "Select a job to review or update",
        list(labels),
        format_func=lambda value: labels[value],
        key="selected_job_id",
        persist_state="session",
        help="You can select a row in the table above or choose a job here.",
    )
    job = next(item for item in jobs if item.internal_id == job_id)
    left, right = st.columns([2, 1])
    with left:
        st.markdown(f"### {job.title}")
        st.write(f"**{job.company}** · {job.location or 'Location not specified'}")
        st.write(f"Category: {job.category} · Source: {job.source}")
        if job.job_url:
            st.link_button("Open application page", job.job_url)
        st.markdown("#### Match explanation")
        for reason in json_list(job.match_reason):
            st.write(f"- {reason}")
        matches = json_list(job.matched_keywords)
        if matches:
            st.caption("Matched keywords: " + ", ".join(matches))
        with st.expander("Full description", expanded=False):
            st.write(job.description or "No description was supplied by this source.")

    with right:
        st.metric("Match score", f"{job.match_score}/100")
        statuses = [item.value for item in JobStatus]
        with st.form(f"job-form-{job.internal_id}"):
            selected_status = st.selectbox(
                "Status", statuses, index=statuses.index(job.status)
            )
            notes = st.text_area("Review notes", value=job.notes or "")
            resume_version = st.text_input("Resume version", value=job.resume_version or "")
            cover_letter = st.checkbox(
                "Cover letter used", value=bool(job.cover_letter_used)
            )
            referral = st.text_input("Referral", value=job.referral or "")
            contact = st.text_input("Contact person", value=job.contact_person or "")
            application_notes = st.text_area(
                "Application notes", value=job.application_notes or ""
            )
            submitted = st.form_submit_button(
                "Save status and notes / 保存状态与备注",
                type="primary",
            )
        if submitted:
            with database.session() as session:
                repository.update_status(
                    session,
                    job.internal_id,
                    selected_status,
                    notes=notes,
                    application_fields={
                        "resume_version": resume_version,
                        "cover_letter_used": cover_letter,
                        "referral": referral,
                        "contact_person": contact,
                        "application_notes": application_notes,
                    },
                )
            st.success("Job updated.")
            st.rerun()


def analytics(database: Database, timezone_name: str) -> None:
    st.subheader("Application analytics")
    now = utcnow()
    today = local_today(timezone_name)
    week_day = today - timedelta(days=today.weekday())
    week_start, _ = utc_bounds_for_local_day(week_day, timezone_name)
    month_start, _ = utc_bounds_for_local_day(today.replace(day=1), timezone_name)
    with database.session() as session:
        applications_week = int(
            session.scalar(select(func.count(Job.internal_id)).where(Job.applied_date >= week_start))
            or 0
        )
        applications_month = int(
            session.scalar(select(func.count(Job.internal_id)).where(Job.applied_date >= month_start))
            or 0
        )
        interviews = int(
            session.scalar(select(func.count(Job.internal_id)).where(Job.status == "INTERVIEW")) or 0
        )
        rejections = int(
            session.scalar(select(func.count(Job.internal_id)).where(Job.status == "REJECTED")) or 0
        )
        total_applications = int(
            session.scalar(select(func.count(Job.internal_id)).where(Job.applied_date.is_not(None)))
            or 0
        )
        by_company = session.execute(
            select(Job.company, func.count(Job.internal_id))
            .where(Job.applied_date.is_not(None))
            .group_by(Job.company)
            .order_by(func.count(Job.internal_id).desc())
        ).all()
        by_category = session.execute(
            select(Job.category, func.count(Job.internal_id))
            .where(Job.applied_date.is_not(None))
            .group_by(Job.category)
            .order_by(func.count(Job.internal_id).desc())
        ).all()
    columns = st.columns(5)
    columns[0].metric("Applications this week", applications_week)
    columns[1].metric("Applications this month", applications_month)
    columns[2].metric("Interviews", interviews)
    columns[3].metric("Interview rate", f"{(interviews / total_applications * 100):.1f}%" if total_applications else "0%")
    columns[4].metric("Rejections", rejections)
    chart_columns = st.columns(2)
    if by_company:
        chart_columns[0].bar_chart(
            pd.DataFrame(by_company, columns=["Company", "Applications"]).set_index("Company")
        )
    else:
        chart_columns[0].info("Application counts by company will appear here.")
    if by_category:
        chart_columns[1].bar_chart(
            pd.DataFrame(by_category, columns=["Category", "Applications"]).set_index("Category")
        )
    else:
        chart_columns[1].info("Application counts by category will appear here.")


def daily_report(config: AppConfig, database: Database) -> None:
    st.subheader("Daily report")
    st.write(
        "Generate this after you finish applying so the report includes every job "
        "you marked APPLIED today."
    )
    if st.button(
        "Generate today's report",
        key="generate_daily_report",
        type="primary",
        icon=":material/description:",
    ):
        try:
            with st.spinner("Generating today's report..."):
                path, content = Reporter(config, database).write()
            st.session_state["daily_report_path"] = str(path)
            st.session_state["daily_report_content"] = content
            st.success(f"Report saved as {path.name}")
        except Exception as exc:
            st.error(f"The report could not be generated: {exc}")

    content = str(st.session_state.get("daily_report_content", ""))
    report_path = str(st.session_state.get("daily_report_path", ""))
    if not content:
        st.info("No report has been generated in this Dashboard session yet.")
        return

    path = Path(report_path)
    st.download_button(
        "Download Markdown report",
        data=content,
        file_name=path.name,
        mime="text/markdown",
        key="download_daily_report",
        icon=":material/download:",
        on_click="ignore",
    )
    st.caption(f"Saved locally to {path}")
    with st.container(border=True):
        st.markdown(content)


def main() -> None:
    config, database, repository = resources()
    timezone_name = str(config.settings.get("timezone", "America/Vancouver"))
    st.title("⚡ Job Search Monitor")
    st.caption("Your local power-engineering job-search command center")

    st.sidebar.header("Data refresh")
    manual_refresh_requested = st.sidebar.button(
        "Refresh jobs now",
        key="manual_job_refresh",
        icon=":material/refresh:",
        width="stretch",
    )
    if manual_refresh_requested:
        manual_refresh = run_refresh_with_progress(
            config,
            database,
            initial_label="正在手动刷新职位…",
        )
        state = startup_refresh_guard()
        state["result"] = manual_refresh
        state["day"] = local_today(timezone_name).isoformat()
        show_refresh_result(manual_refresh)
    else:
        automatic_refresh = startup_refresh(config, database)
        if automatic_refresh is not None:
            show_refresh_result(automatic_refresh)

    overview(
        database,
        int(config.settings.get("daily_application_goal", 3)),
        timezone_name,
    )
    all_jobs = load_jobs(database)
    filtered = filter_jobs(all_jobs, timezone_name)
    tabs = st.tabs(["Jobs", "Analytics", "Daily report"])
    with tabs[0]:
        selected_job_id = jobs_table(filtered)
        job_detail(filtered, database, repository, selected_job_id)
    with tabs[1]:
        analytics(database, timezone_name)
    with tabs[2]:
        daily_report(config, database)


main()
