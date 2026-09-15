from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigurationError(ValueError):
    """Raised when user-editable configuration is invalid."""


@dataclass(slots=True)
class CompanyConfig:
    name: str
    priority: str
    careers_url: str = ""
    search_url: str = ""
    source_type: str = "auto"
    enabled: bool = True
    aliases: list[str] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AppConfig:
    root: Path
    settings: dict[str, Any]
    keywords: dict[str, Any]
    companies: list[CompanyConfig]

    @property
    def database_path(self) -> Path:
        value = self.settings.get("database_path", "data/jobs.db")
        return self._resolve_path(value)

    @property
    def log_path(self) -> Path:
        value = self.settings.get("log_path", "logs/job_monitor.log")
        return self._resolve_path(value)

    @property
    def report_directory(self) -> Path:
        value = self.settings.get("report_directory", "reports")
        return self._resolve_path(value)

    @property
    def linkedin_email_directory(self) -> Path:
        value = self.settings.get("linkedin_email", {}).get(
            "directory", "data/linkedin_emails"
        )
        return self._resolve_path(str(value))

    def _resolve_path(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else self.root / path

    def enabled_companies(self, company_name: str | None = None) -> list[CompanyConfig]:
        companies = [company for company in self.companies if company.enabled]
        if company_name:
            needle = company_name.casefold()
            companies = [company for company in companies if company.name.casefold() == needle]
            if not companies:
                raise ConfigurationError(f"No enabled company named {company_name!r} was found")
        rank = {"critical": 0, "tier_1": 1, "tier_2": 2, "tier_3": 3}
        return sorted(companies, key=lambda item: (rank.get(item.priority, 99), item.name.casefold()))

    def resolve_company(self, name: str) -> CompanyConfig | None:
        normalized = name.strip().casefold()
        for company in self.companies:
            if any(
                item.strip().casefold() == normalized
                for item in (company.name, *company.aliases)
            ):
                return company
        return None

    def linkedin_email_source(self) -> CompanyConfig:
        for company in self.companies:
            if company.enabled and company.source_type in {"linkedin_email", "job_alert_email"}:
                return company
        raise ConfigurationError(
            "No enabled job-alert email source is configured in config/companies.yaml"
        )

    def job_alert_email_source(self) -> CompanyConfig:
        return self.linkedin_email_source()


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigurationError(f"Missing configuration file: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = yaml.safe_load(handle) or {}
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError(f"Expected a YAML mapping in {path}")
    return value


def load_config(root: str | Path | None = None) -> AppConfig:
    project_root = Path(root).resolve() if root else PROJECT_ROOT
    config_dir = project_root / "config"
    settings = _read_yaml(config_dir / "settings.yaml")
    keywords = _read_yaml(config_dir / "keywords.yaml")
    company_data = _read_yaml(config_dir / "companies.yaml")

    companies: list[CompanyConfig] = []
    supported_tiers = ("critical", "tier_1", "tier_2", "tier_3")
    for tier in supported_tiers:
        entries = company_data.get("companies", {}).get(tier, []) or []
        if not isinstance(entries, list):
            raise ConfigurationError(f"companies.{tier} must be a list")
        for entry in entries:
            if not isinstance(entry, dict) or not str(entry.get("name", "")).strip():
                raise ConfigurationError(f"Each companies.{tier} item requires a name")
            companies.append(
                CompanyConfig(
                    name=str(entry["name"]).strip(),
                    priority=tier,
                    careers_url=str(entry.get("careers_url", "")).strip(),
                    search_url=str(entry.get("search_url", "")).strip(),
                    source_type=str(entry.get("source_type", "auto")).strip().lower(),
                    enabled=bool(entry.get("enabled", True)),
                    aliases=[str(item) for item in entry.get("aliases", [])],
                    options=dict(entry.get("options", {})),
                )
            )

    if not companies:
        raise ConfigurationError("Configure at least one company in config/companies.yaml")
    return AppConfig(root=project_root, settings=settings, keywords=keywords, companies=companies)
