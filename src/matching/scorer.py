from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.config.loader import AppConfig
from src.matching.keywords import classify_role, find_phrases
from src.models.job import JobCandidate
from src.utils.text import normalize_text


@dataclass(slots=True)
class ScoreResult:
    score: int
    category: str
    matched_keywords: list[str] = field(default_factory=list)
    excluded_keywords: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    hard_excluded: bool = False

    @property
    def explanation(self) -> list[str]:
        return [*self.reasons, *(f"Potential concern: {item}" for item in self.concerns)]


class JobScorer:
    def __init__(self, config: AppConfig):
        self.config = config
        self.settings = config.settings
        self.keywords = config.keywords

    def score(self, job: JobCandidate) -> ScoreResult:
        score = 0
        reasons: list[str] = []
        concerns: list[str] = []
        excluded: list[str] = []
        scoring = self.settings.get("scoring", {})

        company_points = int(
            scoring.get("company_priority", {}).get(job.company_priority, 0)
        )
        score += company_points
        if company_points:
            label = "Critical target company" if job.company_priority == "critical" else (
                f"{job.company_priority.replace('_', ' ').title()} target company"
            )
            reasons.append(f"{label} (+{company_points})")

        role_key, category = classify_role(
            job.title, self.keywords.get("role_categories", {})
        )
        category = category or "Other"
        role_points = int(scoring.get("role", {}).get(role_key, 0))
        score += role_points
        if role_points:
            reasons.append(f"{category} role match (+{role_points})")

        combined = " ".join((job.title, job.experience_level, job.description))
        entry_matches = find_phrases(
            combined, self.keywords.get("entry_level_keywords", [])
        )
        if entry_matches:
            entry_points = int(scoring.get("entry_level_bonus", 20))
            score += entry_points
            reasons.append(
                f"Entry-level friendly: {', '.join(_unique(entry_matches)[:3])} (+{entry_points})"
            )
        else:
            years = _required_years(combined)
            preferred_max = int(
                self.settings.get("experience_level", {}).get("maximum_preferred_years", 3)
            )
            if years is not None and years <= 2:
                score += 15
                reasons.append(f"Requests about {years:g} years of experience (+15)")
            elif years is not None and years <= preferred_max:
                score += 5
                reasons.append(f"Experience requirement is within preferred range (+5)")
            elif years is not None and years > preferred_max:
                penalty = min(25, 5 + int((years - preferred_max) * 3))
                score -= penalty
                concerns.append(f"Requests about {years:g} years of experience (-{penalty})")

        technical_matches: list[str] = []
        technical_points = 0
        normalized_combined = normalize_text(combined)
        for keyword, points in self.keywords.get("technical_keywords", {}).items():
            if normalize_text(keyword) in normalized_combined:
                technical_matches.append(str(keyword))
                technical_points += int(points)
        cap = int(scoring.get("technical_keyword_cap", 24))
        technical_points = min(technical_points, cap)
        score += technical_points
        if technical_matches:
            reasons.append(
                f"Technical matches: {', '.join(technical_matches[:6])} (+{technical_points})"
            )

        location_points, location_reason = self.location_score(job)
        score += location_points
        if location_reason:
            (reasons if location_points >= 0 else concerns).append(location_reason)

        remote_points = int(
            self.settings.get("remote_preferences", {}).get(job.remote_type, 0)
        )
        score += remote_points
        if remote_points:
            reasons.append(f"{job.remote_type.title()} work arrangement (+{remote_points})")

        normalized_title = normalize_text(job.title)
        normalized_description = normalize_text(job.description)
        hard_matches = [
            term
            for term in self.keywords.get("hard_exclusion_title_keywords", [])
            if normalize_text(term) in normalized_title
        ]
        hard_excluded = bool(hard_matches)
        if hard_matches:
            excluded.extend(hard_matches)
            concerns.append(f"Hard-excluded title: {', '.join(hard_matches)}")
            score = 0

        for term, configured_penalty in self.keywords.get("soft_penalty_keywords", {}).items():
            normalized_term = normalize_text(term)
            penalty = int(configured_penalty)
            if normalized_term in normalized_title:
                score -= penalty
                excluded.append(str(term))
                concerns.append(f"{term!s} appears in title (-{penalty})")
            elif normalized_term in normalized_description:
                description_penalty = max(1, round(penalty * 0.35))
                score -= description_penalty
                excluded.append(str(term))
                concerns.append(
                    f"{term!s} appears in description (-{description_penalty})"
                )

        return ScoreResult(
            score=max(0, min(100, round(score))),
            category=category,
            matched_keywords=_unique([*entry_matches, *technical_matches]),
            excluded_keywords=_unique(excluded),
            reasons=reasons,
            concerns=concerns,
            hard_excluded=hard_excluded,
        )

    def company_priority_score(self, priority: str) -> int:
        return int(
            self.settings.get("scoring", {}).get("company_priority", {}).get(priority, 0)
        )

    def location_score(self, job: JobCandidate) -> tuple[int, str]:
        locations = self.settings.get("preferred_locations", {})
        weights = self.settings.get("scoring", {}).get("location", {})
        haystack = normalize_text(" ".join((job.location, job.city, job.province, job.country)))
        for tier in ("tier_1", "tier_2", "tier_3"):
            for configured in locations.get(tier, []):
                if normalize_text(str(configured)) in haystack:
                    points = int(weights.get(tier, 0))
                    city_bonus = self._preferred_city_bonus(job)
                    total = points + city_bonus
                    label = str(configured)
                    reason = f"{label} location (+{points})"
                    if city_bonus:
                        reason += f" and preferred city (+{city_bonus})"
                    return total, reason

        if "canada" in haystack and bool(locations.get("canada_wide", True)):
            points = int(weights.get("tier_3", 4))
            return points, f"Canadian location (+{points})"
        if job.country and normalize_text(job.country) != "canada":
            penalty = int(weights.get("international_penalty", -15))
            if not bool(locations.get("allow_international", False)):
                return penalty, f"International location ({penalty})"
        return 0, ""

    def _preferred_city_bonus(self, job: JobCandidate) -> int:
        cities = self.settings.get("preferred_locations", {}).get("preferred_cities", [])
        location = normalize_text(" ".join((job.location, job.city)))
        if any(normalize_text(str(city)) in location for city in cities):
            return int(
                self.settings.get("scoring", {})
                .get("location", {})
                .get("preferred_city_bonus", 0)
            )
        return 0


def _required_years(text: str) -> float | None:
    normalized = normalize_text(text)
    patterns = (
        r"(\d+(?:\.\d+)?)\s*\+?\s*years?",
        r"(\d+(?:\.\d+)?)\s*(?:to|-)\s*\d+(?:\.\d+)?\s*years?",
    )
    matches: list[float] = []
    for pattern in patterns:
        matches.extend(float(item) for item in re.findall(pattern, normalized))
    return max(matches) if matches else None


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
