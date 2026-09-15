from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from src.utils.dates import utcnow
from src.utils.text import canonicalize_url, content_hash, dedup_fingerprint, normalize_text


class Base(DeclarativeBase):
    pass


class JobStatus(StrEnum):
    NEW = "NEW"
    REVIEW = "REVIEW"
    INTERESTED = "INTERESTED"
    APPLY = "APPLY"
    APPLIED = "APPLIED"
    INTERVIEW = "INTERVIEW"
    REJECTED = "REJECTED"
    CLOSED = "CLOSED"
    SKIPPED = "SKIPPED"


@dataclass(slots=True)
class JobCandidate:
    title: str
    company: str
    location: str
    job_url: str
    source: str
    source_url: str = ""
    external_job_id: str | None = None
    company_priority: str = "tier_3"
    city: str = ""
    province: str = ""
    country: str = ""
    remote_type: str = "unknown"
    description: str = ""
    date_posted: datetime | None = None
    employment_type: str = ""
    experience_level: str = ""
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.title = " ".join(self.title.split())
        self.company = " ".join(self.company.split())
        self.location = " ".join(self.location.split())
        self.job_url = canonicalize_url(self.job_url)
        self.source_url = canonicalize_url(self.source_url)
        self.external_job_id = str(self.external_job_id) if self.external_job_id else None
        self.remote_type = (self.remote_type or "unknown").casefold()
        if self.date_posted and self.date_posted.tzinfo is not None:
            self.date_posted = self.date_posted.astimezone(UTC).replace(tzinfo=None)

    @property
    def fingerprint(self) -> str:
        return dedup_fingerprint(self.company, self.title, self.location)

    @property
    def normalized_company(self) -> str:
        return normalize_text(self.company)

    @property
    def hash(self) -> str:
        return content_hash(
            self.title,
            self.company,
            self.location,
            self.description,
            self.date_posted,
            self.employment_type,
            self.experience_level,
            self.salary_min,
            self.salary_max,
        )

    def snapshot(self) -> dict[str, Any]:
        value = asdict(self)
        value["date_posted"] = self.date_posted.isoformat() if self.date_posted else None
        return value


class Job(Base):
    __tablename__ = "jobs"

    internal_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    external_job_id: Mapped[str | None] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(500), index=True)
    company: Mapped[str] = mapped_column(String(255), index=True)
    normalized_company: Mapped[str] = mapped_column(String(255), index=True)
    company_priority: Mapped[str] = mapped_column(String(20), default="tier_3", index=True)
    location: Mapped[str] = mapped_column(String(500), default="")
    city: Mapped[str] = mapped_column(String(255), default="")
    province: Mapped[str] = mapped_column(String(255), default="", index=True)
    country: Mapped[str] = mapped_column(String(255), default="", index=True)
    remote_type: Mapped[str] = mapped_column(String(30), default="unknown", index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    job_url: Mapped[str] = mapped_column(Text, default="")
    canonical_url: Mapped[str] = mapped_column(Text, default="", index=True)
    source: Mapped[str] = mapped_column(String(100), index=True)
    source_url: Mapped[str] = mapped_column(Text, default="")
    date_posted: Mapped[datetime | None] = mapped_column(DateTime)
    date_first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    date_last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    date_closed: Mapped[datetime | None] = mapped_column(DateTime)
    employment_type: Mapped[str] = mapped_column(String(100), default="")
    experience_level: Mapped[str] = mapped_column(String(100), default="")
    salary_min: Mapped[float | None] = mapped_column(Float)
    salary_max: Mapped[float | None] = mapped_column(Float)
    salary_currency: Mapped[str] = mapped_column(String(12), default="")
    category: Mapped[str] = mapped_column(String(100), default="Other", index=True)
    matched_keywords: Mapped[str] = mapped_column(Text, default="[]")
    excluded_keywords: Mapped[str] = mapped_column(Text, default="[]")
    match_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    match_reason: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.NEW.value, index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    applied_date: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    resume_version: Mapped[str] = mapped_column(String(255), default="")
    cover_letter_used: Mapped[bool] = mapped_column(Boolean, default=False)
    referral: Mapped[str] = mapped_column(String(255), default="")
    contact_person: Mapped[str] = mapped_column(String(255), default="")
    application_notes: Mapped[str] = mapped_column(Text, default="")
    dedup_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    raw_data: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    occurrences: Mapped[list[JobOccurrence]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    events: Mapped[list[JobEvent]] = relationship(back_populates="job", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_jobs_company_external", "normalized_company", "external_job_id"),
        Index("ix_jobs_active_score", "is_active", "match_score"),
    )


class JobOccurrence(Base):
    __tablename__ = "job_occurrences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.internal_id"), index=True)
    source: Mapped[str] = mapped_column(String(100), index=True)
    normalized_company: Mapped[str] = mapped_column(String(255), index=True)
    external_job_id: Mapped[str | None] = mapped_column(String(255), index=True)
    canonical_url: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str] = mapped_column(Text, default="")
    date_first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    date_last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    job: Mapped[Job] = relationship(back_populates="occurrences")

    __table_args__ = (
        UniqueConstraint("job_id", "source", "external_job_id", name="uq_occurrence_identity"),
    )


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.internal_id"), index=True)
    event_type: Mapped[str] = mapped_column(String(30), index=True)
    details: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    job: Mapped[Job] = relationship(back_populates="events")


class SchemaVersion(Base):
    __tablename__ = "schema_version"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(30), default="RUNNING")
    companies_attempted: Mapped[int] = mapped_column(Integer, default=0)
    companies_succeeded: Mapped[int] = mapped_column(Integer, default=0)
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    new_jobs: Mapped[int] = mapped_column(Integer, default=0)
    updated_jobs: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    closed_jobs: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[str] = mapped_column(Text, default="[]")


class SourceRun(Base):
    __tablename__ = "source_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_run_id: Mapped[str] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    company: Mapped[str] = mapped_column(String(255), index=True)
    source: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30))
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    new_jobs: Mapped[int] = mapped_column(Integer, default=0)
    updated_jobs: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
