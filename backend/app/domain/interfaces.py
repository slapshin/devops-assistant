"""Boundary interfaces implemented by later tasks (T004 metrics, T005 analysis, T006 AI, T007
storage and orchestration). Kept free of web framework and LLM SDK types."""

import asyncio
from collections.abc import Sequence
from typing import Protocol

from app.domain.common import Contract, Scope
from app.domain.detector_config import DetectorConfig
from app.domain.explanation import Explanation, ExplanationInput
from app.domain.findings import DailyTrend, Evidence, Finding, SignalCoverage
from app.domain.jobs import AnalysisJob, JobError, JobState, StageProgress
from app.domain.metrics import MetricCapability, MetricSeries
from app.domain.report import (
    AnalysisReport,
    AnalysisRequest,
    AnalysisWindows,
    Exclusion,
    SourceInfo,
)


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


class CollectionResult(Contract):
    series: list[MetricSeries]
    exclusions: list[Exclusion]


class MetricsSource(Protocol):
    """Read-only, scope-enforcing access to a Prometheus-compatible API (T004)."""

    async def source_info(self) -> SourceInfo: ...

    async def list_projects(self) -> list[str]: ...

    async def list_envs(self, project: str) -> list[str]: ...

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


class DetectionResult(Contract):
    findings: list[Finding]
    evidence: list[Evidence]
    trends: list[DailyTrend]
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
    """SQLite-backed job and report persistence (T007)."""

    async def create_job(self, job: AnalysisJob) -> None: ...

    async def get_job(self, analysis_id: str) -> AnalysisJob | None: ...

    async def find_active(self, request: AnalysisRequest) -> AnalysisJob | None: ...

    async def list_jobs(
        self, scope: Scope | None, limit: int, cursor: str | None
    ) -> tuple[list[AnalysisJob], str | None]: ...

    async def update_stage(self, analysis_id: str, progress: StageProgress) -> None: ...

    async def finish_job(
        self, analysis_id: str, state: JobState, error: JobError | None
    ) -> AnalysisJob: ...

    async def save_report(self, report: AnalysisReport) -> None: ...

    async def get_report(self, analysis_id: str) -> AnalysisReport | None: ...

    async def fail_interrupted(self) -> int:
        """Mark queued/running jobs failed with interrupted_by_restart; return the count."""
        ...
