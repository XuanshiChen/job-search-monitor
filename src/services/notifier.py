from __future__ import annotations

import logging
import smtplib
from abc import ABC, abstractmethod
from email.message import EmailMessage
from os import getenv

from src.config.loader import AppConfig
from src.models.job import Job


class Notifier(ABC):
    @abstractmethod
    def notify_job(self, job: Job) -> bool:
        """Notify about one high-quality newly discovered job."""


class DesktopNotifier(Notifier):
    def __init__(self) -> None:
        self.logger = logging.getLogger("services.notifier.desktop")

    def notify_job(self, job: Job) -> bool:
        try:
            from plyer import notification

            notification.notify(
                title=f"New job match: {job.match_score}/100",
                message=f"{job.title}\n{job.company} — {job.location}",
                app_name="Job Search Monitor",
                timeout=10,
            )
            return True
        except Exception:
            self.logger.warning(
                "desktop_notification_failed",
                extra={"job_id": job.internal_id},
                exc_info=True,
            )
            return False


class EmailNotifier(Notifier):
    def __init__(self) -> None:
        self.logger = logging.getLogger("services.notifier.email")

    def notify_job(self, job: Job) -> bool:
        host = getenv("SMTP_HOST", "")
        username = getenv("SMTP_USERNAME", "")
        password = getenv("SMTP_PASSWORD", "")
        sender = getenv("SMTP_FROM", "")
        recipient = getenv("SMTP_TO", "")
        if not all((host, sender, recipient)):
            self.logger.warning("email_notification_skipped_missing_configuration")
            return False
        message = EmailMessage()
        message["Subject"] = f"Job match {job.match_score}/100: {job.title}"
        message["From"] = sender
        message["To"] = recipient
        message.set_content(
            f"{job.title}\n{job.company}\n{job.location}\nScore: {job.match_score}/100\n\n{job.job_url}"
        )
        port = int(getenv("SMTP_PORT", "587"))
        use_tls = getenv("SMTP_USE_TLS", "true").casefold() == "true"
        try:
            with smtplib.SMTP(host, port, timeout=20) as client:
                if use_tls:
                    client.starttls()
                if username:
                    client.login(username, password)
                client.send_message(message)
            return True
        except Exception:
            self.logger.warning("email_notification_failed", exc_info=True)
            return False


class CompositeNotifier(Notifier):
    def __init__(self, notifiers: list[Notifier]):
        self.notifiers = notifiers

    def notify_job(self, job: Job) -> bool:
        results = [notifier.notify_job(job) for notifier in self.notifiers]
        return any(results)


def build_notifier(config: AppConfig) -> Notifier:
    settings = config.settings.get("notifications", {})
    notifiers: list[Notifier] = []
    if settings.get("desktop_enabled", True):
        notifiers.append(DesktopNotifier())
    if settings.get("email_enabled", False):
        notifiers.append(EmailNotifier())
    return CompositeNotifier(notifiers)
