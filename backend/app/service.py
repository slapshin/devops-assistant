"""Framework-independent analysis pipeline: the single entry point for web, CLI, and schedules.

One immutable request (project scope, frozen end time T, detector version/config hash) flows
through the project's metrics source (capability discovery, collection), deterministic
detection, and optional AI explanation.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.ai.providers import explain_findings
from app.domain.common import SOURCE_FAMILIES, Scope
from app.domain.detector_config import DetectorConfig
from app.domain.explanation import ExplanationStatus
from app.domain.interfaces import (
    CancellationToken,
    CollectionResult,
    Detector,
    ExplanationProvider,
    OpenedSource,
    ProgressReporter,
    SourceError,
    SourceProvider,
)
from app.domain.jobs import StageName, StageProgress, StageStatus
from app.domain.metrics import MetricCapability
from app.domain.report import (
    TREND_DAYS,
    AnalysisReport,
    AnalysisRequest,
    AnalysisWindows,
    Exclusion,
    ReportState,
    SourceInfo,
)

log = logging.getLogger("app.service")

PARTIAL_CODES = {
    "query_failed",
    "query_timeout",
    "series_truncated",
    "evidence_dropped",
    "source_unavailable",
}
MAX_REPORT_BYTES = 20 * 1024 * 1024
MAX_EPISODES_PER_DAY_WHEN_OVER_BUDGET = 50
EXPLANATION_STAGE_STATUS = {
    ExplanationStatus.SUCCEEDED: StageStatus.DONE,
    ExplanationStatus.FAILED: StageStatus.FAILED,
}


class AnalysisPipeline:
    def __init__(
        self,
        sources: SourceProvider,
        detector: Detector,
        config: DetectorConfig,
        provider: ExplanationProvider | None,
        unavailable: ExplanationStatus = ExplanationStatus.DISABLED,
        unavailable_reason: str | None = None,
        max_report_bytes: int = MAX_REPORT_BYTES,
    ) -> None:
        self.sources = sources
        self.detector = detector
        self.config = config
        self.provider = provider
        self.unavailable = unavailable
        self.unavailable_reason = unavailable_reason
        self.max_report_bytes = max_report_bytes

    async def run(
        self,
        analysis_id: str,
        request: AnalysisRequest,
        progress: ProgressReporter,
        cancel: CancellationToken,
    ) -> AnalysisReport:
        windows = AnalysisWindows.for_end(request.end_time, self.config.step_seconds)

        async def stage(name: StageName, status: StageStatus, message: str | None = None) -> None:
            now = datetime.now(UTC)
            await progress.update(
                StageProgress(
                    stage=name,
                    status=status,
                    message=message,
                    started_at=now if status is StageStatus.RUNNING else None,
                    finished_at=now if status is not StageStatus.RUNNING else None,
                )
            )

        await stage(StageName.DISCOVERY, StageStatus.RUNNING)
        async with self.sources.open(request.scope) as opened:
            gathered = await _gather(opened, request.scope, windows, progress, cancel)
        cancel.raise_if_cancelled()
        capabilities, collection = gathered.capabilities, gathered.collection

        await stage(StageName.DETECTION, StageStatus.RUNNING)
        result = self.detector.detect(request, windows, capabilities, collection, self.config)
        await stage(StageName.DETECTION, StageStatus.DONE, f"{len(result.findings)} findings")
        await stage(StageName.TRENDS, StageStatus.DONE, f"{TREND_DAYS} daily buckets")
        cancel.raise_if_cancelled()

        await stage(StageName.EXPLANATION, StageStatus.RUNNING)
        explanation = await explain_findings(
            self.provider,
            self.unavailable,
            self.unavailable_reason,
            request.scope,
            windows.latest_day,
            result.findings,
            result.coverage,
        )
        await stage(
            StageName.EXPLANATION,
            EXPLANATION_STAGE_STATUS.get(explanation.status, StageStatus.SKIPPED),
            explanation.reason,
        )
        cancel.raise_if_cancelled()

        exclusions = [*collection.exclusions, *result.exclusions]
        is_partial = any(e.code in PARTIAL_CODES for e in exclusions)
        report = AnalysisReport(
            analysis_id=analysis_id,
            scope=request.scope,
            windows=windows,
            generated_at=datetime.now(UTC),
            detector_version=request.detector_version,
            config_hash=request.config_hash,
            sources=gathered.sources,
            source=gathered.sources[0],
            state=ReportState.PARTIAL if is_partial else ReportState.COMPLETED,
            capabilities=capabilities,
            coverage=result.coverage,
            findings=result.findings,
            trends=result.trends,
            trend_summary=result.trend_summary,
            evidence=result.evidence,
            exclusions=exclusions,
            explanation=explanation,
        )
        return self._fit(report)

    def _fit(self, report: AnalysisReport) -> AnalysisReport:
        """Keep the snapshot within the storage budget, disclosing everything dropped."""
        if self._fits(report):
            return report

        slim = _keep_primary_evidence(report)
        if not self._fits(slim):
            slim = _truncate_daily_episodes(slim)
        return AnalysisReport.model_validate(slim.model_dump())

    def _fits(self, report: AnalysisReport) -> bool:
        return len(report.model_dump_json()) <= self.max_report_bytes


@dataclass
class _Gathered:
    sources: list[SourceInfo] = field(default_factory=list)
    capabilities: list[MetricCapability] = field(default_factory=list)
    collection: CollectionResult = field(
        default_factory=lambda: CollectionResult(series=[], exclusions=[])
    )


async def _gather(
    opened: list[OpenedSource],
    scope: Scope,
    windows: AnalysisWindows,
    progress: ProgressReporter,
    cancel: CancellationToken,
) -> _Gathered:
    """Discover and collect every source; a failing source is disclosed, not fatal, unless
    every source fails (then its error fails the job as with a single source)."""
    result = _Gathered()
    failures: list[tuple[OpenedSource, SourceError]] = []
    discovered: list[tuple[OpenedSource, list[MetricCapability]]] = []
    for item in opened:
        try:
            info = await item.source.source_info()
            caps = await item.source.capabilities(scope, windows)
        except SourceError as exc:
            failures.append((item, exc))
            continue
        result.sources.append(info.model_copy(update={"kind": item.kind}))
        caps = [c.model_copy(update={"source": item.kind}) for c in caps]
        result.capabilities += caps
        discovered.append((item, caps))
        cancel.raise_if_cancelled()
    await progress.update(
        StageProgress(
            stage=StageName.DISCOVERY,
            status=StageStatus.DONE,
            finished_at=datetime.now(UTC),
            message=", ".join(f"{item.kind.value}: unavailable" for item, _ in failures) or None,
        )
    )

    series, exclusions, mappings = [], [], []
    for item, caps in discovered:
        try:
            collected = await item.source.collect(scope, windows, caps, progress, cancel)
        except SourceError as exc:
            failures.append((item, exc))
            continue
        series += collected.series
        exclusions += collected.exclusions
        mappings += collected.mappings
        cancel.raise_if_cancelled()

    if failures and len(failures) == len(opened):
        raise failures[0][1]
    for item, error in failures:
        log.warning("%s source failed: %s", item.kind.value, error.message)
        exclusions += [
            Exclusion(
                code="source_unavailable",
                message=f"{item.kind.value} source failed ({error.kind.value}): {error.message}",
                family=family,
            )
            for family in SOURCE_FAMILIES[item.kind]
        ]
    result.collection = CollectionResult(series=series, exclusions=exclusions, mappings=mappings)
    return result


def _keep_primary_evidence(report: AnalysisReport) -> AnalysisReport:
    """Keep only each finding's first evidence series; mark the report partial."""
    primary = {f.evidence_ids[0] for f in report.findings}
    dropped = [e for e in report.evidence if e.evidence_id not in primary]
    findings = [f.model_copy(update={"evidence_ids": f.evidence_ids[:1]}) for f in report.findings]

    exclusions = list(report.exclusions)
    if dropped:
        exclusions.append(
            Exclusion(
                code="evidence_dropped",
                message=f"{len(dropped)} supporting evidence series (operands, p99) dropped to "
                "respect the report size budget.",
            )
        )

    return report.model_copy(
        update={
            "findings": findings,
            "evidence": [e for e in report.evidence if e.evidence_id in primary],
            "exclusions": exclusions,
            "state": ReportState.PARTIAL,
        }
    )


def _truncate_daily_episodes(report: AnalysisReport) -> AnalysisReport:
    limit = MAX_EPISODES_PER_DAY_WHEN_OVER_BUDGET
    trends = [t.model_copy(update={"episodes": t.episodes[:limit]}) for t in report.trends]
    exclusions = [
        *report.exclusions,
        Exclusion(
            code="evidence_dropped",
            message=f"Daily episode lists truncated to {limit} per day (counts are complete).",
        ),
    ]
    return report.model_copy(update={"trends": trends, "exclusions": exclusions})
