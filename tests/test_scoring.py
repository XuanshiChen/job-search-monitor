from src.matching.scorer import JobScorer
from src.models.job import JobCandidate


def candidate(**overrides: object) -> JobCandidate:
    values = {
        "title": "Protection & Control Engineer-in-Training",
        "company": "Target Utility",
        "company_priority": "tier_1",
        "location": "Toronto, Ontario, Canada",
        "city": "Toronto",
        "province": "Ontario",
        "country": "Canada",
        "remote_type": "hybrid",
        "description": "IEC 61850 GOOSE, SEL relay testing, Omicron and substation protection.",
        "job_url": "https://example.test/job/1",
        "source": "test",
        "experience_level": "New graduate EIT",
    }
    values.update(overrides)
    return JobCandidate(**values)


def test_high_match_is_transparent_and_capped(app_config) -> None:
    result = JobScorer(app_config).score(candidate())

    assert result.score == 100
    assert result.category == "Protection & Control"
    assert "iec 61850" in result.matched_keywords
    assert any("Tier 1 target company" in reason for reason in result.reasons)
    assert any("Entry-level friendly" in reason for reason in result.reasons)


def test_senior_title_is_penalized_but_not_hard_rejected(app_config) -> None:
    scorer = JobScorer(app_config)
    base = scorer.score(
        candidate(title="Electrical Engineer", description="Power utility design", experience_level="")
    )
    senior = scorer.score(
        candidate(
            title="Senior Electrical Engineer",
            description="Power utility design and five years experience",
            experience_level="Senior",
        )
    )

    assert senior.score < base.score
    assert not senior.hard_excluded
    assert "senior" in senior.excluded_keywords


def test_company_priority_calculation(app_config) -> None:
    scorer = JobScorer(app_config)
    assert scorer.company_priority_score("critical") == 25
    assert scorer.company_priority_score("tier_1") == 20
    assert scorer.company_priority_score("tier_2") == 12
    assert scorer.company_priority_score("tier_3") == 5


def test_location_matching_is_config_driven(app_config) -> None:
    scorer = JobScorer(app_config)
    ontario_points, reason = scorer.location_score(candidate())
    bc_points, _ = scorer.location_score(
        candidate(location="Victoria, British Columbia, Canada", city="Victoria", province="British Columbia")
    )
    international_points, _ = scorer.location_score(
        candidate(location="Seattle, Washington, USA", city="Seattle", province="Washington", country="USA")
    )

    assert ontario_points == 15
    assert "Ontario" in reason
    assert bc_points == 8
    assert international_points == -15
