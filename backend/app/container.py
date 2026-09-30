"""Wiring of long-lived application services (shared by the web app and future CLI)."""

from dataclasses import dataclass

from app.domain.detector_config import DetectorConfig
from app.domain.interfaces import MetricsSource
from app.jobs import JobRunner
from app.metrics.client import PrometheusClient
from app.settings import Settings
from app.storage.repository import SqliteReportRepository


@dataclass
class Services:
    settings: Settings
    config: DetectorConfig
    source: MetricsSource
    repo: SqliteReportRepository
    runner: JobRunner
    client: PrometheusClient | None
