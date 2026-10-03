"""Boundary interfaces implemented by later tasks (T004 metrics, T005 analysis, T006 AI, T007
storage and orchestration). Kept free of web framework and LLM SDK types."""

import asyncio
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from pydantic import Field

from app.domain.common import Contract, Scope, Severity, UtcDatetime
from app.domain.detector_config import DetectorConfig
from app.domain.explanation import Explanation, ExplanationInput, ExplanationStatus
from app.domain.findings import DailyTrend, Evidence, Finding, SignalCoverage, TrendSummary
from app.domain.jobs import AnalysisJob, JobError, JobState, StageProgress
from app.domain.metrics import MetricCapability, MetricSeries
from app.domain.report import (
    AnalysisReport,
    AnalysisRequest,
    AnalysisWindows,
    Exclusion,
    SourceInfo,
)


class ProjectActivity(Contract):
    latest: AnalysisJob | None = None
    """Newest finished job."""
    active: AnalysisJob | None = None
    """Queued or running job."""
    report_count: int = 0


class Cancelled(Exception):
    """Raised at a cancellation checkpoint."""


class CancellationToken:
    """Cooperative cancellation checked between queries and stages."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise Cancelled


class ProgressReporter(Protocol):
    async def update(self, progress: StageProgress) -> None: ...


class MappingKind(StrEnum):
    OTEL_SERVICE = "otel_service"
    """target_info.service_instance_id -> cAdvisor container id -> cAdvisor instance (host)."""
    SWARM_SERVICE = "swarm_service"
    """docker_swarm_task_info{state="running"}.node_hostname."""


class EntityMapping(Contract):
    """A service-to-host relationship established by shared label values at one instant."""

    kind: MappingKind
    service: str = Field(description="OTel job label or Swarm service_name.")
    host: str = Field(description="Host alias, equal to node/cAdvisor instance.")
    observed_at: UtcDatetime


class CollectionResult(Contract):
    series: list[MetricSeries]
    exclusions: list[Exclusion]
    mappings: list[EntityMapping] = Field(default_factory=list)


class MetricsSource(Protocol):
    """Read-only, scope-enforcing access to a Prometheus-compatible API (T004)."""

    async def source_info(self) -> SourceInfo: ...

    async def capabilities(
        self, scope: Scope, windows: AnalysisWindows
    ) -> list[MetricCapability]: ...

    async def collect(
        self,
        scope: Scope,
        windows: AnalysisWindows,
        capabilities: Sequence[MetricCapability],
        progress: ProgressReporter,
        cancel: CancellationToken,
    ) -> CollectionResult: ...


class SourceProvider(Protocol):
    """Opens a project's MetricsSource for the duration of one analysis or probe."""

    def open(self, scope: Scope) -> AbstractAsyncContextManager[MetricsSource]: ...


class DetectionResult(Contract):
    findings: list[Finding]
    evidence: list[Evidence]
    trends: list[DailyTrend]
    trend_summary: TrendSummary | None = None
    coverage: list[SignalCoverage]
    exclusions: list[Exclusion]


class Detector(Protocol):
    """Deterministic, pure analysis over collected series (T005)."""

    def detect(
        self,
        request: AnalysisRequest,
        windows: AnalysisWindows,
        capabilities: Sequence[MetricCapability],
        collection: CollectionResult,
        config: DetectorConfig,
    ) -> DetectionResult: ...


class ExplanationError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ExplanationProvider(Protocol):
    """Replaceable AI adapter (T006). Raises ExplanationError on failure."""

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    async def explain(self, explanation_input: ExplanationInput) -> Explanation: ...


class AnalysisService(Protocol):
    """Framework-independent analysis entry point shared by web, future CLI, and schedules (T007).

    Returns a report whose state is completed or partial; raises Cancelled on cancellation.
    """

    async def run(
        self,
        analysis_id: str,
        request: AnalysisRequest,
        progress: ProgressReporter,
        cancel: CancellationToken,
    ) -> AnalysisReport: ...


class ReportRepository(Protocol):
    """Job and report persistence (implemented by app.storage.repository, T007)."""

    async def create_job(self, job: AnalysisJob) -> None: ...

    async def get_job(self, analysis_id: str) -> AnalysisJob | None: ...

    async def find_active(self, request: AnalysisRequest) -> AnalysisJob | None: ...

    async def count_active(self) -> dict[JobState, int]: ...

    async def list_jobs(
        self, project_id: str | None, limit: int, cursor: str | None
    ) -> tuple[list[AnalysisJob], str | None]: ...

    async def project_activity(self, project_ids: Sequence[str]) -> dict[str, ProjectActivity]: ...

    async def mark_running(self, analysis_id: str, at: datetime) -> AnalysisJob | None: ...

    async def update_stage(self, analysis_id: str, progress: StageProgress) -> None: ...

    async def finish_job(
        self,
        analysis_id: str,
        state: JobState,
        error: JobError | None,
        *,
        explanation_status: ExplanationStatus | None = None,
        finding_counts: dict[Severity, int] | None = None,
        daily_episodes: list[int | None] | None = None,
        report_available: bool = False,
    ) -> AnalysisJob: ...

    async def save_report(self, report: AnalysisReport) -> None: ...

    async def get_report(self, analysis_id: str) -> AnalysisReport | None: ...

    async def fail_interrupted(self) -> int:
        """Mark queued/running jobs failed with interrupted_by_restart; return the count."""
        ...

    async def stats(self) -> tuple[int, int]:
        """(report count, database bytes)."""
        ...
