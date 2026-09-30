"""Provider-independent explanation contracts."""

from enum import StrEnum

from pydantic import Field

from app.domain.common import (
    ConfidenceLevel,
    Contract,
    Entity,
    Scope,
    Severity,
    SignalFamily,
    TimeRange,
    UtcDatetime,
)


class ExplanationStatus(StrEnum):
    DISABLED = "disabled"
    NOT_CONFIGURED = "not_configured"
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED_NO_FINDINGS = "skipped_no_findings"


class Likelihood(StrEnum):
    PLAUSIBLE = "plausible"
    POSSIBLE = "possible"
    SPECULATIVE = "speculative"


class Hypothesis(Contract):
    """Always presented as unverified."""

    text: str = Field(min_length=1)
    finding_ids: list[str] = Field(min_length=1)
    likelihood: Likelihood


class InvestigationStep(Contract):
    text: str = Field(min_length=1)
    finding_ids: list[str] = Field(default_factory=list)


class Explanation(Contract):
    summary: str = Field(min_length=1)
    hypotheses: list[Hypothesis]
    investigation_steps: list[InvestigationStep]
    uncertainty: str
    provider: str
    model: str
    generated_at: UtcDatetime


class ExplanationResult(Contract):
    status: ExplanationStatus
    explanation: Explanation | None = None
    reason: str | None = Field(default=None, description="Why failed/not_configured/disabled.")
    validation_notes: list[str] = Field(
        default_factory=list, description="E.g. dropped items referencing unknown finding IDs."
    )


class FindingDigest(Contract):
    """Bounded, credential-free finding summary sent to a provider (no raw series)."""

    finding_id: str
    title: str
    family: SignalFamily
    entity: Entity
    severity: Severity
    confidence: ConfidenceLevel
    start: UtcDatetime
    end: UtcDatetime
    observed: str = Field(description="Human-readable value with unit.")
    expected: str | None
    related_finding_ids: list[str] = Field(default_factory=list)
    host: str | None = Field(default=None, description="Verified host, if any.")


class ExplanationInput(Contract):
    scope: Scope
    latest_day: TimeRange
    coverage_summary: str
    findings: list[FindingDigest] = Field(max_length=20)
