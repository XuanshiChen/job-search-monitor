from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

from src.config.loader import AppConfig
from src.database.database import Database
from src.models.job import Job
from src.utils.dates import local_today, utc_bounds_for_local_day


@dataclass(slots=True)
class DailySummary:
    day: date
    new_jobs: list[Job]
    applications_today: int
    total_active: int
    daily_goal: int

    @property
    def remaining(self) -> int:
        return max(0, self.daily_goal - self.applications_today)


class Reporter:
    def __init__(self, config: AppConfig, database: Database):
        self.config = config
        self.database = database

    def summary(self, day: date | None = None) -> DailySummary:
        timezone_name = str(self.config.settings.get("timezone", "America/Vancouver"))
        selected = day or local_today(timezone_name)
        beginning, end = utc_bounds_for_local_day(selected, timezone_name)
        minimum_score = int(self.config.settings.get("minimum_job_score", 45))
        with self.database.session() as session:
            new_jobs = list(
                session.scalars(
                    select(Job)
                    .where(
                        Job.date_first_seen >= beginning,
                        Job.date_first_seen < end,
                        Job.match_score >= minimum_score,
                    )
                    .order_by(Job.match_score.desc(), Job.company_priority, Job.date_first_seen)
                ).all()
            )
            applications = int(
                session.scalar(
                    select(func.count(Job.internal_id)).where(
                        Job.applied_date >= beginning, Job.applied_date < end
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
        return DailySummary(
            day=selected,
            new_jobs=new_jobs,
            applications_today=applications,
            total_active=active,
            daily_goal=int(self.config.settings.get("daily_application_goal", 3)),
        )

    def render_markdown(self, summary: DailySummary) -> str:
        high_cutoff = int(self.config.settings.get("high_priority_score", 75))
        medium_cutoff = int(self.config.settings.get("medium_priority_score", 55))
        high = [job for job in summary.new_jobs if job.match_score >= high_cutoff]
        medium = [
            job for job in summary.new_jobs if medium_cutoff <= job.match_score < high_cutoff
        ]
        low = [job for job in summary.new_jobs if job.match_score < medium_cutoff]
        lines = [
            "# JOB SEARCH DAILY REPORT",
            "",
            summary.day.strftime("%B %d, %Y"),
            "",
            f"New relevant jobs found: **{len(summary.new_jobs)}**",
            f"High priority: **{len(high)}**",
            f"Medium priority: **{len(medium)}**",
            f"Low priority: **{len(low)}**",
            "",
            "## TOP JOBS TODAY",
            "",
        ]
        if not summary.new_jobs:
            lines.extend(("No new jobs met the configured minimum score today.", ""))
        for index, job in enumerate(summary.new_jobs[:15], 1):
            reasons = _json_list(job.match_reason)
            lines.extend(
                (
                    f"### {index}. {job.title}",
                    "",
                    f"Company: {job.company}  ",
                    f"Location: {job.location or 'Not specified'}  ",
                    f"Score: **{job.match_score}/100**  ",
                    f"Status: {job.status}",
                    "",
                    "Why:",
                    "",
                    *(f"- {reason}" for reason in reasons),
                    "",
                    f"[Open Job]({job.job_url})",
                    "",
                )
            )
        unreviewed_high = sum(job.status == "NEW" for job in high)
        lines.extend(
            (
                "## TODAY'S APPLICATION TARGET",
                "",
                f"Daily goal: **{summary.daily_goal}**",
                f"Applied today: **{summary.applications_today} / {summary.daily_goal}**",
                f"Remaining today: **{summary.remaining}**",
                f"Unreviewed high-priority jobs: **{unreviewed_high}**",
                f"Total active jobs: **{summary.total_active}**",
                "",
            )
        )
        return "\n".join(lines)

    def write(self, day: date | None = None) -> tuple[Path, str]:
        summary = self.summary(day)
        content = self.render_markdown(summary)
        directory = self.config.report_directory
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"daily-{summary.day.isoformat()}.md"
        path.write_text(content, encoding="utf-8")
        return path, content


def _json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
        return [str(item) for item in parsed] if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []
