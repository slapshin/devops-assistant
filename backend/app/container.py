"""Wiring of long-lived application services (shared by the web app and future CLI)."""

from dataclasses import dataclass, field

from app.domain.detector_config import DetectorConfig
from app.domain.projects import ConnectionTest
from app.jobs import JobRunner
from app.notifications.notifier import ReportNotifier
from app.scheduler import ReportScheduler
from app.settings import Settings
from app.sources.factory import ProjectSources
from app.storage.channels import SqliteChannelRepository
from app.storage.credentials import SqliteCredentialRepository
from app.storage.projects import SqliteProjectRepository
from app.storage.repository import SqliteReportRepository


@dataclass
class Services:
    settings: Settings
    config: DetectorConfig
    sources: ProjectSources
    repo: SqliteReportRepository
    runner: JobRunner
    projects: SqliteProjectRepository
    scheduler: ReportScheduler
    credentials: SqliteCredentialRepository
    channels: SqliteChannelRepository
    notifier: ReportNotifier
    health_cache: dict[str, tuple[float, str, list[ConnectionTest]]] = field(default_factory=dict)
    """project_id -> (monotonic time, project updated_at, last connection test)."""
