from __future__ import annotations

from sqlalchemy import func, select

from src.matching.scorer import JobScorer
from src.models.job import Job, JobCandidate, JobEvent, JobStatus
from src.services.deduplicator import Deduplicator
from src.services.repository import JobRepository


def make_candidate(**overrides: object) -> JobCandidate:
    values = {
        "external_job_id": "REQ-101",
        "title": "Protection Engineer EIT",
        "company": "Grid Utility",
        "company_priority": "tier_1",
        "location": "Toronto, Ontario, Canada",
        "province": "Ontario",
        "country": "Canada",
        "description": "Entry level relay protection and IEC 61850 work.",
        "job_url": "https://jobs.example.test/REQ-101",
        "source": "test",
    }
    values.update(overrides)
    return JobCandidate(**values)


def repository(app_config) -> JobRepository:
    return JobRepository(Deduplicator(93), JobScorer(app_config))


def test_deduplicates_by_external_id_and_updates_last_seen(database, app_config) -> None:
    repo = repository(app_config)
    with database.session() as session:
        first = repo.upsert(session, make_candidate())
        job_id = first.job.internal_id
    with database.session() as session:
        second = repo.upsert(
            session,
            make_candidate(job_url="https://mirror.example.test/different", source="aggregator"),
        )
        count = session.scalar(select(func.count(Job.internal_id)))

    assert first.action == "NEW"
    assert second.action == "DUPLICATE"
    assert second.job.internal_id == job_id
    assert second.dedup_signal == "company + external_job_id"
    assert count == 1


def test_deduplication_hierarchy_uses_canonical_url_then_fingerprint(database, app_config) -> None:
    repo = repository(app_config)
    with database.session() as session:
        original = repo.upsert(session, make_candidate(external_job_id=None))
    with database.session() as session:
        url_match = repo.upsert(
            session,
            make_candidate(
                external_job_id="OTHER-ID",
                job_url="https://jobs.example.test/REQ-101?utm_source=other",
            ),
        )
    assert url_match.job.internal_id == original.job.internal_id
    assert url_match.dedup_signal == "canonical job URL"

    with database.session() as session:
        fingerprint_match = repo.upsert(
            session,
            make_candidate(external_job_id=None, job_url="", source="second"),
        )
    assert fingerprint_match.job.internal_id == original.job.internal_id
    assert fingerprint_match.dedup_signal == "company + normalized title + location"


def test_meaningful_change_creates_update_event(database, app_config) -> None:
    repo = repository(app_config)
    with database.session() as session:
        new = repo.upsert(session, make_candidate())
        job_id = new.job.internal_id
    with database.session() as session:
        updated = repo.upsert(
            session,
            make_candidate(description="Expanded IEC 61850, GOOSE and relay testing description."),
        )
    with database.session() as session:
        event_types = list(
            session.scalars(
                select(JobEvent.event_type).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
            )
        )

    assert updated.action == "UPDATED"
    assert event_types == ["NEW", "UPDATED"]


def test_status_update_records_applied_date_only_when_explicit(database, app_config) -> None:
    repo = repository(app_config)
    with database.session() as session:
        job_id = repo.upsert(session, make_candidate()).job.internal_id
    with database.session() as session:
        reviewed = repo.update_status(session, job_id, JobStatus.REVIEW, notes="Review tonight")
        assert reviewed.applied_date is None
    with database.session() as session:
        applied = repo.update_status(
            session,
            job_id,
            JobStatus.APPLIED,
            application_fields={"resume_version": "power-v2", "cover_letter_used": True},
        )
        applied_date = applied.applied_date
    with database.session() as session:
        repo.update_status(session, job_id, JobStatus.INTERVIEW)
        job = session.get(Job, job_id)

    assert applied_date is not None
    assert job.applied_date == applied_date
    assert job.resume_version == "power-v2"
    assert job.cover_letter_used is True


def test_manual_closed_status_updates_active_state_and_can_reopen(database, app_config) -> None:
    repo = repository(app_config)
    with database.session() as session:
        job_id = repo.upsert(session, make_candidate()).job.internal_id
    with database.session() as session:
        closed = repo.update_status(session, job_id, JobStatus.CLOSED)
        assert closed.is_active is False
        assert closed.date_closed is not None
    with database.session() as session:
        reopened = repo.update_status(session, job_id, JobStatus.REVIEW)
        assert reopened.is_active is True
        assert reopened.date_closed is None
