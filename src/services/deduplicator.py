from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz.fuzz import ratio
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.models.job import Job, JobCandidate, JobOccurrence
from src.utils.text import normalize_text


@dataclass(slots=True)
class DeduplicationMatch:
    job: Job | None
    signal: str | None = None
    similarity: float | None = None


class Deduplicator:
    def __init__(self, similarity_threshold: float = 93):
        self.similarity_threshold = similarity_threshold

    def find(self, session: Session, candidate: JobCandidate) -> DeduplicationMatch:
        company = candidate.normalized_company
        if candidate.external_job_id:
            occurrence = session.scalar(
                select(JobOccurrence)
                .where(
                    JobOccurrence.normalized_company == company,
                    JobOccurrence.external_job_id == candidate.external_job_id,
                )
                .limit(1)
            )
            if occurrence:
                return DeduplicationMatch(occurrence.job, "company + external_job_id")
            job = session.scalar(
                select(Job)
                .where(
                    Job.normalized_company == company,
                    Job.external_job_id == candidate.external_job_id,
                )
                .limit(1)
            )
            if job:
                return DeduplicationMatch(job, "company + external_job_id")

        if candidate.job_url:
            occurrence = session.scalar(
                select(JobOccurrence)
                .where(JobOccurrence.canonical_url == candidate.job_url)
                .limit(1)
            )
            if occurrence:
                return DeduplicationMatch(occurrence.job, "canonical job URL")
            job = session.scalar(
                select(Job).where(Job.canonical_url == candidate.job_url).limit(1)
            )
            if job:
                return DeduplicationMatch(job, "canonical job URL")

        job = session.scalar(
            select(Job).where(Job.dedup_fingerprint == candidate.fingerprint).limit(1)
        )
        if job:
            return DeduplicationMatch(job, "company + normalized title + location")

        possible = session.scalars(
            select(Job).where(Job.normalized_company == company).order_by(Job.date_last_seen.desc())
        ).all()
        title = normalize_text(candidate.title)
        location = normalize_text(candidate.location)
        best_job: Job | None = None
        best_score = 0.0
        for existing in possible:
            title_score = ratio(title, normalize_text(existing.title))
            existing_location = normalize_text(existing.location)
            location_score = (
                ratio(location, existing_location) if location and existing_location else 100.0
            )
            combined = title_score * 0.8 + location_score * 0.2
            if title_score >= 92 and location_score >= 85 and combined > best_score:
                best_job = existing
                best_score = combined
        if best_job and best_score >= self.similarity_threshold:
            return DeduplicationMatch(best_job, "similarity fallback", best_score)
        return DeduplicationMatch(None)
