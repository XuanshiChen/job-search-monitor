from __future__ import annotations

import hashlib
import imaplib
import logging
import os
import ssl
from dataclasses import dataclass
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Iterable

from src.config.loader import AppConfig, ConfigurationError
from src.utils.dates import utcnow


@dataclass(slots=True)
class EmailImportResult:
    imported: int = 0
    skipped: int = 0


@dataclass(slots=True)
class EmailSyncResult(EmailImportResult):
    enabled: bool = True
    examined: int = 0


class LinkedInEmailSyncService:
    def __init__(self, config: AppConfig):
        self.config = config
        self.settings = config.settings.get("linkedin_email", {})
        self.directory = config.linkedin_email_directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger("services.linkedin_email_sync")

    def import_files(self, paths: Iterable[str | Path]) -> EmailImportResult:
        result = EmailImportResult()
        for value in paths:
            path = Path(value).expanduser().resolve()
            if not path.is_file():
                raise FileNotFoundError(f"Email file not found: {path}")
            raw = path.read_bytes()
            self._validate_email(raw, path.name)
            if self._save(raw, prefix="manual"):
                result.imported += 1
            else:
                result.skipped += 1
        return result

    def sync_imap(self) -> EmailSyncResult:
        if not bool(self.settings.get("imap_enabled", False)):
            return EmailSyncResult(enabled=False)

        host = os.getenv("LINKEDIN_IMAP_HOST", "").strip()
        username = os.getenv("LINKEDIN_IMAP_USERNAME", "").strip()
        password = os.getenv("LINKEDIN_IMAP_PASSWORD", "")
        port = int(os.getenv("LINKEDIN_IMAP_PORT", "993"))
        if not host or not username or not password:
            raise ConfigurationError(
                "IMAP sync is enabled but LINKEDIN_IMAP_HOST, "
                "LINKEDIN_IMAP_USERNAME, or LINKEDIN_IMAP_PASSWORD is missing from .env"
            )

        mailbox = str(self.settings.get("mailbox", "INBOX"))
        sender_value = self.settings.get("sender_contains", "linkedin.com")
        if isinstance(sender_value, str):
            sender_filters = [sender_value.casefold()] if sender_value.strip() else []
        else:
            sender_filters = [
                str(value).strip().casefold() for value in sender_value if str(value).strip()
            ]
        subject_contains = str(self.settings.get("subject_contains", "")).casefold()
        lookback_days = max(1, int(self.settings.get("lookback_days", 14)))
        maximum = max(1, int(self.settings.get("max_messages_per_sync", 100)))
        mark_as_read = bool(self.settings.get("mark_as_read", False))
        since = (utcnow() - timedelta(days=lookback_days)).strftime("%d-%b-%Y")
        result = EmailSyncResult(enabled=True)

        client: imaplib.IMAP4_SSL | None = None
        try:
            client = imaplib.IMAP4_SSL(
                host=host,
                port=port,
                ssl_context=ssl.create_default_context(),
            )
            client.login(username, password)
            status, _ = client.select(mailbox, readonly=not mark_as_read)
            if status != "OK":
                raise RuntimeError(f"Unable to select IMAP mailbox {mailbox!r}")
            identifiers_found: set[bytes] = set()
            searches = sender_filters or [""]
            for sender_filter in searches:
                criteria = f'(SINCE {since})'
                if sender_filter:
                    criteria = f'(SINCE {since} FROM "{sender_filter}")'
                status, data = client.uid("search", None, criteria)
                if status != "OK":
                    raise RuntimeError(f"IMAP search failed for {sender_filter or 'all senders'}")
                identifiers_found.update((data[0] or b"").split())
            identifiers = sorted(
                identifiers_found,
                key=lambda value: (
                    0,
                    int(value),
                )
                if value.isdigit()
                else (1, value),
            )[-maximum:]
            for uid in identifiers:
                status, payload = client.uid("fetch", uid, "(BODY.PEEK[])")
                if status != "OK":
                    self.logger.warning(
                        "linkedin_email_fetch_failed",
                        extra={"imap_uid": uid.decode(errors="replace")},
                    )
                    continue
                raw = _extract_message_bytes(payload)
                if not raw:
                    continue
                result.examined += 1
                message = BytesParser(policy=policy.default).parsebytes(raw, headersonly=True)
                sender = str(message.get("From", "")).casefold()
                subject = str(message.get("Subject", "")).casefold()
                if sender_filters and not any(value in sender for value in sender_filters):
                    result.skipped += 1
                    continue
                if subject_contains and subject_contains not in subject:
                    result.skipped += 1
                    continue
                if self._save(raw, prefix="imap"):
                    result.imported += 1
                else:
                    result.skipped += 1
                if mark_as_read:
                    client.uid("store", uid, "+FLAGS", "(\\Seen)")
            self.logger.info(
                "linkedin_email_sync_finished",
                extra={
                    "examined": result.examined,
                    "imported": result.imported,
                    "skipped": result.skipped,
                },
            )
            return result
        except imaplib.IMAP4.error as exc:
            raise RuntimeError(f"IMAP synchronization failed: {exc}") from exc
        finally:
            if client is not None:
                try:
                    client.logout()
                except (imaplib.IMAP4.error, OSError):
                    pass

    def _save(self, raw: bytes, *, prefix: str) -> bool:
        digest = hashlib.sha256(raw).hexdigest()
        target = self.directory / f"{prefix}-{digest[:24]}.eml"
        if target.exists():
            return False
        temporary = self.directory / f".{prefix}-{digest[:24]}.tmp"
        temporary.write_bytes(raw)
        temporary.replace(target)
        return True

    @staticmethod
    def _validate_email(raw: bytes, name: str) -> None:
        message = BytesParser(policy=policy.default).parsebytes(raw, headersonly=True)
        if not message.get("From") and not message.get("Subject"):
            raise ValueError(f"File does not look like an email message: {name}")


def _extract_message_bytes(payload: object) -> bytes:
    if not isinstance(payload, list):
        return b""
    for item in payload:
        if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes):
            return item[1]
    return b""


JobAlertEmailSyncService = LinkedInEmailSyncService
