from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from src.config.loader import AppConfig
from src.database.database import Database
from src.services.email_sync import EmailSyncResult, LinkedInEmailSyncService
from src.services.scanner import ScanProgress, ScanResult, Scanner
from src.utils.dates import utcnow


@dataclass(slots=True)
class DashboardRefreshResult:
    started_at: datetime
    finished_at: datetime
    email: EmailSyncResult | None = None
    scan: ScanResult | None = None
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DashboardRefreshProgress:
    phase: str
    message: str
    percent: int


DashboardProgressCallback = Callable[[DashboardRefreshProgress], None]


class DashboardRefreshService:
    """Synchronize alert email and scan jobs without coupling the work to Streamlit."""

    def __init__(self, config: AppConfig, database: Database):
        self.config = config
        self.database = database
        self.logger = logging.getLogger("services.dashboard_refresh")

    def run(
        self,
        progress: DashboardProgressCallback | None = None,
    ) -> DashboardRefreshResult:
        started = utcnow()
        result = DashboardRefreshResult(started_at=started, finished_at=started)
        email_enabled = bool(
            self.config.settings.get("linkedin_email", {}).get("imap_enabled", False)
        )
        if email_enabled:
            self._emit(progress, "email", "第 1/3 步 · 正在同步 Gmail 职位提醒", 2)
            try:
                result.email = LinkedInEmailSyncService(self.config).sync_imap()
            except Exception as exc:
                result.errors.append(f"Email sync: {exc}")
                self.logger.error("dashboard_email_sync_failed", exc_info=True)
        else:
            result.email = EmailSyncResult(enabled=False)
            self._emit(
                progress,
                "official_only",
                "官网监控模式 · 邮件和聚合招聘网站已关闭",
                5,
            )

        scan_step = "第 2/3 步" if email_enabled else "第 1/2 步"
        final_step = "第 3/3 步" if email_enabled else "第 2/2 步"
        self._emit(progress, "scan", f"{scan_step} · 正在准备公司官网来源", 10)

        def scan_progress(update: ScanProgress) -> None:
            if update.phase == "finalizing":
                percent = 96
                message = f"{final_step} · 正在保存更新、检查关闭职位并发送通知"
            else:
                completed = update.current / update.total if update.total else 1.0
                percent = 10 + round(completed * 85)
                if update.phase == "fetching":
                    message = (
                        f"{scan_step} · 正在并行检索 {update.total} 个公司官网"
                        f"（最多同时 {update.workers} 个）"
                    )
                else:
                    message = (
                        f"{scan_step} · 已完成 {update.company} "
                        f"（{update.current}/{update.total}）"
                    )
            self._emit(progress, "scan", message, percent)

        try:
            scanner = Scanner(self.config, self.database)
            result.scan = (
                scanner.scan(progress=scan_progress) if progress is not None else scanner.scan()
            )
        except Exception as exc:
            result.errors.append(f"Job scan: {exc}")
            self.logger.error("dashboard_scan_failed", exc_info=True)
        self._emit(progress, "complete", f"{final_step} · 刷新完成", 100)
        result.finished_at = utcnow()
        return result

    def _emit(
        self,
        callback: DashboardProgressCallback | None,
        phase: str,
        message: str,
        percent: int,
    ) -> None:
        if callback is None:
            return
        try:
            callback(
                DashboardRefreshProgress(
                    phase=phase,
                    message=message,
                    percent=max(0, min(100, percent)),
                )
            )
        except Exception:
            self.logger.warning("dashboard_progress_callback_failed", exc_info=True)
