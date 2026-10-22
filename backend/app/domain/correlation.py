"""Related metrics (T023): what else changed around a finding, with its evidence kept apart.

Each comparison pairs a finding (the anchor) with one other collected series (the candidate)
and records three independent kinds of evidence: the resource association, the similarity of
their unusual changes, and their timing. None of it establishes causation, and none of it
changes a finding's severity or confidence. The numerical policy is in docs/DECISIONS.md §5.
"""

from enum import StrEnum
from typing import Final, Self

from pydantic import Field, model_validator

from app.domain.common import (
    Contract,
    Entity,
    Ratio,
    Reason,
    SignalFamily,
    SourceKind,
    TimeRange,
    Unit,
    UtcDatetime,
)
from app.domain.metrics import MetricSeries

MAX_RELATED_PER_FINDING: Final = 5
"""Comparisons kept per finding; further qualifying ones are counted as omitted."""


class ResourceAssociation(StrEnum):
    """How the two series' resources are connected, from identity labels or verified mappings
    only; never from matching names across sources."""

    SAME_ENTITY = "same_entity"
    SAME_SERVICE = "same_service"
    """Routes and runtime processes of one ``job``, the parts of one reverse proxy, or a Sentry
    project and its error kinds."""
    SAME_DATABASE_SERVER = "same_database_server"
    SAME_STORAGE_CLUSTER = "same_storage_cluster"
    SHARED_HOST = "shared_host"
    """A host from identity labels or a verified OTel/Swarm mapping."""
    UNKNOWN = "unknown"
    """No verified connection; the series only share the project."""


class ComparisonMethod(StrEnum):
    DEVIATION_CORRELATION = "deviation_correlation"
    """Pearson correlation of the two series' baseline deviations (robust z, clipped), over a
    bounded range of lags."""
    INTERVAL_OVERLAP = "interval_overlap"
    """Overlap of unusual intervals (events, flagged steps, large deviations), for event series
    and series without an adequate baseline."""


class RelatedDirection(StrEnum):
    SAME = "same"
    """The deviations move together (positive coefficient)."""
    OPPOSITE = "opposite"
    """One rises while the other falls (negative coefficient)."""


class ComparisonTiming(StrEnum):
    SIMULTANEOUS = "simultaneous"
    """Best agreement without a shift, or no shift agrees clearly better."""
    CANDIDATE_LEADS = "candidate_leads"
    """The candidate's changes come earlier (positive lag)."""
    CANDIDATE_LAGS = "candidate_lags"
    """The candidate's changes come later (negative lag)."""
    INCONCLUSIVE = "inconclusive"
    """Shifts in both directions agree about as well: no ordering can be claimed."""


class SharedInput(StrEnum):
    NONE = "none"
    """No known shared input."""
    POSSIBLE = "possible"
    """An alert rule or custom rule whose query is not analysed, or near-identical values: the
    agreement may be mechanical and is not independent confirmation."""


class RelatedSeries(Contract):
    """One side of a comparison: which series, from which source, on which resource."""

    source: SourceKind
    family: SignalFamily
    signal: str = Field(min_length=1)
    label: str = Field(min_length=1, description="What the signal measures, e.g. 'CPU'.")
    entity: Entity
    unit: Unit
    finding_id: str | None = Field(
        description="The series' own latest-day finding overlapping the window, if any."
    )
    chart_id: str = Field(pattern=r"^rch_[0-9a-f]{16}$")


class RelatedMetric(Contract):
    """One retained comparison between a finding and another collected series."""

    comparison_id: str = Field(pattern=r"^cmp_[0-9a-f]{16}$")
    finding_id: str = Field(description="The anchor finding.")
    rank: int = Field(ge=1, le=MAX_RELATED_PER_FINDING)
    anchor: RelatedSeries
    candidate: RelatedSeries
    selection: str = Field(min_length=1, description="Why this comparison was kept.")
    association: ResourceAssociation
    association_basis: str = Field(min_length=1, description="The labels or mapping behind it.")
    shared_input: SharedInput = SharedInput.NONE
    method: ComparisonMethod
    window: TimeRange = Field(description="Steps compared, the same for every candidate.")
    step_seconds: int = Field(gt=0)
    coefficient: float | None = Field(
        ge=-1.0,
        le=1.0,
        description="Signed Pearson coefficient of the deviations at `lag_seconds` (0 unless "
        "a lead or lag is estimated); null for interval overlap. Not a probability.",
    )
    direction: RelatedDirection | None
    timing: ComparisonTiming
    lag_seconds: int | None = Field(
        description="Estimated shift; positive when the candidate leads. Null unless timing is "
        "candidate_leads or candidate_lags."
    )
    onset_lead_seconds: int | None = Field(
        description="Observed difference of change onsets, positive when the candidate's "
        "unusual change began first; null when it has no onset near the finding."
    )
    candidate_onset: UtcDatetime | None
    overlap_steps: int = Field(
        ge=0, description="Steps where both series are unusual (within the overlap slack)."
    )
    paired_samples: int = Field(ge=0, description="Jointly observed steps at the reported lag.")
    paired_coverage: Ratio = Field(description="paired_samples / steps in the window.")
    longest_gap_seconds: int = Field(ge=0, description="Longest run of unpaired steps.")
    reasons: list[Reason] = Field(
        default_factory=list, description="Data-quality limits, e.g. gaps, clipping."
    )


class RelatedState(StrEnum):
    EVALUATED = "evaluated"
    """Candidates were compared; `retained` may be 0."""
    INSUFFICIENT_DATA = "insufficient_data"
    """The finding's own series cannot be compared (see reasons)."""
    SKIPPED_BUDGET = "skipped_budget"
    """The run's comparison budget was used up by more severe findings."""
    DROPPED_BUDGET = "dropped_budget"
    """Comparisons were computed but dropped to respect the report size budget."""


class UnavailableCount(Contract):
    code: str = Field(min_length=1)
    count: int = Field(ge=1)


class RelatedEvaluation(Contract):
    """What happened when looking for related metrics of one finding."""

    finding_id: str
    state: RelatedState
    window: TimeRange | None = None
    candidates: int = Field(
        ge=0, description="Series with an unusual change in the window (eligible)."
    )
    evaluated: int = Field(ge=0)
    retained: int = Field(ge=0, le=MAX_RELATED_PER_FINDING)
    omitted: int = Field(ge=0, description="Qualifying comparisons beyond the kept limit.")
    not_evaluated: int = Field(ge=0, description="Eligible candidates over the per-finding cap.")
    shared_input: int = Field(
        ge=0, description="Candidates excluded because they share a known input (operands)."
    )
    unavailable: list[UnavailableCount] = Field(
        default_factory=list, description="Candidates whose comparison was unavailable, by code."
    )
    reasons: list[Reason] = Field(default_factory=list)


class RelatedChart(Contract):
    """Chart data of one side of a comparison over its window, stored once per series and
    window so a report reopens without the source."""

    chart_id: str = Field(pattern=r"^rch_[0-9a-f]{16}$")
    series: MetricSeries
    expected: list[float | None] | None = None
    lower: list[float | None] | None = None
    upper: list[float | None] | None = None
    deviation: list[float | None] | None = Field(
        default=None, description="Robust z (clipped); null without an adequate baseline."
    )

    @model_validator(mode="after")
    def _aligned(self) -> Self:
        n = len(self.series.values)
        for name in ("expected", "lower", "upper", "deviation"):
            band = getattr(self, name)
            if band is not None and len(band) != n:
                raise ValueError(f"{self.chart_id}: {name} length {len(band)} != {n}")
        return self


class RelatedPolicy(Contract):
    """The numerical policy the comparisons used (also in config_hash)."""

    method: str
    window_pad_seconds: int = Field(ge=0)
    max_window_seconds: int = Field(gt=0)
    max_lag_seconds: int = Field(ge=0)
    change_z: float = Field(gt=0)
    min_coefficient: float = Field(gt=0, le=1)
    max_per_finding: int = Field(ge=1, le=MAX_RELATED_PER_FINDING)


class RelatedMetrics(Contract):
    """Related metrics of every latest-day finding (2.9)."""

    policy: RelatedPolicy
    evaluations: list[RelatedEvaluation]
    comparisons: list[RelatedMetric]
    charts: list[RelatedChart]

    @model_validator(mode="after")
    def _references_resolve(self) -> Self:
        charts = {c.chart_id for c in self.charts}
        evaluated = {e.finding_id for e in self.evaluations}
        for c in self.comparisons:
            if missing := {c.anchor.chart_id, c.candidate.chart_id} - charts:
                raise ValueError(f"{c.comparison_id} references unknown charts {sorted(missing)}")
            if c.finding_id not in evaluated:
                raise ValueError(f"{c.comparison_id} has no evaluation for {c.finding_id}")
        return self
