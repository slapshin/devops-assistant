"""Analysis request, windows, and the saved report snapshot."""

from datetime import timedelta
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from app.domain.common import (
    REPORT_SCHEMA_VERSION,
    STEP_SECONDS,
    Contract,
    Reason,
    Scope,
    SignalFamily,
    SourceKind,
    TimeRange,
    UtcDatetime,
)
from app.domain.explanation import ExplanationResult
from app.domain.findings import DailyTrend, Evidence, Finding, SignalCoverage, TrendSummary
from app.domain.metrics import MetricCapability

TREND_DAYS = 14


class AnalysisRequest(Contract):
    """Framework-independent input to AnalysisService. end_time is already frozen and aligned."""

    scope: Scope
    end_time: UtcDatetime
    detector_version: str
    config_hash: str = Field(pattern=r"^[0-9a-f]{12}$")

    @model_validator(mode="after")
    def _aligned(self) -> Self:
        if int(self.end_time.timestamp()) % STEP_SECONDS or self.end_time.microsecond:
            raise ValueError(
                f"end_time {self.end_time.isoformat()} must be aligned to {STEP_SECONDS}s steps"
            )
        return self


class AnalysisWindows(Contract):
    end_time: UtcDatetime
    latest_day: TimeRange
    trend: TimeRange
    step_seconds: int

    @classmethod
    def for_end(cls, end_time: UtcDatetime, step_seconds: int = STEP_SECONDS) -> AnalysisWindows:
        day = timedelta(hours=24)
        return cls(
            end_time=end_time,
            latest_day=TimeRange(start=end_time - day, end=end_time),
            trend=TimeRange(start=end_time - TREND_DAYS * day, end=end_time),
            step_seconds=step_seconds,
        )


class SourceInfo(Contract):
    """Identity of a data source without credentials."""

    kind: SourceKind = SourceKind.PROMETHEUS
    base_url: str = Field(description="Scheme, host, port, and path prefix; never userinfo.")
    backend: str | None = Field(default=None, description="E.g. 'victoriametrics' when detected.")
    version: str | None = None


class ReportState(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"


class Exclusion(Reason):
    """Something omitted from the report, always disclosed (codes in docs/contracts.md)."""

    family: SignalFamily | None = None
    finding_id: str | None = None


class AnalysisReport(Contract):
    schema_version: Literal["2.0", "2.1"] = REPORT_SCHEMA_VERSION
    analysis_id: str
    scope: Scope
    windows: AnalysisWindows
    generated_at: UtcDatetime
    detector_version: str
    config_hash: str
    sources: list[SourceInfo] = Field(
        default_factory=list, description="Every source analysed (2.1); empty in 2.0 reports."
    )
    source: SourceInfo | None = Field(
        default=None, description="Deprecated: the first source; use sources."
    )
    state: ReportState
    capabilities: list[MetricCapability]
    coverage: list[SignalCoverage]
    findings: list[Finding]
    trends: list[DailyTrend] = Field(min_length=TREND_DAYS, max_length=TREND_DAYS)
    trend_summary: TrendSummary | None = None
    evidence: list[Evidence]
    exclusions: list[Exclusion]
    explanation: ExplanationResult

    @property
    def all_sources(self) -> list[SourceInfo]:
        """Sources of 2.1 reports, or the single source of a 2.0 report."""
        return self.sources or ([self.source] if self.source else [])

    @model_validator(mode="after")
    def _references_resolve(self) -> Self:
        finding_ids = {f.finding_id for f in self.findings}
        evidence_ids = {e.evidence_id for e in self.evidence}
        for f in self.findings:
            if missing := set(f.evidence_ids) - evidence_ids:
                raise ValueError(f"{f.finding_id} references unknown evidence {sorted(missing)}")
            if missing := set(f.related_finding_ids) - finding_ids:
                raise ValueError(f"{f.finding_id} references unknown findings {sorted(missing)}")
        for e in self.evidence:
            if e.finding_id not in finding_ids:
                raise ValueError(f"{e.evidence_id} references unknown finding {e.finding_id}")
        bucket_indexes = [t.bucket_index for t in self.trends]
        if bucket_indexes != list(range(TREND_DAYS)):
            raise ValueError(
                f"trends must be ordered by bucket_index 0..{TREND_DAYS - 1}, got {bucket_indexes}"
            )
        if self.explanation.explanation is not None:
            ex = self.explanation.explanation
            referenced = [h.finding_ids for h in ex.hypotheses] + [
                s.finding_ids for s in ex.investigation_steps
            ]
            for ids in referenced:
                if missing := set(ids) - finding_ids:
                    raise ValueError(f"explanation references unknown findings {sorted(missing)}")
        return self
