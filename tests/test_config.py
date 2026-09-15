from __future__ import annotations

from src.config.loader import load_config


EXPECTED_OFFICIAL_COMPANIES = {
    "Hydro One",
    "IESO",
    "Bruce Power",
    "Ontario Power Generation",
    "Siemens",
    "SEL",
    "GE Vernova",
    "Toronto Hydro",
    "BBA Consultants",
    "AltaLink",
    "Hatch",
    "Tetra Tech",
    "Burns & McDonnell",
    "Eaton",
    "Sargent & Lundy",
    "Black & Veatch",
    "ABB",
    "Schneider Electric",
    "Hitachi Energy",
    "Stantec",
}


def test_default_strategy_enables_only_twenty_official_company_sources() -> None:
    config = load_config()
    enabled = config.enabled_companies()

    assert {company.name for company in enabled} == EXPECTED_OFFICIAL_COMPANIES
    assert all(
        company.source_type not in {"linkedin_email", "job_alert_email"}
        for company in enabled
    )
    assert config.settings["linkedin_email"]["imap_enabled"] is False
