from __future__ import annotations

import re
from typing import Any

from src.utils.text import normalize_text


PROVINCES = {
    "ab": "Alberta",
    "alberta": "Alberta",
    "bc": "British Columbia",
    "british columbia": "British Columbia",
    "mb": "Manitoba",
    "manitoba": "Manitoba",
    "nb": "New Brunswick",
    "new brunswick": "New Brunswick",
    "nl": "Newfoundland and Labrador",
    "newfoundland and labrador": "Newfoundland and Labrador",
    "ns": "Nova Scotia",
    "nova scotia": "Nova Scotia",
    "nt": "Northwest Territories",
    "northwest territories": "Northwest Territories",
    "nu": "Nunavut",
    "nunavut": "Nunavut",
    "on": "Ontario",
    "ontario": "Ontario",
    "pe": "Prince Edward Island",
    "prince edward island": "Prince Edward Island",
    "qc": "Quebec",
    "quebec": "Quebec",
    "québec": "Quebec",
    "sk": "Saskatchewan",
    "saskatchewan": "Saskatchewan",
    "yt": "Yukon",
    "yukon": "Yukon",
}

COUNTRIES = {
    "ca": "Canada",
    "can": "Canada",
    "canada": "Canada",
    "us": "United States",
    "usa": "United States",
    "united states": "United States",
    "united states of america": "United States",
}


def infer_location_parts(location: str, data: dict[str, Any] | None = None) -> tuple[str, str, str]:
    source = data or {}
    city = str(source.get("city") or source.get("addressLocality") or "").strip()
    province = str(source.get("province") or source.get("region") or source.get("addressRegion") or "").strip()
    country = str(source.get("country") or source.get("addressCountry") or "").strip()

    parts = [item.strip() for item in re.split(r"[,|/;]", location) if item.strip()]
    normalized_location = normalize_text(location)
    # Province abbreviations must be standalone location components. Matching a
    # two-letter code anywhere in the normalized text can turn foreign words
    # such as "Plzeňský" into a false Saskatchewan (SK) match.
    location_tokens = {
        token
        for part in parts
        for token in re.split(r"[.]+", normalize_text(part))
        if token
    }
    if not province:
        for key, canonical in PROVINCES.items():
            normalized_key = normalize_text(key)
            if (
                len(normalized_key) <= 2
                and normalized_key in location_tokens
            ) or (
                len(normalized_key) > 2
                and re.search(rf"\b{re.escape(normalized_key)}\b", normalized_location)
            ):
                province = canonical
                break
    else:
        province = PROVINCES.get(normalize_text(province), province)
    if country:
        country = COUNTRIES.get(normalize_text(country), country)
    if not country and (province or "canada" in normalized_location):
        country = "Canada"
    if not city and parts:
        first = parts[0]
        if normalize_text(first) not in PROVINCES and normalize_text(first) not in {"canada", "remote"}:
            city = first
    return city, province, country


def infer_remote_type(*values: str) -> str:
    text = normalize_text(" ".join(values))
    if "hybrid" in text:
        return "hybrid"
    if any(term in text for term in ("remote", "work from home", "telecommute")):
        return "remote"
    if any(term in text for term in ("on site", "onsite", "in office")):
        return "on-site"
    return "unknown"


def location_string(value: Any) -> str:
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, dict):
        address = value.get("address", value)
        if isinstance(address, dict):
            values = [
                address.get("addressLocality"),
                address.get("addressRegion"),
                address.get("addressCountry"),
            ]
            return ", ".join(str(item) for item in values if item)
    if isinstance(value, list):
        return "; ".join(filter(None, (location_string(item) for item in value)))
    return ""
