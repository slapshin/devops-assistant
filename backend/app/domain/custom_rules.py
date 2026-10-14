"""Custom rules: user-written PromQL analysed like catalog signals (T020).

A project's Prometheus source holds up to ``MAX_CUSTOM_RULES`` rules. Each one is collected
with the project's matchers injected into every selector (app/domain/user_promql.py) and
analysed against its own baseline, a fixed threshold, or both; any positive value can also be
treated as an event.
"""

import re
from enum import StrEnum
from typing import Annotated, Self

from pydantic import AfterValidator, Field, field_validator, model_validator

from app.domain.common import LABEL_NAME, Contract, Entity, Severity, Unit, UtcDatetime
from app.domain.user_promql import MAX_QUERY_CHARS, UserQueryError, scope_template

MAX_CUSTOM_RULES = 20
MAX_IDENTITY_LABELS = 10
CUSTOM_SIGNAL_PREFIX = "custom_"
"""Signal and rule name of a custom rule: the prefix plus its slug."""
_SLUG = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


class CustomRuleMode(StrEnum):
    ANOMALY = "anomaly"
    """Deviation from the rule's own robust baseline (preceding days)."""
    THRESHOLD = "threshold"
    """Fixed threshold held for at least ``min_duration_minutes``; no baseline."""
    BOTH = "both"
    """Baseline deviation or the threshold, whichever applies."""
    EVENT = "event"
    """Any value of at least 0.5 in a step (e.g. failed jobs per step) is a finding."""


class ChangeDirection(StrEnum):
    UP = "up"
    DOWN = "down"
    BOTH = "both"


class ThresholdOp(StrEnum):
    ABOVE = "above"
    """Breached at or above the threshold."""
    BELOW = "below"
    """Breached at or below the threshold."""


class CustomRule(Contract):
    slug: str = Field(
        description="Identifier unique within the project, e.g. checkout_failures "
        "([a-z][a-z0-9_]*, at most 40 characters)."
    )
    title: str = Field(min_length=1, max_length=80, description="Shown on findings.")
    description: str = Field(
        default="",
        max_length=500,
        description="What the value means and why it matters; shown with findings and given "
        "to the AI explanation.",
    )
    query: str = Field(
        min_length=1,
        max_length=MAX_QUERY_CHARS,
        description="PromQL; the project's label matchers are added to every selector. "
        "$__window is replaced by the rate window (5m).",
    )
    unit: Unit = Unit.COUNT
    mode: CustomRuleMode = CustomRuleMode.ANOMALY
    direction: ChangeDirection = Field(
        default=ChangeDirection.UP, description="Which baseline deviations count (anomaly/both)."
    )
    threshold: float | None = Field(default=None, description="Required in threshold/both mode.")
    critical_threshold: float | None = Field(
        default=None, description="Optional second threshold that raises the severity."
    )
    threshold_op: ThresholdOp = ThresholdOp.ABOVE
    min_duration_minutes: int = Field(
        default=15, ge=5, le=1440, description="How long a threshold breach must last."
    )
    min_change_factor: float | None = Field(
        default=None,
        gt=1,
        description="Baseline deviations must also be at least this factor of the expected "
        "value (default 1.5 when neither this nor min_change is set).",
    )
    min_change: float | None = Field(
        default=None,
        gt=0,
        description="Baseline deviations must also differ from the expected value by at least "
        "this much, in the rule's unit.",
    )
    max_severity: Severity | None = Field(
        default=None,
        description="Upper bound of finding severity; in event mode the severity of every "
        "finding (default high).",
    )
    identity_labels: list[str] = Field(
        default_factory=list,
        max_length=MAX_IDENTITY_LABELS,
        description="Labels that identify one analysed series (e.g. job, instance); empty uses "
        "every label the query returns except the project's matcher labels.",
    )

    @field_validator("slug")
    @classmethod
    def _valid_slug(cls, value: str) -> str:
        if not _SLUG.match(value):
            raise ValueError("must match [a-z][a-z0-9_]* (at most 40 characters)")
        return value

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        if not (stripped := value.strip()):
            raise ValueError("must not be blank")
        return stripped

    @field_validator("description")
    @classmethod
    def _strip_description(cls, value: str) -> str:
        return value.strip()

    @field_validator("query")
    @classmethod
    def _scopable_query(cls, value: str) -> str:
        try:
            scope_template(value)
        except UserQueryError as exc:
            raise ValueError(str(exc)) from None
        return value.strip()

    @field_validator("identity_labels")
    @classmethod
    def _valid_identity(cls, value: list[str]) -> list[str]:
        if bad := [v for v in value if not LABEL_NAME.match(v) or v.startswith("__")]:
            raise ValueError(f"invalid label names: {', '.join(bad)}")
        if len(set(value)) != len(value):
            raise ValueError("duplicate label names")
        return value

    @model_validator(mode="after")
    def _consistent_thresholds(self) -> Self:
        uses_threshold = self.mode in (CustomRuleMode.THRESHOLD, CustomRuleMode.BOTH)
        if uses_threshold and self.threshold is None:
            raise ValueError(f"a threshold is required in {self.mode.value} mode")
        if not uses_threshold and (
            self.threshold is not None or self.critical_threshold is not None
        ):
            raise ValueError(f"thresholds are not used in {self.mode.value} mode")
        if self.critical_threshold is not None and self.threshold is not None:
            above = self.threshold_op is ThresholdOp.ABOVE
            beyond = (
                self.critical_threshold > self.threshold
                if above
                else self.critical_threshold < self.threshold
            )
            if not beyond:
                side = "above" if above else "below"
                raise ValueError(f"the critical threshold must be {side} the threshold")
        return self

    @property
    def signal(self) -> str:
        return f"{CUSTOM_SIGNAL_PREFIX}{self.slug}"


def normalise_custom_rules(rules: list[CustomRule]) -> list[CustomRule]:
    """Reject duplicate slugs; keep the given order (it is the collection order)."""
    slugs = [r.slug for r in rules]
    if duplicates := sorted({s for s in slugs if slugs.count(s) > 1}):
        raise ValueError(f"duplicate rule slugs: {', '.join(duplicates)}")
    return rules


CustomRules = Annotated[
    list[CustomRule],
    Field(max_length=MAX_CUSTOM_RULES),
    AfterValidator(normalise_custom_rules),
]


PREVIEW_MAX_SERIES = 20


class CustomRulePreviewSeries(Contract):
    entity: Entity
    values: list[float | None] = Field(description="The last 24 h on the analysis step grid.")
    latest: float | None = Field(description="Newest observed value.")


class CustomRulePreview(Contract):
    """Result of POST /api/projects/custom-rules/preview: what the rule's query returns."""

    ok: bool = Field(description="False when the query could not be scoped or run.")
    query: str | None = Field(description="The query as run, with the project's matchers.")
    start: UtcDatetime = Field(description="Time of the first value of every series.")
    step_seconds: int
    series_count: int = Field(ge=0, description="Series the query returned (in scope).")
    series: list[CustomRulePreviewSeries] = Field(
        description=f"At most {PREVIEW_MAX_SERIES} series, ordered by identity."
    )
    message: str | None = None
