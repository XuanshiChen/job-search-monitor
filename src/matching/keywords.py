from __future__ import annotations

from collections.abc import Iterable

from src.utils.text import normalize_text


def contains_phrase(text: str, phrase: str) -> bool:
    return normalize_text(phrase) in normalize_text(text)


def find_phrases(text: str, phrases: Iterable[str]) -> list[str]:
    normalized = normalize_text(text)
    return [phrase for phrase in phrases if normalize_text(phrase) in normalized]


def classify_role(title: str, role_categories: dict[str, list[str]]) -> tuple[str, str | None]:
    normalized_title = normalize_text(title)
    labels = {
        "highest": "Protection & Control",
        "power_systems": "Power Systems / Substation",
        "medium": "Electrical / Adjacent Engineering",
        "adjacent": "Adjacent Engineering",
    }
    for key in ("highest", "power_systems", "medium", "adjacent"):
        for phrase in role_categories.get(key, []):
            if normalize_text(phrase) in normalized_title:
                return key, labels[key]
    return "other", None
