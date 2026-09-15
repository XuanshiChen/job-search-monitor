from datetime import UTC, datetime

from src.models.job import JobCandidate
from src.sources.normalization import infer_location_parts, infer_remote_type
from src.utils.dates import parse_datetime
from src.utils.text import canonicalize_url, normalize_text


def test_job_candidate_normalizes_core_identity() -> None:
    candidate = JobCandidate(
        title="  Protection   Engineer ",
        company=" Example   Utility ",
        location="Toronto, ON, Canada",
        job_url="HTTPS://EXAMPLE.COM/jobs/123/?utm_source=alert#apply",
        source="test",
    )

    assert candidate.title == "Protection Engineer"
    assert candidate.company == "Example Utility"
    assert candidate.job_url == "https://example.com/jobs/123"
    assert len(candidate.fingerprint) == 64


def test_location_and_remote_normalization() -> None:
    assert infer_location_parts("Burnaby, BC, Canada") == (
        "Burnaby",
        "British Columbia",
        "Canada",
    )
    assert infer_remote_type("Hybrid — Toronto") == "hybrid"
    assert infer_remote_type("Work from home in Canada") == "remote"


def test_tracking_parameters_are_removed_without_losing_identity_parameters() -> None:
    url = canonicalize_url(
        "https://example.com/job?id=42&gh_jid=99&utm_campaign=test&source=email"
    )
    assert url == "https://example.com/job?id=42&gh_jid=99"
    assert normalize_text("IEC 61850 / GOOSE") == "iec 61850 goose"


def test_millisecond_epoch_date_is_parsed() -> None:
    parsed = parse_datetime(1_725_192_000_000)
    assert parsed is not None
    assert parsed.year == 2024


def test_country_codes_are_normalized() -> None:
    assert infer_location_parts("Toronto, ON, CA", {"country": "CA"}) == (
        "Toronto",
        "Ontario",
        "Canada",
    )


def test_foreign_words_do_not_match_province_abbreviations() -> None:
    assert infer_location_parts(
        "Plzen, Plzeňský kraj, Czechia; Brno, Jihomoravsky, Czechia"
    ) == ("Plzen", "", "")


def test_job_candidate_normalizes_aware_dates_to_naive_utc() -> None:
    value = JobCandidate(
        title="Electrical Engineer",
        company="Example Utility",
        location="Toronto, ON, Canada",
        job_url="https://example.com/jobs/1",
        source="test",
        date_posted=datetime(2026, 9, 1, 12, 30, tzinfo=UTC),
    )
    assert value.date_posted == datetime(2026, 9, 1, 12, 30)
    assert value.date_posted.tzinfo is None
