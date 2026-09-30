"""Findings, evidence, coverage, and daily trend contracts."""

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from app.domain.common import (
    ConfidenceLevel,
    Contract,
    Entity,
    Labels,
    Ratio,
    Reason,
    Severity,
    SignalFamily,
    TimeRange,
    Unit,
    UtcDatetime,
)
from app.domain.metrics import CapabilityStatus, MetricSeries


class DetectionMethod(StrEnum):
    RELATIVE = "relative"
    """Robust median/MAD baseline from preceding days."""
    ABSOLUTE = "absolute"
    """Provisional diagnostic heuristic threshold; never an SLO."""


class BaselineMode(StrEnum):
    WHOLE_BASELINE = "whole_baseline"
    TIME_OF_DAY = "time_of_day"


class SignalStatus(StrEnum):
    ANOMALOUS = "anomalous"
    NO_ANOMALY = "no_anomaly"
    INSUFFICIENT_DATA = "insufficient_data"
    UNSUPPORTED = "unsupported"
    SOURCE_ERROR = "source_error"
    NOT_EVALUATED = "not_evaluated"


class FindingState(StrEnum):
    ONGOING = "ongoing"
    """Episode still anomalous at the end of the analysed window."""
    RESOLVED = "resolved"


class Recurrence(StrEnum):
    NEW = "new"
    """No episode for this entity and signal on the preceding 13 days."""
    REPEATED = "repeated"
    """Episodes on 1-2 preceding days."""
    RECURRING = "recurring"
    """Episodes on at least 3 preceding days."""


class ObservedValue(Contract):
    value: float
    unit: Unit


class ExpectedValue(Contract):
    median: float
    lower: float
    upper: float
    unit: Unit


class Finding(Contract):
    finding_id: str = Field(pattern=r"^fnd_[0-9a-f]{16}$")
    detector: str
    detector_version: str
    method: DetectionMethod
    family: SignalFamily
    signal: str
    title: str
    entity: Entity
    start: UtcDatetime
    end: UtcDatetime
    peak_at: UtcDatetime
    duration_seconds: int = Field(gt=0)
    severity: Severity
    severity_points: int = Field(ge=0)
    confidence: ConfidenceLevel
    confidence_reasons: list[Reason] = Field(
        description="Every factor that lowered confidence; empty when high."
    )
    observed: ObservedValue = Field(description="Peak observed value in the episode.")
    expected: ExpectedValue | None = Field(description="Null for absolute-only findings.")
    peak_score: float | None = Field(description="Robust z-score; null for absolute checks.")
    threshold: float | None = Field(description="Heuristic threshold for absolute checks.")
    baseline_days: int | None = Field(ge=0)
    baseline_mode: BaselineMode | None
    evidence_ids: list[str] = Field(min_length=1)
    related_finding_ids: list[str] = Field(
        default_factory=list, description="Findings sharing identity labels only."
    )
    attributes: Labels = Field(
        default_factory=dict, description="Descriptive labels, e.g. error_type; not identity."
    )
    state: FindingState = FindingState.RESOLVED
    recurrence: Recurrence = Recurrence.NEW
    prior_episode_days: int = Field(default=0, ge=0, le=13)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if not self.start <= self.peak_at < self.end:
            raise ValueError("peak_at must lie in [start, end)")
        if self.method is DetectionMethod.RELATIVE and (
            self.expected is None or self.peak_score is None
        ):
            raise ValueError("relative findings need expected and peak_score")
        if self.method is DetectionMethod.ABSOLUTE and self.threshold is None:
            raise ValueError("absolute findings need threshold")
        return self


class Evidence(Contract):
    """Chart data for a finding, persisted so a report reopens without the source."""

    evidence_id: str = Field(pattern=r"^evd_[0-9a-f]{16}$")
    finding_id: str
    series: MetricSeries
    expected: list[float | None] | None = None
    lower: list[float | None] | None = None
    upper: list[float | None] | None = None
    threshold: float | None = None
    threshold_label: str | None = None

    @model_validator(mode="after")
    def _aligned(self) -> Self:
        n = len(self.series.values)
        for name in ("expected", "lower", "upper"):
            band = getattr(self, name)
            if band is not None and len(band) != n:
                raise ValueError(f"{name} length {len(band)} != series length {n}")
        return self


class SignalCoverage(Contract):
    """One row of the report coverage table."""

    family: SignalFamily
    status: SignalStatus
    capability: CapabilityStatus
    evaluated_series: int = Field(ge=0)
    total_series: int = Field(ge=0)
    baseline_days: int | None = Field(default=None, ge=0)
    reasons: list[Reason] = Field(default_factory=list)


class EpisodeSummary(Contract):
    episode_id: str = Field(pattern=r"^eps_[0-9a-f]{16}$")
    finding_id: str | None = Field(description="Set only for latest-day episodes with evidence.")
    entity: Entity
    family: SignalFamily
    signal: str
    start: UtcDatetime
    end: UtcDatetime
    severity: Severity
    peak_observed: float
    expected_median: float | None
    unit: Unit


class TrendDirection(StrEnum):
    WORSENING = "worsening"
    IMPROVING = "improving"
    STABLE = "stable"
    INCONCLUSIVE = "inconclusive"


class TrendSummary(Contract):
    """Direction of the 14-day trend: last 7 buckets vs the 7 before, by anomalous share."""

    direction: TrendDirection
    confidence: ConfidenceLevel
    recent_share: Ratio | None
    previous_share: Ratio | None
    recent_episodes: int = Field(ge=0)
    previous_episodes: int = Field(ge=0)
    reason: str


class TrendBucketStatus(StrEnum):
    OK = "ok"
    INSUFFICIENT_BASELINE = "insufficient_baseline"
    INSUFFICIENT_DATA = "insufficient_data"
    SOURCE_ERROR = "source_error"


class DailyTrend(Contract):
    """One 24-hour bucket; bucket_index 0 is the latest day [T-24h, T)."""

    bucket_index: int = Field(ge=0, le=13)
    window: TimeRange
    status: TrendBucketStatus
    episode_count: int = Field(ge=0)
    anomalous_minutes: int = Field(ge=0)
    peak_severity: Severity | None
    affected_entities: list[str] = Field(description="Entity keys.")
    observed_entity_minutes: int = Field(ge=0, description="Eligible denominator.")
    anomalous_share: Ratio | None = Field(description="Null when nothing was observed.")
    baseline_days_used: int = Field(ge=0, le=14)
    coverage: Ratio
    episodes: list[EpisodeSummary]
