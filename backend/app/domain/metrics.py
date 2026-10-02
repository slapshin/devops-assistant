"""Metric series and capability contracts."""

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from app.domain.common import (
    Contract,
    Entity,
    Labels,
    Ratio,
    SignalFamily,
    Unit,
    UtcDatetime,
)


class CapabilityStatus(StrEnum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    UNVERIFIED = "unverified"
    """Assumed from supplied examples; not observed in the live source (T003)."""


class MetricSeries(Contract):
    """One scoped series on a regular grid: values[i] is at start + i * step_seconds.

    `None` is a gap (no sample), never zero.
    """

    series_id: str = Field(pattern=r"^ser_[0-9a-f]{16}$")
    family: SignalFamily
    signal: str = Field(min_length=1, description="Derived signal, e.g. 'cpu_utilization'.")
    entity: Entity
    labels: Labels = Field(description="All retained labels of the result series.")
    unit: Unit
    query: str = Field(min_length=1, description="Exact PromQL/MetricsQL sent to the source.")
    step_seconds: int = Field(gt=0)
    start: UtcDatetime
    values: list[float | None]
    coverage: Ratio = Field(description="Share of steps with a sample.")

    @model_validator(mode="after")
    def _coverage_matches_values(self) -> Self:
        if self.values:
            observed = sum(v is not None for v in self.values) / len(self.values)
            if abs(observed - self.coverage) > 1e-6:
                raise ValueError(
                    f"{self.series_id}: coverage {self.coverage} != observed share {observed:.6f}"
                )
        elif self.coverage != 0.0:
            raise ValueError(f"{self.series_id}: empty series must have coverage 0")
        return self


class MetricCapability(Contract):
    """Whether a signal family can be analysed for a scope, and why (not)."""

    family: SignalFamily
    signal: str
    status: CapabilityStatus
    verified: bool = Field(description="True only when observed in the live source.")
    required_metrics: list[str]
    required_labels: list[str]
    observed_metrics: list[str] = Field(default_factory=list)
    reason: str | None = None
    history_days: float | None = Field(
        default=None, ge=0, description="Observed history for the scope; not proof of retention."
    )
