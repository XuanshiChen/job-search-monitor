from __future__ import annotations

import hashlib
import re
from html import unescape
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup


_TRACKING_PARAMETERS = {
    "source",
    "src",
    "ref",
    "referrer",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(BeautifulSoup(value, "html.parser").get_text(" ").split())


def normalize_text(value: str | None) -> str:
    text = unescape(value or "").casefold()
    text = re.sub(r"[^a-z0-9+#]+", " ", text)
    return " ".join(text.split())


def canonicalize_url(value: str | None) -> str:
    if not value:
        return ""
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return value.strip()
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if key.casefold() not in _TRACKING_PARAMETERS and not key.casefold().startswith("utm_")
    ]
    path = re.sub(r"/+", "/", parts.path).rstrip("/") or "/"
    return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), path, urlencode(query), ""))


def dedup_fingerprint(company: str, title: str, location: str) -> str:
    material = "|".join(
        (normalize_text(company), normalize_text(title), normalize_text(location))
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def content_hash(*values: object) -> str:
    material = "\x1f".join(normalize_text(str(value or "")) for value in values)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-.")
    return cleaned or "report"
