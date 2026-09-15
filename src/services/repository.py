from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.matching.scorer import JobScorer, ScoreResult
from src.models.job import Job, JobCandidate, JobEvent, JobOccurrence, JobStatus
from src.services.deduplicator import Deduplicator
from src.utils.dates import utcnow
from src.utils.text import content_hash, dedup_fingerprint, normalize_text


@dataclass(slots=True)
class UpsertResult:
    action: str
    job: Job
    dedup_signal: str | None = None


class JobRepository:
    TRACKED_FIELDS = (
        "title",
        "location",
        "description",
        "date_posted",
        "employment_type",
        "experience_level",
        "salary_min",
        "salary_max",
        "salary_currency",
    )

    def __init__(self, deduplicator: Deduplicator, scorer: JobScorer):
        self.deduplicator = deduplicator
        self.scorer = scorer

    def upsert(self, session: Session, candidate: JobCandidate) -> UpsertResult:
        now = utcnow()
        match = self.deduplicator.find(session, candidate)
        if match.job is None:
            score = self.scorer.score(candidate)
            job = Job(
                **self._candidate_values(candidate),
                **self._score_values(score),
                date_first_seen=now,
                date_last_seen=now,
                status=JobStatus.NEW.value,
                is_active=True,
            )
            session.add(job)
            session.flush()
            self._record_occurrence(session, job, candidate, now)
            self._event(
                session,
                job,
                "NEW",
                {"source": candidate.source, "snapshot": candidate.snapshot()},
            )
            return UpsertResult("NEW", job)

        job = match.job
        old_values = {field: getattr(job, field) for field in self.TRACKED_FIELDS}
        merged = self._merged_candidate(job, candidate)
        merged_hash = merged.hash
        reopened = not job.is_active
        changed = merged_hash != job.content_hash

        if changed or reopened:
            score = self.scorer.score(merged)
            for key, value in self._candidate_values(merged).items():
                setattr(job, key, value)
            for key, value in self._score_values(score).items():
                setattr(job, key, value)
            job.is_active = True
            job.date_closed = None
            if reopened:
                job.status = JobStatus.NEW.value
            changed_fields = [
                field for field, old in old_values.items() if getattr(job, field) != old
            ]
            changes = {
                field: {"before": old_values[field], "after": getattr(job, field)}
                for field in changed_fields
            }
            self._event(
                session,
                job,
                "REOPENED" if reopened else "UPDATED",
                {
                    "source": candidate.source,
                    "changed_fields": changed_fields,
                    "changes": changes,
                    "dedup_signal": match.signal,
                    "incoming_snapshot": candidate.snapshot(),
                },
            )

        job.date_last_seen = now
        self._record_occurrence(session, job, candidate, now)
        return UpsertResult("UPDATED" if changed or reopened else "DUPLICATE", job, match.signal)

    def update_status(
        self,
        session: Session,
        job_id: str,
        status: str | JobStatus,
        *,
        notes: str | None = None,
        application_fields: dict[str, Any] | None = None,
    ) -> Job:
        job = session.get(Job, job_id)
        if not job:
            raise LookupError(f"Job {job_id!r} was not found")
        status_value = status.value if isinstance(status, JobStatus) else status.upper()
        try:
            JobStatus(status_value)
        except ValueError as exc:
            raise ValueError(f"Invalid job status: {status_value}") from exc

        old_status = job.status
        job.status = status_value
        if status_value == JobStatus.CLOSED.value:
            job.is_active = False
            job.date_closed = job.date_closed or utcnow()
        elif old_status == JobStatus.CLOSED.value:
            job.is_active = True
            job.date_closed = None
        if notes is not None:
            job.notes = notes
        if status_value == JobStatus.APPLIED.value and job.applied_date is None:
            job.applied_date = utcnow()
        allowed_fields = {
            "resume_version",
            "cover_letter_used",
            "referral",
            "contact_person",
            "application_notes",
        }
        for key, value in (application_fields or {}).items():
            if key in allowed_fields:
                setattr(job, key, value)
        self._event(
            session,
            job,
            "STATUS_CHANGED",
            {"from": old_status, "to": status_value},
        )
        return job

    def close_stale_jobs(
        self, session: Session, normalized_companies: list[str], cutoff: datetime
    ) -> list[Job]:
        if not normalized_companies:
            return []
        jobs = session.scalars(
            select(Job).where(
                Job.is_active.is_(True),
                Job.normalized_company.in_(normalized_companies),
                Job.date_last_seen < cutoff,
            )
        ).all()
        now = utcnow()
        for job in jobs:
            previous_status = job.status
            job.is_active = False
            job.date_closed = now
            job.status = JobStatus.CLOSED.value
            self._event(
                session,
                job,
                "CLOSED",
                {"previous_status": previous_status, "last_seen": job.date_last_seen.isoformat()},
            )
        return list(jobs)

    def _merged_candidate(self, job: Job, candidate: JobCandidate) -> JobCandidate:
        def prefer(incoming: Any, existing: Any) -> Any:
            return incoming if incoming not in (None, "", "unknown") else existing

        same_primary_source = candidate.source == job.source
        description = candidate.description
        if not same_primary_source and len(job.description or "") > len(description or ""):
            description = job.description
        date_posted = candidate.date_posted or job.date_posted
        if candidate.date_posted and job.date_posted:
            date_posted = min(candidate.date_posted, job.date_posted)
        return JobCandidate(
            external_job_id=(
                prefer(candidate.external_job_id, job.external_job_id)
                if same_primary_source
                else job.external_job_id
            ),
            title=prefer(candidate.title, job.title),
            company=job.company,
            company_priority=job.company_priority,
            location=prefer(candidate.location, job.location),
            city=prefer(candidate.city, job.city),
            province=prefer(candidate.province, job.province),
            country=prefer(candidate.country, job.country),
            remote_type=prefer(candidate.remote_type, job.remote_type),
            description=description,
            job_url=(prefer(candidate.job_url, job.job_url) if same_primary_source else job.job_url),
            source=job.source,
            source_url=(
                prefer(candidate.source_url, job.source_url)
                if same_primary_source
                else job.source_url
            ),
            date_posted=date_posted,
            employment_type=prefer(candidate.employment_type, job.employment_type),
            experience_level=prefer(candidate.experience_level, job.experience_level),
            salary_min=prefer(candidate.salary_min, job.salary_min),
            salary_max=prefer(candidate.salary_max, job.salary_max),
            salary_currency=prefer(candidate.salary_currency, job.salary_currency),
            raw_data=candidate.raw_data,
        )

    def _candidate_values(self, candidate: JobCandidate) -> dict[str, Any]:
        return {
            "external_job_id": candidate.external_job_id,
            "title": candidate.title,
            "company": candidate.company,
            "normalized_company": candidate.normalized_company,
            "company_priority": candidate.company_priority,
            "location": candidate.location,
            "city": candidate.city,
            "province": candidate.province,
            "country": candidate.country,
            "remote_type": candidate.remote_type,
            "description": candidate.description,
            "job_url": candidate.job_url,
            "canonical_url": candidate.job_url,
            "source": candidate.source,
            "source_url": candidate.source_url,
            "date_posted": candidate.date_posted,
            "employment_type": candidate.employment_type,
            "experience_level": candidate.experience_level,
            "salary_min": candidate.salary_min,
            "salary_max": candidate.salary_max,
            "salary_currency": candidate.salary_currency,
            "dedup_fingerprint": dedup_fingerprint(
                candidate.company, candidate.title, candidate.location
            ),
            "content_hash": candidate.hash,
            "raw_data": json.dumps(candidate.raw_data, default=str, ensure_ascii=False),
        }

    def _score_values(self, score: ScoreResult) -> dict[str, Any]:
        return {
            "category": score.category,
            "matched_keywords": json.dumps(score.matched_keywords, ensure_ascii=False),
            "excluded_keywords": json.dumps(score.excluded_keywords, ensure_ascii=False),
            "match_score": score.score,
            "match_reason": json.dumps(score.explanation, ensure_ascii=False),
        }

    def _record_occurrence(
        self, session: Session, job: Job, candidate: JobCandidate, now: object
    ) -> None:
        conditions = []
        if candidate.external_job_id:
            conditions.append(JobOccurrence.external_job_id == candidate.external_job_id)
        if candidate.job_url:
            conditions.append(JobOccurrence.canonical_url == candidate.job_url)
        occurrence = None
        if conditions:
            occurrence = session.scalar(
                select(JobOccurrence)
                .where(
                    JobOccurrence.job_id == job.internal_id,
                    JobOccurrence.source == candidate.source,
                    or_(*conditions),
                )
                .limit(1)
            )
        if occurrence:
            occurrence.date_last_seen = now
            occurrence.source_url = candidate.source_url
        else:
            session.add(
                JobOccurrence(
                    job_id=job.internal_id,
                    source=candidate.source,
                    normalized_company=candidate.normalized_company,
                    external_job_id=candidate.external_job_id,
                    canonical_url=candidate.job_url,
                    source_url=candidate.source_url,
                    date_first_seen=now,
                    date_last_seen=now,
                )
            )

    def _event(self, session: Session, job: Job, event_type: str, details: dict[str, Any]) -> None:
        session.add(
            JobEvent(
                job_id=job.internal_id,
                event_type=event_type,
                details=json.dumps(details, default=str, ensure_ascii=False),
            )
        )
